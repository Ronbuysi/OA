# -*- coding: utf-8 -*-
"""产品库 —— G:\\劲牌产品PNG 的专用浏览页。

按设计稿（800×800 免抠 PNG 商品库）重构，设计要点：

· **左栏 215px 系列树**：顶部系列搜索框，展开箭头 + 文件夹图标（展开主色 / 收起琥珀色），
  选中行实心主色铺满；底部「免抠透底保障」提示卡。
· **中栏卡片矩阵**：严格 1:1 方形容器放缩略图（产品图都是方图），卡片角标标出
  `PNG` / 容量 / 分辨率；悬停浮出「复制 PNG / 打开」快捷动作；底部一条元信息
  （文件名 + 大小 / 日期）。
· **密度**：3–6 列可调（设计稿的 ZoomIn 滑块），换算成格子尺寸而不是缩放图片。
· **透底背景**：棋盘格 / 棚拍淡灰 / 纯白 / 暗调黑 —— 透明 PNG 的观察方式，
  缩略图与检视面板同步切换。
· **右栏检视面板**：1:1 大图 + 一键复制免抠 PNG + 规格表（分辨率 / 格式 / Alpha 校验 /
  大小 / 更新日期）+ 资产标签。默认收起，右上角开关展开。
· 缩略图持久缓存（原图最大 4480×6720），后台渲染 + 分批渐进加载。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (
    QEvent, QObject, QPoint, QRect, QRunnable, QSize, Qt, QThreadPool, QTimer, QUrl, Signal, Slot,
)
from PySide6.QtGui import (
    QBrush, QColor, QFont, QFontMetrics, QIcon, QImage, QImageReader, QKeySequence,
    QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QShortcut,
)
import shutil
import time

from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame,
    QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLayout, QLineEdit, QListWidget,
    QListWidgetItem, QMenu, QMessageBox, QPushButton, QSizePolicy, QSlider, QStyle,
    QStyledItemDelegate, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

from .. import organizer
from ..config import user_known_folders
from ..preview import open_file, preview_cache_dir, reveal_file
from .empty_state import EmptyStatePanel
from .icons import icon as painted_icon
from .layout import page_body
from .product_rail import GROUP_ORDER, ProductRail
from .wheel import scroll_by_wheel
from . import styles as S

ROOT_DEFAULT = r"G:\劲牌产品PNG"

# Qt 默认单张图解码上限 256MB。库里有两张 1.1 亿像素级的大图（解码后 450~610MB），
# 不放开的话连缩略图都读不出来（只会留一张 0×0 的失败图）。
QImageReader.setAllocationLimit(1024)

# 超过这个像素数的大图走「串行池」，避免几张大图同时解码把内存打满
GIANT_PIXELS = 40_000_000
DEFAULT_COLUMNS = 5
COLUMN_MIN, COLUMN_MAX = 3, 6
MIN_CARD_WIDTH = 164

CARD_MARGIN = 3          # 卡片外留白（格子间距的一半）
CARD_RADIUS = 12
META_HEIGHT = 46         # 卡片底部元信息条
THUMB_PAD = 4            # 缩略图与卡片内缘的间距
THUMB_RENDER = (360, 360)   # 缩略图渲染尺寸（缓存用）
HI_RENDER = (1100, 1100)    # 检视面板大图
THUMBNAIL_BATCH = 120
# 滚动时除了可见区域，再多往下预加载多少行（约两屏），避免滑到跟前才开始解码
THUMB_LOOKAHEAD_ROWS = 120

# 透底背景：(键, 界面标签)
BACKDROPS = (
    ("checker", "棋盘"),
    ("studio", "淡灰"),
    ("white", "纯白"),
    ("dark", "暗调"),
)

KIND_ALL, KIND_BOTTLE, KIND_SET = "all", "bottle", "set"
KIND_FILTERS = ((KIND_ALL, "全部"), (KIND_BOTTLE, "单瓶"), (KIND_SET, "礼袋/套装"))
SET_KEYWORDS = ("礼袋", "礼盒", "组合", "套装", "扎袋", "双瓶", "多瓶", "家族", "阵列", "箱装")

# 容量：既要显示（5L / 125ml×6），也要归一化成毫升好按容量分类。
# ⚠ 判断单位别用「标签里含不含 l/L」—— "小写 ml" 里也有 l，我就这么写错过一次，
# 结果把 500ml 乘了 1000 变成 500000ml。直接按 (倍率, 正则) 配对。
_CAP_UNITS: tuple[tuple[float, re.Pattern], ...] = (
    (1.0, re.compile(r"(\d{2,4})\s*(?:ml|ML|毫升)")),
    (1000.0, re.compile(r"(\d+(?:\.\d+)?)\s*(?:L|l(?![a-zA-Z])|升)")),
)
_MULTI_RE = re.compile(r"[×xX*]\s*(\d+)")
_DEG_RE = re.compile(r"(\d{2})\s*(?:度|%vol|%VOL|vol)", re.IGNORECASE)

# 系列名 → 四个大分类（用户定的口径）。顺序即优先级，先命中先归组。
# 「品牌素材」放最前，否则「标志与素材」会被后面的关键词抢走；
# 「白酒」放在「保健酒」前面，是为了让毛铺草本这类带「草本」字样的归到白酒。
GROUP_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("品牌素材", ("标志", "logo", "LOGO", "Logo", "VI", "vi", "视觉规范", "品牌", "吉祥物",
                 "酒具", "赠品", "周边", "插画", "材质", "模板", "背景", "贴片", "物料",
                 "icon", "字体")),
    ("白酒系列", ("毛铺", "荞酒", "苦荞", "金荞", "紫荞", "黑荞", "玉荞", "清香年代",
                 "酱酒", "酱香", "匠普", "和露", "大蒜酒", "封藏", "原浆", "老酒", "陈香",
                 "白酒", "年份酒")),
    ("保健酒系列", ("劲酒", "参茸", "茸", "人参", "枸杞", "石斛", "铁皮", "苁蓉", "黄精",
                   "灵芝", "虫草", "追风", "八珍", "十全", "养生", "保健", "草本",
                   "配制酒", "滋补", "持正堂")),
)
FALLBACK_BRAND = "其他素材"


_CUSTOM_SERIES_GROUPS: dict[str, str] = {}

STARTER_SERIES: tuple[str, ...] = (
    "中国劲酒", "毛铺苦荞酒", "养生一号", "参茸劲酒", "标志与素材",
)


def set_custom_series_group(series: str, brand: str | None) -> None:
    """设置自定义系列名到大类的映射（持久化在 settings 表 custom_series_groups 中）。"""
    key = (series or "").strip()
    if not key:
        return
    if brand and brand in GROUP_ORDER:
        _CUSTOM_SERIES_GROUPS[key] = brand
    else:
        _CUSTOM_SERIES_GROUPS.pop(key, None)


def load_custom_series_groups(raw_json: str) -> dict[str, str]:
    """从 JSON 字符串加载用户自定义的系列大类映射。"""
    _CUSTOM_SERIES_GROUPS.clear()
    if not raw_json:
        return _CUSTOM_SERIES_GROUPS
    try:
        data = json.loads(raw_json)
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(k, str) and v in GROUP_ORDER:
                    _CUSTOM_SERIES_GROUPS[k.strip()] = v
    except Exception:
        pass
    return _CUSTOM_SERIES_GROUPS


def dump_custom_series_groups() -> str:
    return json.dumps(_CUSTOM_SERIES_GROUPS, ensure_ascii=False)


def group_for_series(name: str) -> str:
    """把系列 / 月份 / 目录名归到四个大分类之一（优先匹配用户自定义系列归属）。"""
    clean = (name or "").strip()
    if clean in _CUSTOM_SERIES_GROUPS:
        return _CUSTOM_SERIES_GROUPS[clean]
    for custom_key, custom_brand in _CUSTOM_SERIES_GROUPS.items():
        if custom_key and custom_key in clean:
            return custom_brand
    for brand, keywords in GROUP_RULES:
        if any(keyword in clean for keyword in keywords):
            return brand
    return FALLBACK_BRAND


def parse_capacity_ml(name: str) -> int | None:
    """从文件名读出**单瓶容量**（毫升）；读不出返回 None。

    支持 `125ml` / `500ML` / `2.5L` / `1.5升` 这些写法。
    注意 `中国劲酒520礼盒` 里的 520 是产品名不是容量 —— 只有跟着 ml/L 的数字才算。
    """
    for factor, pattern in _CAP_UNITS:
        hit = pattern.search(name)
        if hit:
            return int(round(float(hit.group(1)) * factor))
    return None


def format_capacity_ml(ml: int | None) -> str:
    """毫升 → 展示写法：125ml / 2.5L。"""
    if not ml:
        return ""
    if ml >= 1000:
        text = f"{ml / 1000:.2f}".rstrip("0").rstrip(".")
        return f"{text}L"
    return f"{ml}ml"


def parse_capacity(name: str) -> str:
    """卡片上显示的容量：单瓶容量 + 可选的瓶数，如 `125ml×6` / `2.5L`。"""
    ml = parse_capacity_ml(name)
    if ml is None:
        return ""
    base = format_capacity_ml(ml)
    multi = _MULTI_RE.search(name)
    return f"{base}×{int(multi.group(1))}" if multi else base


# 容量档位：按实际规格体系分三档，剩下读不出容量的单列一组
CAPACITY_UNKNOWN = "未标注容量"
CAPACITY_BANDS: tuple[str, ...] = (
    "小规格（≤100ml）", "常规装（100ml~1L）", "大规格（≥1L）", CAPACITY_UNKNOWN,
)
CAPACITY_BAND_COLORS: dict[str, str] = {
    "小规格（≤100ml）": "emerald",
    "常规装（100ml~1L）": "blue",
    "大规格（≥1L）": "amber",
    CAPACITY_UNKNOWN: "rose",
}


def capacity_band(ml: int | None) -> str:
    if ml is None:
        return CAPACITY_UNKNOWN
    if ml <= 100:
        return "小规格（≤100ml）"
    if ml < 1000:
        return "常规装（100ml~1L）"
    return "大规格（≥1L）"


def parse_degrees(name: str) -> str:
    hit = _DEG_RE.search(name)
    return f"{int(hit.group(1))}度" if hit else ""


def kind_of(name: str) -> str:
    return KIND_SET if any(word in name for word in SET_KEYWORDS) else KIND_BOTTLE


@dataclass(frozen=True)
class ProductShot:
    """一张产品图。"""

    path: str
    name: str
    series: str
    brand: str
    size: int = 0
    mtime: float = 0.0
    width: int = 0
    height: int = 0
    has_alpha: bool = False
    capacity_ml: int | None = None      # 单瓶容量（毫升），文件名读不出就是 None

    def with_alpha(self, value: bool) -> None:
        """像素核查结果回写：实例是 frozen 的，这是唯一允许后期改的字段。

        用 object.__setattr__ 而不是 dataclasses.replace，是为了保持对象身份不变 ——
        同一张图同时挂在 _shots 列表和 _by_path 字典里，替换成新对象会让两边不同步。
        """
        object.__setattr__(self, "has_alpha", value)

    @property
    def capacity_label(self) -> str:
        """规范写法：50ml / 2.5L。"""
        return format_capacity_ml(self.capacity_ml)

    @property
    def capacity_band(self) -> str:
        """容量档位：小规格 / 常规装 / 大规格 / 未标注容量。"""
        return capacity_band(self.capacity_ml)

    @property
    def format_label(self) -> str:
        """真实格式（卡片角标以前写死成 PNG，jpg 也标 PNG，是错的）。"""
        suffix = Path(self.name).suffix.upper().lstrip(".")
        return "JPG" if suffix in ("JPEG", "JPG") else (suffix or "IMG")

    @property
    def capacity(self) -> str:
        return parse_capacity(self.name)

    @property
    def degrees(self) -> str:
        return parse_degrees(self.name)

    @property
    def kind(self) -> str:
        return kind_of(self.name)

    @property
    def is_square_800(self) -> bool:
        return self.width == 800 and self.height == 800

    @property
    def resolution(self) -> str:
        return f"{self.width} × {self.height}" if self.width and self.height else "—"


IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")

# ── 多来源（自适应本机与他机环境） ──
WECOM_ACCOUNTS = ("1688856994683758", "1688854287702806", "1688856335855108",
                  "1688856975763172", "2251800399854470")
WECOM_ROOT = Path("F:/WXWork")
ARCHIVE_ROOT = Path("G:/")
_WECOM_ACCOUNT_RE = re.compile(r"^\d{8,24}$")

# 企微缓存里这些不是产品素材：聊天截图、头像、以及没有原始文件名的（UUID / 纯 hex）重编码图
_WECOM_NOISE = re.compile(r"^(企业微信截图|微信截图|Screenshot|IMG_|mmexport|WechatIMG)", re.IGNORECASE)
_WECOM_HASHED = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-|^[0-9a-f]{16,}", re.IGNORECASE)
# 兼顾酒水品类与通用电商视觉词库（让其他品类设计师的企微收图也能自动识别）
_PRODUCT_WORDS = ("酒", "劲", "毛铺", "产品", "商品", "主图", "详情", "海报", "包装", "瓶", "礼盒",
                  "透明", "免抠", "白底", "精修", "渲染", "抠图", "模特", "样机", "直通车",
                  "电商", "促销", "物料", "标签", "礼装", "珍藏", "限定", "sku", "kv", "banner")
_NOISE_WORDS = ("二维码", "福利群", "微信群", "群码", "扫码")
_COPY_SUFFIX = re.compile(r"(\s*\(\d+\))+$")
_HAS_CJK = re.compile(r"[\u4e00-\u9fff]")


def normalize_stem(name: str) -> str:
    """去掉「(1)(2)」这类转发副本后缀，用于去重。"""
    stem = Path(name).stem
    while True:
        stripped = _COPY_SUFFIX.sub("", stem)
        if stripped == stem:
            return stem.strip()
        stem = stripped


@dataclass(frozen=True)
class Source:
    """一个素材来源。"""

    key: str
    label: str
    hint: str


SOURCES: tuple[Source, ...] = (
    Source("library", "产品库（免抠 PNG）",
           "按品牌 / 系列归档的免抠产品图库"),
    Source("wecom", "企业微信收图",
           "企业微信图片缓存 —— 同事发来的主图 / 详情 / 礼盒图，按月份和品牌归组"),
    Source("archive", "主图归档汇总",
           "归档盘各月「归档汇总」里的主图 / SKU / 产品图目录"),
)


def time_sort_key(shot: ProductShot) -> tuple[float, str]:
    """排序规则：**时间新的放上面**。

    `mtime` 取负让新的排前面；同一时间的按文件名兜底，保证顺序稳定
    （否则同批复制的图每次刷新可能换位置）。

    以前这里排的是「800×800 → 接近方形 → 长条最后」（想让首屏先出产品图），
    用户明确要求按时间，就换掉了。
    """
    return (-shot.mtime, shot.name)


def _dedupe_rank(shot: ProductShot) -> tuple[int, float]:
    """同一张图有多份时怎么挑：先要「名字没被转发后缀污染」的，再要最新的。"""
    has_suffix = bool(_COPY_SUFFIX.search(Path(shot.name).stem))
    return (1 if has_suffix else 0, -shot.mtime)


def discover_wecom_base_dirs(custom_root: str | Path | None = None) -> list[Path]:
    """跨机器自动探测企业微信数据根目录（WXWork）：
    优先级：用户手动设置 -> Windows 注册表 DataLocation -> F:/WXWork -> 文档/WXWork -> 各盘符根目录/WXWork。"""
    candidates: list[Path] = []
    if custom_root:
        candidates.append(Path(custom_root))
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Tencent\WXWork") as key:
                val, _ = winreg.QueryValueEx(key, "DataLocation")
                if val:
                    p = Path(os.path.expandvars(str(val)))
                    candidates.append(p / "WXWork" if p.name.lower() != "wxwork" and (p / "WXWork").is_dir() else p)
        except Exception:
            pass
    candidates.append(WECOM_ROOT)
    docs = user_known_folders().get("Documents", Path.home() / "Documents")
    candidates.append(docs / "WXWork")
    for drive in ("D", "E", "F", "G", "C"):
        candidates.append(Path(f"{drive}:/WXWork"))

    found: list[Path] = []
    seen: set[str] = set()
    for c in candidates:
        try:
            if c.is_dir():
                k = str(c.resolve()).lower()
                if k not in seen:
                    seen.add(k)
                    found.append(c)
        except OSError:
            continue
    return found


def wecom_account_dirs(custom_root: str | Path | None = None) -> list[Path]:
    """扫描所有企业微信账号目录（自动适配任意同事的企微账号，无需硬编码账号 ID）。"""
    accounts: list[Path] = []
    seen: set[str] = set()
    for wx_root in discover_wecom_base_dirs(custom_root):
        # 如果用户直接选到了某个账号目录甚至 Cache 目录，向上或直接兼容
        if (wx_root / "Cache").is_dir():
            accounts.append(wx_root)
            continue
        try:
            for entry in sorted(wx_root.iterdir(), key=lambda p: p.name):
                if not entry.is_dir():
                    continue
                if (_WECOM_ACCOUNT_RE.match(entry.name)
                        or entry.name in WECOM_ACCOUNTS
                        or (entry / "Cache" / "Image").is_dir()
                        or (entry / "Cache" / "File").is_dir()):
                    k = str(entry.resolve()).lower()
                    if k not in seen:
                        seen.add(k)
                        accounts.append(entry)
        except OSError:
            continue
    return accounts


def wecom_image_roots(custom_root: str | Path | None = None) -> list[Path]:
    """企业微信的图片缓存目录（按账号 + 月份）。"""
    roots: list[Path] = []
    for acc_dir in wecom_account_dirs(custom_root):
        base = acc_dir / "Cache" / "Image"
        if base.is_dir():
            roots.append(base)
    return roots


def wecom_file_roots(custom_root: str | Path | None = None) -> list[Path]:
    """企业微信的文件缓存目录（Cache/File）。"""
    roots: list[Path] = []
    for acc_dir in wecom_account_dirs(custom_root):
        base = acc_dir / "Cache" / "File"
        if base.is_dir():
            roots.append(base)
    return roots


def archive_image_roots(archive_root: str | Path | None = None) -> list[Path]:
    """归档盘各月「归档汇总」下含主图 / SKU / 产品字样的目录（跟随设置页的归档位置）。"""
    roots: list[Path] = []
    base_root = Path(archive_root) if archive_root else organizer.DEST_DRIVE
    if not base_root.is_dir() and ARCHIVE_ROOT.is_dir():
        base_root = ARCHIVE_ROOT
    if not base_root.is_dir():
        return roots
    try:
        month_dirs = sorted(base_root.glob("2*年*月文件"))
    except OSError:
        return roots
    for month_dir in month_dirs:
        archive = month_dir / "归档汇总"
        if not archive.is_dir():
            continue
        for current, _dirs, _files in os.walk(archive):
            name = os.path.basename(current)
            if any(word in name for word in ("主图", "SKU", "sku", "产品", "详情", "PNG")):
                roots.append(Path(current))
    return roots


def _image_size(path: Path) -> tuple[int, int]:
    try:
        reader = QImageReader(str(path))
        dims = reader.size()
        if dims.isValid():
            return dims.width(), dims.height()
    except Exception:  # noqa: BLE001
        pass
    return 0, 0


def probe_alpha(path: Path) -> bool:
    """只看文件头判断有没有 Alpha 通道 —— 不用解码，几万个文件也能秒扫。

    产品图是透明底 PNG，而详情页 / 场景图 / 海报是无透明的 JPG 或 RGB PNG。
    按「有没有 Alpha 通道」筛，正好把非产品图挡在外面。
    """
    try:
        with open(path, "rb") as handle:
            head = handle.read(4096)
    except OSError:
        return False
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        if len(head) < 26:
            return False
        color_type = head[25]        # IHDR 里的颜色类型
        if color_type in (4, 6):     # 灰度+Alpha / RGBA
            return True
        if color_type == 3:          # 调色板图：靠 tRNS 块才有透明
            return b"tRNS" in head
        return False
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        if b"ALPH" in head:
            return True
        return b"VP8X" in head and len(head) > 20 and bool(head[20] & 0x10)
    return False                       # JPEG 等一律无 Alpha


# ── 透明底的「像素级」判定 ──
#
# `probe_alpha()` 只看文件头，只能答「有没有 Alpha 通道」。可主图 / 活动图 / 详情页
# 也都是带 Alpha 的 PNG（Alpha 通道只在四周几个像素上）—— 于是它们混进了产品库：
# 用户看到的 `260420中国劲酒主图6.png`、`替换陈皮酒为十全酒….png` 就是这么来的。
# 真正的透明底免抠图要看**像素**：四周一圈基本全透明、内容不铺满画布、内容也不算少。
#
# 判据只留「铁证」级别，宁可漏判也不误杀 —— 第一版用「内容填充率高就是活动图」，
# 结果把礼盒外盒 / 提袋也判掉了（方盒子填充率本来就高），那些是真产品。
PIXEL_EMPTY_CONTENT = 0.005    # 实心内容低于此 → 空白画布
PIXEL_EDGE_FULL = 0.90         # 边缘圈不透明高于此 → 满幅图（主图 / 详情 / 活动）
PIXEL_BBOX_FULL = 0.85         # 内容外接框占画布高于此 → 铺满画布（活动图 / 贴图）
PIXEL_TINY_CONTENT = 0.05      # 实心内容低于此且形状规则 → 链接占位图
PIXEL_FILL_RECT = 0.90         # 外接框填充率高于此 → 形状接近一整块矩形

_SOLID_TABLE = bytes((255 if value > 128 else 0) for value in range(256))
_ANY_TABLE = bytes((1 if value > 16 else 0) for value in range(256))


def analyze_alpha(path: Path, max_side: int = 256) -> tuple[float, float, float] | None:
    """缩略解码后统计透明特征 → (实心占比, 边缘不透明比, 外接框填充率)。

    字节运算全走 C 实现（`bytes.translate` + `sum`），单张几毫秒，不必引入 numpy。
    """
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    original = reader.size()
    if not original.isValid():
        return None
    scale = max_side / max(original.width(), original.height())
    if scale < 1:
        reader.setScaledSize(QSize(max(1, int(original.width() * scale)),
                                   max(1, int(original.height() * scale))))
    image = reader.read()
    if image.isNull():
        return None
    image = image.convertToFormat(QImage.Format.Format_ARGB32)
    width, height = image.width(), image.height()
    if width < 8 or height < 8:
        return None
    raw = bytes(image.constBits())
    stride = image.bytesPerLine()
    if stride != width * 4:                     # 有行尾填充时按行剥掉
        raw = b"".join(raw[y * stride:y * stride + width * 4] for y in range(height))
    total = width * height
    alphas = raw[3::4][:total]                  # BGRA 的第 4 个字节
    if len(alphas) < total:
        return None
    solid = alphas.translate(_SOLID_TABLE)      # 0 / 255：看得见的内容
    anypix = alphas.translate(_ANY_TABLE)       # 0 / 1：有一点点也算
    solid_ratio = sum(solid) / 255 / total
    border = (anypix[:2 * width] + anypix[-2 * width:]
              + anypix[0::width] + anypix[1::width]
              + anypix[width - 2::width] + anypix[width - 1::width])
    edge = (sum(border) / len(border)) if border else 0.0
    rows = [y for y in range(height) if max(solid[y * width:(y + 1) * width])]
    cols = [x for x in range(width) if max(solid[x::width])]
    fill = 0.0
    if rows and cols:
        y0, y1, x0, x1 = rows[0], rows[-1], cols[0], cols[-1]
        # 注意 sum() 不能直接加 bytes —— 先把整框拼起来再一次求和
        band = b"".join(solid[y * width + x0:y * width + x1 + 1] for y in range(y0, y1 + 1))
        inside = sum(band)
        area = (y1 - y0 + 1) * (x1 - x0 + 1)
        fill = inside / 255 / area if area else 0.0
    return solid_ratio, edge, fill


def judge_transparent(stats: tuple[float, float, float] | None) -> bool:
    """这图是不是「透明底免抠产品图」。"""
    if stats is None:
        return False
    solid, edge, fill = stats
    if solid < PIXEL_EMPTY_CONTENT:
        return False                            # 空白画布
    if edge > PIXEL_EDGE_FULL:
        return False                            # 四周几乎全不透明 → 满幅主图 / 详情 / 活动
    bbox = (solid / fill) if fill > 0 else 0.0
    if bbox > PIXEL_BBOX_FULL and fill > PIXEL_FILL_RECT:
        return False                            # 内容铺满整张画布 → 活动图 / 满幅贴图
    if solid < PIXEL_TINY_CONTENT and fill > PIXEL_FILL_RECT:
        return False                            # 极小的一块规则内容 → 链接占位图
    return True


class PixelVerifier:
    """像素核查结果的磁盘缓存（路径 → 是否合格），带线程锁与内存缓存同步。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._cache: dict[str, list] = {}
        self._fresh: dict[str, list] = {}
        self.pending: list[tuple[Path, int, int]] = []   # (路径, 大小, mtime_ns)
        path = _pixel_cache_path()
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self._cache = raw
                    self._fresh = dict(raw)
            except Exception:  # noqa: BLE001 - 缓存坏了就重算
                self._cache = {}

    def verdict(self, path: Path, stat) -> bool | None:
        """返回 True/False 表示已核查过；None 表示还没查（并进待查队列）。"""
        key = str(path)
        stamp = int(stat.st_mtime_ns)
        with self._lock:
            entry = self._cache.get(key) or self._fresh.get(key)
            if entry and len(entry) >= 3 and entry[0] == stat.st_size and entry[1] == stamp:
                self._fresh[key] = entry
                self._cache[key] = entry
                return bool(entry[2])
            self.pending.append((path, stat.st_size, stamp))
            return None

    def record(self, path: Path, size: int, stamp: int, ok: bool) -> None:
        entry = [size, stamp, 1 if ok else 0]
        key = str(path)
        with self._lock:
            self._fresh[key] = entry
            self._cache[key] = entry

    def take_pending(self) -> list[tuple[Path, int, int]]:
        with self._lock:
            items = list(self.pending)
            self.pending.clear()
            return items

    def commit(self) -> None:
        path = _pixel_cache_path()
        try:
            with self._lock:
                payload = json.dumps(self._fresh)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(payload, encoding="utf-8")
        except OSError:
            pass


def _pixel_cache_path() -> Path:
    return preview_cache_dir().parent / "index" / "library_pixel_verify.json"


_pixel_verifier: PixelVerifier | None = None


def pixel_verifier() -> PixelVerifier:
    """进程内共用一份（主线程串行使用，无跨线程问题）。"""
    global _pixel_verifier
    if _pixel_verifier is None:
        _pixel_verifier = PixelVerifier()
    return _pixel_verifier


def _probe_cache_path(key: str) -> Path:
    return preview_cache_dir().parent / "index" / f"product_index_{key}.json"


class SizeProbe:
    """图片头尺寸探测 + 磁盘缓存。

    读像素尺寸要打开每个文件，企微缓存里有 2.4 万个文件、候选几千张，
    全量探测一次要一分半。缓存住「路径 → (大小, 修改时间, 宽, 高)」之后，
    再扫描只需要探测新增和改动过的文件（几秒）。
    """

    def __init__(self, key: str):
        self.key = key
        self._cache: dict[str, list[int]] = {}
        self._fresh: dict[str, list[int]] = {}
        path = _probe_cache_path(key)
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self._cache = raw
            except Exception:  # noqa: BLE001 - 缓存坏了就重扫，不是错误
                self._cache = {}

    def info_of(self, path: Path, stat) -> tuple[int, int, bool]:
        """返回 (宽, 高, 是否有 Alpha 通道)；命中缓存就不碰文件。"""
        key = str(path)
        stamp = int(stat.st_mtime_ns)
        entry = self._cache.get(key)
        if entry and len(entry) >= 5 and entry[0] == stat.st_size and entry[1] == stamp:
            self._fresh[key] = entry
            return entry[2], entry[3], bool(entry[4])
        width, height = _image_size(path)
        alpha = probe_alpha(path)
        self._fresh[key] = [stat.st_size, stamp, width, height, 1 if alpha else 0]
        return width, height, alpha

    def commit(self):
        path = _probe_cache_path(self.key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(self._fresh), encoding="utf-8")
        except OSError:
            pass  # 写不了就算了，下次再探测一遍而已


def build_index(root: Path) -> list[ProductShot]:
    """扫描产品库根目录，产出索引（顺带用图片头读真实像素尺寸）。"""
    shots: list[ProductShot] = []
    if not root.is_dir():
        return shots
    probe = SizeProbe("library")
    pixels = pixel_verifier()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        series = path.parent.relative_to(root).parts[0] if path.parent != root else "（根目录）"
        try:
            stat = path.stat()
        except OSError:
            continue  # stat 失败（权限/文件刚被删）就整条跳过，别带着未绑定的 stat 往下走
        size, mtime = stat.st_size, stat.st_mtime
        width, height, alpha = probe.info_of(path, stat)
        # 做过像素核查的以核查结果为准（文件头会把满幅主图误判成透明底）；
        # 没查过的先用文件头结果顶着，稍后由页面分批补查。
        verified = pixels.verdict(path, stat)
        if verified is not None:
            alpha = verified
        shots.append(ProductShot(
            path=str(path), name=path.name, series=series,
            brand=group_for_series(series), size=size, mtime=mtime,
            width=width, height=height, has_alpha=alpha, capacity_ml=parse_capacity_ml(path.name)))
    probe.commit()
    pixels.commit()
    return sorted(shots, key=time_sort_key)


def build_wecom_index(roots: list[Path] | None = None,
                      extra_roots: list[Path] | None = None,
                      custom_root: str | Path | None = None) -> list[ProductShot]:
    """企业微信收图：过滤聊天截图 / 头像 / 无意义文件名，同名副本去重。

    分组：品牌（按文件名关键词）+ 月份（缓存目录名）。
    """
    roots = roots if roots is not None else wecom_image_roots(custom_root)
    extra_roots = extra_roots if extra_roots is not None else wecom_file_roots(custom_root)
    seen: dict[tuple[str, int], ProductShot] = {}
    probe = SizeProbe("wecom")

    def _consider(path: Path, group: str, extra_text: str) -> None:
        name = path.name
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            return
        if _WECOM_NOISE.match(name) or _WECOM_HASHED.match(name):
            return
        if any(word in name for word in _NOISE_WORDS):
            return
        # 名字必须像产品素材：只有中文名还不够（聊天文件里什么都有）
        if not any(word in name.lower() for word in _PRODUCT_WORDS):
            return
        try:
            stat = path.stat()
        except OSError:
            return
        if stat.st_size < 4096:      # 表情、图标、缩略碎片
            return
        width, height, alpha = probe.info_of(path, stat)
        if max(width, height) < 200:  # 小头像 / 小图标
            return
        key = (normalize_stem(name).lower(), stat.st_size)
        shot = ProductShot(
            path=str(path), name=name, series=group,
            brand=group_for_series(f"{extra_text} {name}"), size=stat.st_size,
            mtime=stat.st_mtime, width=width, height=height, has_alpha=alpha,
            capacity_ml=parse_capacity_ml(name))
        previous = seen.get(key)
        if previous is None or _dedupe_rank(shot) < _dedupe_rank(previous):
            seen[key] = shot

    for base in roots:
        try:
            months = sorted(os.listdir(base))
        except OSError:
            continue
        for month in months:
            folder = base / month
            if not folder.is_dir():
                continue
            for entry in folder.iterdir():
                if entry.is_file():
                    _consider(entry, month, "")
    for base in extra_roots:
        if not base.is_dir():
            continue
        for entry in base.rglob("*"):
            if entry.is_file():
                _consider(entry, "收到的文件", "")
    probe.commit()
    return sorted(seen.values(), key=time_sort_key)


def build_archive_index(roots: list[Path] | None = None) -> list[ProductShot]:
    """G 盘归档里成批的主图 / SKU 目录。分组：品牌 + 目录名。"""
    roots = roots if roots is not None else archive_image_roots()
    seen: dict[tuple[str, int], ProductShot] = {}
    probe = SizeProbe("archive")
    for base in roots:
        group = base.name
        for entry in sorted(base.rglob("*")):
            if not entry.is_file() or entry.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            try:
                stat = entry.stat()
            except OSError:
                continue
            if stat.st_size < 4096:
                continue
            width, height, alpha = probe.info_of(entry, stat)
            if max(width, height) < 200:
                continue
            key = (normalize_stem(entry.name).lower(), stat.st_size)
            shot = ProductShot(
                path=str(entry), name=entry.name, series=group,
                brand=group_for_series(f"{base.parent.name} {group} {entry.name}"),
                size=stat.st_size, mtime=stat.st_mtime,
                width=width, height=height, has_alpha=alpha,
                capacity_ml=parse_capacity_ml(entry.name))
            previous = seen.get(key)
            if previous is None or _dedupe_rank(shot) < _dedupe_rank(previous):
                seen[key] = shot
    probe.commit()
    return sorted(seen.values(), key=time_sort_key)


def build_source_index(
    key: str,
    library_root: Path | str | None = None,
    wecom_root: Path | str | None = None,
    archive_root: Path | str | None = None,
) -> list[ProductShot]:
    """按来源产出索引（扫描放在后台线程里跑，支持跨机器自定义目录）。"""
    if key == "wecom":
        return build_wecom_index(custom_root=wecom_root)
    if key == "archive":
        return build_archive_index(archive_image_roots(archive_root))
    target = Path(library_root) if library_root else Path(ROOT_DEFAULT)
    return build_index(target)


def thumb_cache_path(source: Path, tag: str = "prod") -> Path:
    """缩略图缓存名。tag 区分尺寸/用途（网格缩略图 / 检视大图），别互相覆盖。"""
    stat = source.stat()
    key = f"{source.resolve()}:{stat.st_size}:{stat.st_mtime_ns}:{tag}".encode("utf-8")
    return preview_cache_dir() / f"{tag}_{hashlib.sha256(key).hexdigest()}.png"


def checker_tile(dark: bool) -> QPixmap:
    """棋盘格衬底：透明 PNG 的标准呈现方式，随主题换深浅。"""
    size = 8
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = _new_painter(pixmap)
    base = "#1A1E27" if dark else "#FFFFFF"
    alt = "#232936" if dark else "#E7EAF0"
    for (x, y), color in (((0, 0), base), ((1, 0), alt), ((0, 1), alt), ((1, 1), base)):
        painter.fillRect(x * size, y * size, size, size, color)
    painter.end()
    return pixmap


def paint_backdrop(painter: QPainter, rect: QRect, mode: str, dark: bool,
                   checker: QPixmap) -> None:
    """在 rect 内铺透底背景。缩略图和检视面板共用一套，保证观感一致。"""
    if mode == "checker":
        painter.fillRect(rect, QBrush(checker))
    elif mode == "studio":
        gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        if dark:
            gradient.setColorAt(0.0, QColor("#2A2F3A"))
            gradient.setColorAt(1.0, QColor("#1C2027"))
        else:
            gradient.setColorAt(0.0, QColor("#F5F6F8"))
            gradient.setColorAt(1.0, QColor("#E4E7EC"))
        painter.fillRect(rect, QBrush(gradient))
    elif mode == "white":
        painter.fillRect(rect, QColor("#FFFFFF"))
    else:  # dark
        painter.fillRect(rect, QColor("#181A20"))


def _new_painter(pixmap) -> QPainter:
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    return painter


class ThumbWorker(QRunnable):
    """渲染一张图（带持久缓存）。

    QImage 跨线程安全，QPixmap 只能在界面线程碰（会段错误，实测踩过）。
    should_run 在开工前检查，换了系列/搜索就立刻放弃，不做无用功。
    """

    class _Signals(QObject):
        done = Signal(object, str, object)  # (token, 路径, QImage | None)

    def __init__(self, path: str, token=None, should_run=None,
                 box: tuple[int, int] = THUMB_RENDER, tag: str = "prod"):
        super().__init__()
        self.path = path
        self.token = token
        self.should_run = should_run
        self.box = box
        self.tag = tag
        self.signals = self._Signals()

    @Slot()
    def run(self):
        if self.should_run is not None and not self.should_run():
            return
        try:
            image = self._render()
        except Exception:  # noqa: BLE001
            image = None
        try:
            self.signals.done.emit(self.token, self.path, image)
        except RuntimeError:
            pass

    def _render(self):
        source = Path(self.path)
        target = thumb_cache_path(source, self.tag)
        if target.exists():
            cached = QImage(str(target))
            if not cached.isNull():
                return _fit(cached, self.box)
        reader = QImageReader(str(source))
        reader.setAutoTransform(True)
        original = reader.size()
        if original.isValid() and original.width() and original.height():
            scale = min(self.box[0] / original.width(), self.box[1] / original.height(), 1.0)
            if scale < 1.0:
                reader.setScaledSize(QSize(
                    max(1, round(original.width() * scale)),
                    max(1, round(original.height() * scale))))
        image = reader.read()
        if image.isNull():
            return None
        image = _fit(image, self.box)
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(str(target))  # PNG 保留透明通道
        return image


class AlphaWorker(QRunnable):
    """后台跑 Alpha 校验（巨图解码慢，绝不能放界面线程）。"""

    class _Signals(QObject):
        done = Signal(object, str, object)  # (token, 路径, (格式, 说明, 是否合格))

    def __init__(self, path: str, token=None, should_run=None):
        super().__init__()
        self.path = path
        self.token = token
        self.should_run = should_run
        self.signals = self._Signals()

    @Slot()
    def run(self):
        if self.should_run is not None and not self.should_run():
            return
        try:
            payload = describe_alpha(self.path)
        except Exception:  # noqa: BLE001
            payload = ("—", "无法读取", False)
        try:
            self.signals.done.emit(self.token, self.path, payload)
        except RuntimeError:
            pass


def _fit(image: QImage, box: tuple[int, int]) -> QImage:
    return image.scaled(box[0], box[1], Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)


def describe_alpha(path: str) -> tuple[str, str, bool]:
    """彩样校验 Alpha 通道 —— 返回 (格式文本, 透明度说明, 是否合格)。

    只读一张 256px 的缩略来判，不解全图（原图能到 4480×6720）。
    """
    try:
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        size = reader.size()
        if size.isValid() and size.width() and size.height():
            scale = min(256 / size.width(), 256 / size.height(), 1.0)
            if scale < 1.0:
                reader.setScaledSize(QSize(max(1, round(size.width() * scale)),
                                           max(1, round(size.height() * scale))))
        image = reader.read()
    except Exception:  # noqa: BLE001
        return "—", "无法读取", False
    if image.isNull():
        return "—", "无法读取", False
    if not image.hasAlphaChannel():
        return "24-bit PNG (RGB)", "无 Alpha 通道（不透底）", False
    minimum = 255
    for y in range(0, image.height(), 3):
        for x in range(0, image.width(), 3):
            alpha = image.pixelColor(x, y).alpha()
            if alpha < minimum:
                minimum = alpha
                if minimum == 0:
                    break
        if minimum == 0:
            break
    if minimum == 0:
        return "32-bit PNG (RGBA)", "透明度 100% 校验通过", True
    return "32-bit PNG (RGBA)", f"存在半透明像素（最低 {minimum}）", minimum < 250


def asset_tags(shot: ProductShot, alpha_ok: bool | None = None) -> list[str]:
    """资产标签：从真实信息里提，不编造。"""
    tags: list[str] = []
    if shot.is_square_800:
        tags.append("800×800")
    if shot.capacity:
        tags.append(shot.capacity)
    if shot.degrees:
        tags.append(shot.degrees)
    if shot.kind == KIND_SET:
        tags.append("礼袋套装")
    else:
        tags.append("单瓶")
    name = shot.name
    for keyword, label in (("红印", "红印"), ("金标", "金标盖"), ("蓝标", "蓝标"),
                           ("体验装", "体验装"), ("家族", "全家族"), ("组合", "组合"),
                           ("宣纸", "宣纸草扎"), ("光瓶", "光瓶")):
        if keyword in name:
            tags.append(label)
    if shot.path.lower().endswith(".png"):
        tags.append("免抠 PNG" if alpha_ok is not False else "PNG")
    return tags[:8]


def _fmt_size(num_bytes: int | None) -> str:
    if not num_bytes:
        return "—"
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit in ("B", "KB") else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def _fmt_time(timestamp: float) -> str:
    try:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
    except (OSError, ValueError, OverflowError):
        return "—"


@dataclass
class CardMeta:
    """卡片上要画的一行字（提前算好，paint 里别再做 IO）。"""

    name: str
    capacity: str
    resolution: str
    size_text: str
    date_text: str
    format_label: str = "PNG"
    has_alpha: bool = False


class ProductCardDelegate(QStyledItemDelegate):
    """卡片式绘制：1:1 方形缩略区 + 角标 + 悬停动作 + 底部元信息。

    每个控件挂不了多个 QGraphicsEffect，投影这类效果在这里用画笔自己画，
    密集列表里开销可控。
    """

    COPY_CHIP = (86, 28)
    OPEN_CHIP = (60, 28)

    def __init__(self, page, parent=None):
        super().__init__(parent)
        self.page = page
        self.hover_row = -1
        self.dark = S.get_theme() == "dark"
        self.checker = checker_tile(self.dark)

    # ── 主题 ──
    def refresh_theme(self):
        self.dark = S.get_theme() == "dark"
        self.checker = checker_tile(self.dark)

    # ── 几何：卡片 / 缩略区 / 动作按钮（视图命中测试也用这几个） ──
    @staticmethod
    def card_rect(item_rect: QRect) -> QRect:
        return item_rect.adjusted(CARD_MARGIN, CARD_MARGIN, -CARD_MARGIN, -CARD_MARGIN)

    @staticmethod
    def thumb_rect(item_rect: QRect) -> QRect:
        card = ProductCardDelegate.card_rect(item_rect)
        side = card.width()
        return QRect(card.x(), card.y(), side, side)

    @classmethod
    def chip_rects(cls, item_rect: QRect) -> tuple[QRect, QRect]:
        """悬停时浮出的两个动作按钮（复制 PNG / 打开）。"""
        thumb = cls.thumb_rect(item_rect)
        gap = 8
        copy_w, copy_h = cls.COPY_CHIP
        open_w, open_h = cls.OPEN_CHIP
        total = copy_w + gap + open_w
        left = thumb.x() + (thumb.width() - total) // 2
        top = thumb.y() + (thumb.height() - copy_h) // 2
        return (QRect(left, top, copy_w, copy_h),
                QRect(left + copy_w + gap, top, open_w, open_h))

    # ── 绘制 ──
    def paint(self, painter: QPainter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        item_rect = option.rect
        card = self.card_rect(item_rect)
        thumb = self.thumb_rect(item_rect)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = index.row() == self.hover_row

        # 卡片底 + 边框（选中：主色边框 + 主色柔光圈；悬停：主色描边）
        border_color = (QColor(S._ACCENT) if (selected or hovered)
                        else QColor(_to_qcolor(S._BORDER)))
        path = QPainterPath()
        path.addRoundedRect(card, CARD_RADIUS, CARD_RADIUS)
        painter.fillPath(path, QColor(_to_qcolor(S._CARD_BG)))
        if selected:
            ring = QPainterPath()
            ring.addRoundedRect(card.adjusted(-1, -1, 1, 1), CARD_RADIUS + 1, CARD_RADIUS + 1)
            painter.setPen(QPen(QColor(S._ACCENT), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(ring)
        painter.setPen(QPen(border_color, 1.2 if (selected or hovered) else 1.0))
        painter.drawPath(path)
        # 顶部 1px 柔和受光高光线（零开销提升卡片精致立体感）
        hi_alpha = 55 if self.dark else 180
        painter.setPen(QPen(QColor(255, 255, 255, hi_alpha), 1))
        painter.drawLine(card.left() + CARD_RADIUS, card.top() + 1,
                         card.right() - CARD_RADIUS, card.top() + 1)

        # 缩略区：先裁剪到卡片圆角，再铺透底背景 + 图
        painter.save()
        painter.setClipPath(path)
        paint_backdrop(painter, thumb, self.page.backdrop_mode, self.dark, self.checker)

        icon = index.data(Qt.ItemDataRole.DecorationRole)
        if isinstance(icon, QIcon):
            side = max(16, thumb.width() - THUMB_PAD * 2)
            # 请求物理像素尺寸（×DPR），高分屏上缩略图才够清晰
            dpr = painter.device().devicePixelRatioF() if painter.device() else 1.0
            dpr = dpr or 1.0
            pixmap = icon.pixmap(QSize(round(side * dpr), round(side * dpr)))
            if not pixmap.isNull():
                # ⚠ pixmap.width() 是【物理像素】，side 是【逻辑像素】——
                # 直接 min() 混算，在 125%/150% 缩放屏上会把图放大 DPR 倍、
                # 溢出缩略区盖住下面的文件名/日期行（实测复现过）。
                # 先除回 DPR 转成逻辑尺寸再算目标框，drawPixmap 会按物理分辨率渲染（更清晰）。
                pm_dpr = pixmap.devicePixelRatio() or 1.0
                logical_w = pixmap.width() / pm_dpr
                logical_h = pixmap.height() / pm_dpr
                target_w = min(side, logical_w)
                target_h = logical_h * target_w / max(1.0, logical_w)
                x = thumb.x() + (thumb.width() - target_w) // 2
                y = thumb.y() + (thumb.height() - target_h) // 2
                painter.drawPixmap(x, y, round(target_w), round(target_h), pixmap)

        if hovered:
            painter.fillRect(thumb, QColor(15, 23, 42, 105))
        painter.restore()

        meta: CardMeta | None = index.data(Qt.ItemDataRole.UserRole + 1)
        if meta is not None:
            self._paint_badges(painter, thumb, meta)
        if hovered:
            self._paint_hover_actions(painter, item_rect, selected)
        if meta is not None:
            self._paint_meta(painter, card, meta, hovered or selected)
        painter.restore()

    def _paint_badges(self, painter: QPainter, thumb: QRect, meta: CardMeta):
        font = QFont()
        font.setPixelSize(9)
        font.setBold(True)
        painter.setFont(font)
        x = thumb.x() + 8
        y = thumb.y() + 8
        badge_bg = QColor("#059669") if meta.has_alpha else QColor(100, 116, 139, 200)
        x = _draw_chip(painter, x, y, meta.format_label, badge_bg, QColor("#FFFFFF"), font)
        if meta.capacity:
            _draw_chip(painter, x + 4, y, meta.capacity, QColor(15, 23, 42, 150),
                       QColor("#FFFFFF"), font)
        if meta.resolution and meta.resolution != "—":
            label = meta.resolution.replace(" ", "")
            width = QFontMetrics(font).horizontalAdvance(label) + 12
            rect = QRect(thumb.right() - width - 8, thumb.bottom() - 22, width, 16)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(15, 23, 42, 130))
            painter.drawRoundedRect(rect, 4, 4)
            painter.setPen(QColor("#FFFFFF"))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)

    def _paint_hover_actions(self, painter: QPainter, item_rect: QRect, selected: bool):
        copy_rect, open_rect = self.chip_rects(item_rect)
        font = QFont()
        font.setPixelSize(11)
        font.setBold(True)
        painter.setFont(font)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#059669") if not selected else QColor("#047857"))
        painter.drawRoundedRect(copy_rect, 8, 8)
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(copy_rect, Qt.AlignmentFlag.AlignCenter, "复制 PNG")

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(S._ACCENT))
        painter.drawRoundedRect(open_rect, 8, 8)
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(open_rect, Qt.AlignmentFlag.AlignCenter, "打开")

    def _paint_meta(self, painter: QPainter, card: QRect, meta: CardMeta, active: bool):
        left = card.x() + 10
        right = card.right() - 10
        top = card.bottom() - META_HEIGHT
        name_font = QFont()
        name_font.setPixelSize(12)
        name_font.setBold(True)
        painter.setFont(name_font)
        painter.setPen(QColor(S._ACCENT) if active else QColor(_to_qcolor(S._TEXT_PRIMARY)))
        metrics = QFontMetrics(name_font)
        elided = metrics.elidedText(meta.name, Qt.TextElideMode.ElideRight, right - left)
        painter.drawText(QRect(left, top + 6, right - left, 16),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided)

        sub_font = QFont()
        sub_font.setPixelSize(10)
        painter.setFont(sub_font)
        painter.setPen(QColor(_to_qcolor(S._TEXT_TERTIARY)))
        painter.drawText(QRect(left, top + 24, (right - left) // 2, 14),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         meta.size_text)
        painter.drawText(QRect(left, top + 24, right - left, 14),
                         Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                         meta.date_text)


def _to_qcolor(text: str, fallback: str = "#888888") -> QColor:
    """主题 token 里深色是 rgba(...) 写法，QColor 不认，得自己解析。"""
    from .icons import to_qcolor

    return to_qcolor(text, fallback)



def _draw_chip(painter: QPainter, x: int, y: int, text: str, bg: QColor, fg: QColor,
               font: QFont) -> int:
    """画一个小胶囊标签，返回它的右缘 x。"""
    width = QFontMetrics(font).horizontalAdvance(text) + 12
    rect = QRect(x, y, width, 16)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(bg)
    painter.drawRoundedRect(rect, 4, 4)
    painter.setPen(fg)
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
    return rect.right()


def grid_qss() -> str:
    """网格区的滚动条：全局那套在高密度列表里显得又粗又重，这里单独收细。"""
    from .styles import _SCROLLBAR_THUMB, _SCROLLBAR_THUMB_HOVER

    return f"""
        QListWidget {{ background: transparent; border: 0; }}
        QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
        QScrollBar::handle:vertical {{
            background: {_SCROLLBAR_THUMB}; border-radius: 999px; min-height: 40px;
        }}
        QScrollBar::handle:vertical:hover {{ background: {_SCROLLBAR_THUMB_HOVER}; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    """


class ProductGrid(QListWidget):
    """缩略图矩阵：自己管悬停行，并把「复制 PNG / 打开」的点击挑出来。"""

    copyRequested = Signal(int)
    openRequested = Signal(int)
    hoverChanged = Signal(int)
    resized = Signal()

    def __init__(self, page, parent=None):
        super().__init__(parent)
        self.page = page
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setMovement(QListWidget.Movement.Static)
        self.setWordWrap(False)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setStyleSheet(grid_qss())
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.setItemDelegate(ProductCardDelegate(page, self))
        self._hover_row = -1

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            paths = [u.toLocalFile() for u in event.mimeData().urls() if u.toLocalFile()]
            if paths and hasattr(self.page, "open_add_product_dialog"):
                event.acceptProposedAction()
                self.page.open_add_product_dialog(paths)
                return
        super().dropEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resized.emit()

    # ── 滚轮：一格滚「一行卡片」，而不是一整页 ──
    #
    # 滚轮策略统一在 ui/wheel.py：一格 = 固定像素。但卡片很大（207px），
    # 按固定像素滚会把卡片切一半，所以这里用 `wheel_step()` 覆盖成「一行卡片的高度」，
    # 再实现 `snap_scroll()` 把落点对齐整行 —— 卡片永远完整。
    WHEEL_FALLBACK_STEP = 100          # 拿不到行高时的兜底步长

    def _row_pitch(self) -> int:
        """一行卡片占多高（卡片高 + 行间距）。"""
        # 图标模式下条目按 gridSize 的格子摆放，格子高就是行距 —— 优先用它，
        # 比从 visualItemRect 反推可靠（条目本身的高度不含行间距）。
        size = self.gridSize()
        if size.isValid() and size.height() > 0:
            return size.height()
        count = self.count()
        if count == 0:
            return 0
        first = self.visualItemRect(self.item(0))
        if not first.isValid() or first.height() <= 0:
            return 0
        columns = 0
        for i in range(count):
            if self.visualItemRect(self.item(i)).top() == first.top():
                columns += 1
            else:
                break
        if columns <= 0 or count <= columns:
            return first.height()
        following = self.visualItemRect(self.item(columns)).top() - first.top()
        return max(first.height(), following)

    def _snap_to_row(self) -> None:
        """把滚动位置对齐到整行，别让卡片卡在视口边上露一半。"""
        bar = self.verticalScrollBar()
        pitch = self._row_pitch()
        if bar is None or pitch <= 0 or self.count() == 0:
            return
        top = self.visualItemRect(self.item(0)).top()
        origin = bar.value() + top                 # 首行在内容里的固定基准
        bar.setValue(int(origin - round(top / pitch) * pitch))

    def wheel_step(self) -> int:
        """一格滚轮 = 一行卡片（给 ui/wheel.py 的全局策略用）。"""
        return self._row_pitch() or self.WHEEL_FALLBACK_STEP

    def snap_scroll(self) -> None:
        """滚完把落点对齐整行，卡片不会被切一半（给全局策略调）。"""
        self._snap_to_row()

    def wheelEvent(self, event):
        # 正常情况下全局策略（ui/wheel.py）已经处理掉了；这里只是让本控件
        # 单独用（比如测试里不装全局策略）时行为一致。
        if scroll_by_wheel(self, event):
            return
        super().wheelEvent(event)

    def mouseMoveEvent(self, event):
        index = self.indexAt(event.position().toPoint())
        row = index.row() if index.isValid() else -1
        if row != self._hover_row:
            previous = self._hover_row
            self._hover_row = row
            self.itemDelegate().hover_row = row
            for changed in (previous, row):
                if changed >= 0:
                    item = self.item(changed)
                    if item is not None:
                        self.update(self.visualItemRect(item))
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        previous = self._hover_row
        self._hover_row = -1
        self.itemDelegate().hover_row = -1
        if previous >= 0:
            item = self.item(previous)
            if item is not None:
                self.update(self.visualItemRect(item))
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            index = self.indexAt(event.position().toPoint())
            if index.isValid():
                item_rect = self.visualRect(index)
                copy_rect, open_rect = ProductCardDelegate.chip_rects(item_rect)
                point = event.position().toPoint()
                if copy_rect.contains(point):
                    self.copyRequested.emit(index.row())
                    return
                if open_rect.contains(point):
                    self.openRequested.emit(index.row())
                    return
        super().mousePressEvent(event)


class BackdropStage(QWidget):
    """1:1 检视舞台：透底背景 + 居中大图。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._image: QImage | None = None
        self._mode = "checker"
        self._dark = S.get_theme() == "dark"
        self._checker = checker_tile(self._dark)
        self.setMinimumSize(160, 160)

    def set_mode(self, mode: str):
        self._mode = mode
        self.update()

    def set_image(self, image: QImage | None):
        self._image = image
        self.update()

    def refresh_theme(self):
        self._dark = S.get_theme() == "dark"
        self._checker = checker_tile(self._dark)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = self.rect().adjusted(0, 0, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(rect, 10, 10)
        painter.setClipPath(path)
        paint_backdrop(painter, rect, self._mode, self._dark, self._checker)
        image = self._image
        if image is not None and not image.isNull():
            box = QSize(max(16, int(rect.width() * 0.86)), max(16, int(rect.height() * 0.86)))
            scaled = image.scaled(box, Qt.AspectRatioMode.KeepAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation)
            painter.drawImage(
                rect.x() + (rect.width() - scaled.width()) // 2,
                rect.y() + (rect.height() - scaled.height()) // 2,
                scaled)
        else:
            painter.setPen(QColor(_to_qcolor(S._TEXT_TERTIARY)))
            font = QFont()
            font.setPixelSize(12)
            painter.setFont(font)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "选中一张图看详情")
        painter.end()


class ProductPage(QWidget):
    """产品库页（支持自主添加产品入库、拖拽导入、系列创建与管理）。"""

    productsAdded = Signal(int)

    def __init__(self, repository, thread_pool, root: str | None = None, parent=None):
        super().__init__(parent)
        self.repository = repository
        self.thread_pool = thread_pool
        self.setAcceptDrops(True)
        self._explicit_root = Path(root) if root else None
        self._wecom_custom_root: str = ""
        try:
            self._wecom_custom_root = repository.get_setting("wecom_root", "")
            load_custom_series_groups(repository.get_setting("custom_series_groups", ""))
        except Exception:
            pass
        self.root = self._resolve_library_root()
        self._shots: list[ProductShot] = []
        self._filtered: list[ProductShot] = []
        self._thumb_epoch = 0
        self._pending_thumbs: dict[int, tuple[str, QImage]] = {}
        self._pending_schedule: deque = deque()
        # 已经派发过解码任务的行号，避免滚动来回时重复排队（换源/改筛选时清空）
        self._thumb_queued_rows: set[int] = set()
        self._active_series = "全部"
        self._active_brand: str | None = None
        self._columns = DEFAULT_COLUMNS
        self._effective_columns = DEFAULT_COLUMNS
        self.backdrop_mode = "checker"
        self._kind_filter = KIND_ALL
        self._alpha_only = True     # 产品库的意义就是透明底免抠图，非透明的一律先藏着
        self._inspector_open = False
        self._current_shot: ProductShot | None = None
        self._index_epoch = 0
        # 多来源：每个来源扫描一次后缓存，换来换去不用重扫
        self._source_key = "library"
        self._source_cache: dict[str, list[ProductShot]] = {}
        self._source_counts: dict[str, int] = {}

        self._image_pool = QThreadPool(self)
        self._image_pool.setMaxThreadCount(3)
        self._render_pool = QThreadPool(self)
        self._render_pool.setMaxThreadCount(1)
        # 全盘扫描单独一个池：它是几十秒级的长任务，丢进 _render_pool（单线程）
        # 会把右栏大图和 Alpha 校验一起堵死，扫描期间点开大图毫无反应。
        self._scan_pool = QThreadPool(self)
        self._scan_pool.setMaxThreadCount(1)

        self._apply_timer = QTimer(self)
        self._apply_timer.setInterval(90)
        self._apply_timer.timeout.connect(self._flush_pending_thumbs)

        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(120)
        self._resize_timer.timeout.connect(self._apply_density)

        self._alpha_epoch = 0
        self._verify_queue: list[tuple[Path, int, int]] = []   # 待做像素核查的图
        self._by_path: dict[str, ProductShot] = {}
        # 记住上次的来源和密度，重开就回到原位
        saved_source = ""
        saved_columns = ""
        saved_alpha = ""
        saved_dimension = "series"
        try:
            saved_source = repository.get_setting("product_source", "library")
            saved_columns = repository.get_setting("product_columns", "")
            saved_alpha = repository.get_setting("product_alpha_only", "1")
            saved_dimension = repository.get_setting("product_group_dimension", "series")
        except Exception:  # noqa: BLE001 - 老库没有 settings 表也不该崩
            pass
        self._alpha_only = saved_alpha != "0"
        # 左栏的归类方式：按系列（默认）或按容量
        self._group_dimension = saved_dimension if saved_dimension in ("series", "capacity") else "series"
        self._active_band: str | None = None        # 容量维度：档位
        self._active_capacity: str | None = None    # 容量维度：具体容量（或「未标注」下的大类）
        if saved_source in {src.key for src in SOURCES}:
            self._source_key = saved_source
        if saved_columns.isdigit() and COLUMN_MIN <= int(saved_columns) <= COLUMN_MAX:
            self._columns = int(saved_columns)
        self._build_ui()
        self._apply_density()
        self.reload()

    # ══ UI ══
    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "rail"):
            self.rail.setFixedWidth(236 if self.width() < 850 else 254)

    def _build_ui(self):
        # 素材浏览器是「越宽越好」的页面，别用默认的 1120 收窄，否则格子只能挤到 160px 上下
        body = page_body(self, spacing=14, max_width=1720)

        # ── 页头：标题 + 透底背景切换 ──
        head = QHBoxLayout()
        head.setSpacing(10)
        title = QLabel("产品库")
        title.setObjectName("PageTitle")
        head.addWidget(title)
        tag = QLabel("PRODUCT LIBRARY")
        tag.setObjectName("TitleTag")
        head.addWidget(tag)
        head.addStretch()

        backdrop_box = QFrame()
        backdrop_box.setObjectName("Segmented")
        backdrop_lay = QHBoxLayout(backdrop_box)
        backdrop_lay.setContentsMargins(3, 3, 3, 3)
        backdrop_lay.setSpacing(2)
        hint = QLabel("透底背景")
        hint.setObjectName("SegmentHint")
        backdrop_lay.addWidget(hint)
        self.backdrop_buttons: dict[str, QPushButton] = {}
        for key, label in BACKDROPS:
            button = QPushButton(label)
            button.setObjectName("SegmentButton")
            button.setCheckable(True)
            button.setChecked(key == self.backdrop_mode)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked, k=key: self.set_backdrop_mode(k))
            backdrop_lay.addWidget(button)
            self.backdrop_buttons[key] = button
        head.addWidget(backdrop_box)

        self.count_label = QLabel("")
        self.count_label.setObjectName("Caption")
        head.addWidget(self.count_label)
        body.addLayout(head)

        self.root_label = QLabel("")
        self.root_label.setObjectName("PageSubtitle")
        body.addWidget(self.root_label)

        # ── 三栏：系列树 / 卡片矩阵 / 检视面板 ──
        columns = QHBoxLayout()
        columns.setSpacing(14)
        columns.addWidget(self._build_rail(), 0)
        columns.addLayout(self._build_center(), 1)
        self.inspector = self._build_inspector()
        self.inspector.setVisible(self._inspector_open)
        columns.addWidget(self.inspector, 0)
        body.addLayout(columns, 1)

        shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        shortcut.activated.connect(lambda: (self.search.setFocus(), self.search.selectAll()))
        copy_shortcut = QShortcut(QKeySequence.StandardKey.Copy, self)
        copy_shortcut.activated.connect(self._copy_selected_images)

    def _build_rail(self) -> QFrame:
        self.rail = ProductRail()
        self.rail.set_dimension(self._group_dimension)   # 先定维度，再连信号
        self.rail.selectionChanged.connect(self._on_rail_selection)
        self.rail.searchChanged.connect(self._on_rail_search)
        self.rail.dimensionChanged.connect(self.set_group_dimension)
        return self.rail

    def set_group_dimension(self, key: str):
        """切换左栏的归类方式：按系列 / 按容量。"""
        if key not in ("series", "capacity") or key == self._group_dimension:
            return
        self._group_dimension = key
        self._active_series, self._active_brand = "全部", None
        self._active_band = None
        self._active_capacity = None
        try:
            self.repository.set_setting("product_group_dimension", key)
        except Exception:  # noqa: BLE001 - 老库没有 settings 表也不该崩
            pass
        self.rail.set_dimension(key)      # 幂等：状态一致时不会回头再触发
        self._refresh_rail()
        self._apply_filter()

    def eventFilter(self, obj, event):
        """顶栏宽度变化时自适应收起次要控件。

        顶栏一行的完整内容约 836px（来源下拉框 186 + 添加按钮 116 + 选目录 104 +
        系列标题 130 + 计数胶囊 100 + 搜索框 160 + 间距），加上自身边距需要约
        862px 宽的顶栏。中心列在窄窗口或展开检视面板时会掉到 300~800px——
        QHBoxLayout 装不下时 qGeomCalc 会把每个控件按比例压缩到最小宽度以下、
        互相叠在一起（「添加产品」压住来源下拉框，实测复现）。所以按可用宽度
        分级收起：先藏计数胶囊，再藏系列标题，最后藏选目录按钮并把搜索框和
        来源下拉框一并收窄。固定尺寸的取值见 _build_center 的注释。
        """
        if obj is self._top_bar and event.type() == QEvent.Type.Resize:
            width = obj.width()
            self.filtered_label.setVisible(width >= 870)
            self.series_label.setVisible(width >= 760)
            self.choose_dir_button.setVisible(width >= 625)
            # 极窄档（检视面板展开 + 窄窗口）：连搜索框都放下，只剩来源+添加按钮
            no_search = width < 510
            self.search.setVisible(not no_search)
            self.source_combo.setFixedSize(160 if no_search else (170 if width < 625 else 186), 32)
            self.search.setFixedSize(150 if width < 625 else 160, 32)
        return super().eventFilter(obj, event)

    def _build_center(self) -> QVBoxLayout:
        center = QVBoxLayout()
        center.setSpacing(10)

        bar = QFrame()
        bar.setObjectName("Surface")
        # 控制栏分两行：窗口一窄 + 展开检视面板时，挤成一行会互相压字（实测过）
        bar_lay = QVBoxLayout(bar)
        bar_lay.setContentsMargins(12, 9, 12, 9)
        bar_lay.setSpacing(8)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)
        # ⚠ 顶栏控件全部固定尺寸。原因：控件在隐藏页面里首次布局时还没 polish，
        # 布局缓存的是「pre-polish 的旧 sizeHint」，控件显示后 hint 变大但布局
        # 不会重排——「添加产品」按钮被压在来源下拉框身下（离屏多宽度实测复现，
        # invalidate/activate/adjustSize/重建条目都治不了）。固定尺寸让构造期与
        # polish 后的 hint 完全一致，第一次排布就是正确的，从根上绕开这个坑。
        # 代价是窄窗口下需要 eventFilter 按宽度分级收起次要控件（见 eventFilter）。
        # 来源选择：产品库只有 G 盘那 954 张，企微缓存和归档里还有一大批
        self.source_combo = QComboBox()
        self.source_combo.setObjectName("SourceCombo")
        self.source_combo.setFixedSize(186, 32)
        self.source_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        for source in SOURCES:
            self.source_combo.addItem(source.label, source.key)
            self.source_combo.setItemData(
                self.source_combo.count() - 1, source.hint, Qt.ItemDataRole.ToolTipRole)
        saved_index = self.source_combo.findData(self._source_key)
        if saved_index >= 0:
            self.source_combo.setCurrentIndex(saved_index)
        self.source_combo.currentIndexChanged.connect(self._on_source_changed)
        top_row.addWidget(self.source_combo)

        self.add_product_button = QPushButton("添加产品")
        self.add_product_button.setObjectName("PrimaryButton")
        self.add_product_button.setIcon(painted_icon("plus", "#FFFFFF", 14))
        self.add_product_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_product_button.setFixedSize(116, 32)
        self.add_product_button.setToolTip("自主添加单张或多张产品图到产品库（支持新建产品系列与直接拖拽图片入库）")
        self.add_product_button.clicked.connect(lambda: self.open_add_product_dialog())
        top_row.addWidget(self.add_product_button)

        self.choose_dir_button = QPushButton("选择目录…")
        self.choose_dir_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.choose_dir_button.setFixedSize(104, 32)
        self.choose_dir_button.setToolTip("指定当前来源的文件夹路径（换电脑或自定义产品库/企微缓存目录时使用）")
        self.choose_dir_button.clicked.connect(self.choose_source_folder)
        top_row.addWidget(self.choose_dir_button)

        self.series_label = QLabel("全部产品")
        self.series_label.setObjectName("SectionTitle")
        self.series_label.setFixedWidth(130)
        self.series_label.setToolTip("当前浏览的系列（在左侧栏选择）")
        top_row.addWidget(self.series_label)
        self.filtered_label = QLabel("")
        self.filtered_label.setObjectName("CountPill")
        self.filtered_label.setFixedWidth(100)
        top_row.addWidget(self.filtered_label)

        self.search = QLineEdit()
        self.search.setObjectName("SearchField")
        self.search.setPlaceholderText("搜索商品名 / 毫升数 / 46度…")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedSize(160, 32)
        self.search.textChanged.connect(self._apply_filter)
        top_row.addWidget(self.search)
        bar_lay.addLayout(top_row)
        # 盯住顶栏宽度，窄时分级收起次要控件（见 eventFilter）
        self._top_bar = bar
        bar.installEventFilter(self)

        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(8)
        # 类型筛选：全部 / 单瓶 / 礼袋套装
        kind_box = QFrame()
        kind_box.setObjectName("Segmented")
        kind_lay = QHBoxLayout(kind_box)
        kind_lay.setContentsMargins(3, 3, 3, 3)
        kind_lay.setSpacing(2)
        self.kind_buttons: dict[str, QPushButton] = {}
        for key, label in KIND_FILTERS:
            button = QPushButton(label)
            button.setObjectName("SegmentButton")
            button.setCheckable(True)
            button.setChecked(key == self._kind_filter)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked, k=key: self.set_kind_filter(k))
            kind_lay.addWidget(button)
            self.kind_buttons[key] = button
        bottom_row.addWidget(kind_box)

        # 密度：3–6 列
        density = QFrame()
        density.setObjectName("Segmented")
        density_lay = QHBoxLayout(density)
        density_lay.setContentsMargins(8, 3, 8, 3)
        density_lay.setSpacing(6)
        density_icon = QLabel()
        self._density_icon = density_icon
        density_icon.setPixmap(painted_icon("grid", S._TEXT_TERTIARY, 13).pixmap(13, 13))
        density_icon.setStyleSheet("background: transparent;")
        density_lay.addWidget(density_icon)
        self.density_label = QLabel(f"{self._columns} 列")
        self.density_label.setObjectName("DensityLabel")
        density_lay.addWidget(self.density_label)
        self.density = QSlider(Qt.Orientation.Horizontal)
        self.density.setObjectName("DensitySlider")
        self.density.setRange(COLUMN_MIN, COLUMN_MAX)
        self.density.setValue(self._columns)
        self.density.setFixedWidth(76)
        self.density.setPageStep(1)
        self.density.valueChanged.connect(self._on_density_changed)
        density_lay.addWidget(self.density)
        bottom_row.addWidget(density)

        self.alpha_check = QCheckBox("只看透明底")
        self.alpha_check.setObjectName("AlphaCheck")
        self.alpha_check.setChecked(self._alpha_only)
        self.alpha_check.setCursor(Qt.CursorShape.PointingHandCursor)
        self.alpha_check.setToolTip("产品图是透明底免抠 PNG；勾选后隐藏详情页 / 场景图 / JPG 等无透明通道的图")
        self.alpha_check.toggled.connect(self.set_alpha_only)
        bottom_row.addWidget(self.alpha_check)
        bottom_row.addStretch()

        # 检视面板开关
        self.inspector_button = QToolButton()
        self.inspector_button.setObjectName("InspectorToggle")
        self.inspector_button.setIcon(painted_icon("sliders", S._TEXT_SECONDARY, 15))
        self.inspector_button.setCheckable(True)
        self.inspector_button.setChecked(self._inspector_open)
        self.inspector_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.inspector_button.setToolTip("展开 / 收起右侧 PNG 详情")
        self.inspector_button.clicked.connect(self.toggle_inspector)
        bottom_row.addWidget(self.inspector_button)
        bar_lay.addLayout(bottom_row)

        center.addWidget(bar)

        self.status = QLabel("")
        self.status.setObjectName("Caption")
        self.status.setWordWrap(False)
        center.addWidget(self.status)

        self.grid = ProductGrid(self)
        self.grid.copyRequested.connect(self._copy_row)
        self.grid.openRequested.connect(self._open_row)
        self.grid.itemSelectionChanged.connect(self._on_selection_changed)
        self.grid.itemDoubleClicked.connect(lambda _item: self._open_selected())
        self.grid.customContextMenuRequested.connect(self._show_context_menu)
        self.grid.verticalScrollBar().valueChanged.connect(self._on_grid_scrolled)
        self.grid.resized.connect(self._resize_timer.start)
        center.addWidget(self.grid, 1)
        self.empty_state = EmptyStatePanel("image")
        self.empty_state.action_button.clicked.connect(self._on_empty_action)
        self.empty_state.hide()
        center.addWidget(self.empty_state, 1)
        return center

    def _on_empty_action(self):
        if not self._shots:
            self.open_add_product_dialog()
            return
        self.search.clear()
        self.rail.search.clear()
        self.rail.reset_selection()
        self._active_series = "全部"
        self._active_brand = None
        self._active_band = None
        self._active_capacity = None
        self.set_kind_filter(KIND_ALL)
        self.set_alpha_only(False)

    def _build_inspector(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("Surface")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(6)
        head_icon = QLabel()
        head_icon.setPixmap(painted_icon("image", S._ACCENT, 15).pixmap(15, 15))
        head_icon.setStyleSheet("background: transparent;")
        head.addWidget(head_icon)
        head_title = QLabel("免抠 PNG 单品检视")
        head_title.setObjectName("SectionTitle")
        head.addWidget(head_title)
        head.addStretch()
        close = QToolButton()
        self._inspector_close = close
        close.setObjectName("InspectorClose")
        close.setIcon(painted_icon("close", S._TEXT_TERTIARY, 14))
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.clicked.connect(self.toggle_inspector)
        head.addWidget(close)
        lay.addLayout(head)

        self.stage = BackdropStage()
        self.stage.set_mode(self.backdrop_mode)
        lay.addWidget(self.stage)

        self.inspector_name = QLabel("")
        self.inspector_name.setObjectName("InspectorName")
        self.inspector_name.setWordWrap(True)
        lay.addWidget(self.inspector_name)

        self.copy_button = QPushButton("  一键复制免抠 PNG（Ctrl+C）")
        self.copy_button.setObjectName("PrimaryButton")
        self.copy_button.setIcon(painted_icon("copy", "#FFFFFF", 15))
        self.copy_button.clicked.connect(self._copy_selected_images)
        lay.addWidget(self.copy_button)

        self.reveal_button = QPushButton("  在资源管理器中定位")
        self.reveal_button.setIcon(painted_icon("external", S._TEXT_SECONDARY, 15))
        self.reveal_button.clicked.connect(self._reveal_selected)
        lay.addWidget(self.reveal_button)

        self.spec_box = QFrame()
        self.spec_box.setObjectName("SpecBox")
        spec_lay = QVBoxLayout(self.spec_box)
        spec_lay.setContentsMargins(10, 9, 10, 9)
        spec_lay.setSpacing(6)
        self.spec_rows: dict[str, QLabel] = {}
        for key in ("分辨率", "格式", "透明通道", "文件大小", "更新时间"):
            row = QHBoxLayout()
            row.setSpacing(8)
            name = QLabel(key)
            name.setObjectName("SpecKey")
            row.addWidget(name)
            row.addStretch()
            value = QLabel("—")
            value.setObjectName("SpecValue")
            row.addWidget(value)
            spec_lay.addLayout(row)
            self.spec_rows[key] = value
        lay.addWidget(self.spec_box)

        tags_head = QLabel("资产标签")
        tags_head.setObjectName("SpecKey")
        lay.addWidget(tags_head)
        self.tags_holder = QWidget()
        self.tags_layout = _FlowLayout(self.tags_holder)
        lay.addWidget(self.tags_holder)

        lay.addStretch()
        self.inspector_footer = QLabel("")
        self.inspector_footer.setObjectName("Caption")
        lay.addWidget(self.inspector_footer)

        frame.setFixedWidth(272)
        return frame

    # ══ 扫描与树 ══
    def set_source(self, key: str):
        """切换来源：命中缓存就秒切，否则后台扫一遍。"""
        if key not in {s.key for s in SOURCES}:
            return
        self._source_key = key
        try:
            self.repository.set_setting("product_source", key)
        except Exception:  # noqa: BLE001
            pass
        cached = self._source_cache.get(key)
        if cached is not None:
            self._shots = cached
            self._refresh_rail()
            self._apply_filter()
            self._update_source_labels(len(cached))
            return
        self.reload()

    def _on_source_changed(self, index: int):
        key = self.source_combo.itemData(index)
        if key and key != self._source_key:
            self.set_source(str(key))

    def _resolve_library_root(self) -> Path:
        """解析产品库目录：显式传参 -> 设置表 product_library_root -> 资产库关联的产品库文件夹 -> 默认 G:\劲牌产品PNG。"""
        if self._explicit_root is not None:
            return self._explicit_root
        try:
            saved = self.repository.get_setting("product_library_root", "")
            if saved:
                return Path(saved)
            lib_root = self.repository.get_root_directory()
            if lib_root:
                found = self.repository.find_product_folder(lib_root)
                if found:
                    candidate = Path(found) if os.path.isabs(found) else (Path(lib_root) / found)
                    if candidate.is_dir():
                        return candidate
        except Exception:
            pass
        default = Path(ROOT_DEFAULT)
        if default.is_dir():
            return default
        docs = user_known_folders().get("Documents", Path.home() / "Documents")
        return docs / "视觉产品库PNG"

    def choose_source_folder(self):
        """手动指定当前来源的目录（支持在任何电脑上自定义产品库、企微缓存目录或归档目录）。"""
        if self._source_key == "wecom":
            selected = QFileDialog.getExistingDirectory(
                self, "选择企业微信数据目录（WXWork）", self._wecom_custom_root or str(Path.home() / "Documents")
            )
            if not selected:
                return
            self._wecom_custom_root = selected
            try:
                self.repository.set_setting("wecom_root", selected)
            except Exception:
                pass
            self._source_cache.pop("wecom", None)
            self.reload()
            return
        if self._source_key == "archive":
            selected = QFileDialog.getExistingDirectory(
                self, "选择归档盘根目录", str(organizer.DEST_DRIVE)
            )
            if not selected:
                return
            organizer.set_dest_drive(selected)
            try:
                self.repository.set_archive_dest(selected)
            except Exception:
                pass
            self._source_cache.pop("archive", None)
            self.reload()
            return
        selected = QFileDialog.getExistingDirectory(
            self, "选择产品库文件夹（免抠 PNG 目录）", str(self.root if self.root.exists() else Path.home())
        )
        if not selected:
            return
        self.root = Path(selected)
        try:
            self.repository.set_setting("product_library_root", selected)
            self.repository.set_product_folder(selected)
        except Exception:
            pass
        self._source_cache.pop("library", None)
        self.reload()

    def _current_source(self) -> Source:
        return next((s for s in SOURCES if s.key == self._source_key), SOURCES[0])

    def reload(self):
        """重扫当前来源（后台），完成后重建系列树。"""
        self.root = self._resolve_library_root()
        source = self._current_source()
        self._shots = []
        self._filtered = []
        self.grid.clear()
        self._active_series = "全部"
        self._active_brand = None
        self.rail.reset_selection()
        self.rail.set_groups([], 0)
        self.status.setText(f"正在扫描「{source.label}」…")
        self.status.repaint()
        self._index_epoch += 1

        page = self
        key = self._source_key
        lib_root = self.root
        wx_root = self._wecom_custom_root or None
        arch_root = organizer.DEST_DRIVE

        class _IndexWorker(QRunnable):
            class _Signals(QObject):
                done = Signal(object)

            def __init__(self, source_key, token):
                super().__init__()
                self.source_key = source_key
                self.token = token
                self.signals = self._Signals()

            @Slot()
            def run(self):
                try:
                    shots = build_source_index(
                        self.source_key,
                        library_root=lib_root,
                        wecom_root=wx_root,
                        archive_root=arch_root,
                    )
                except Exception:  # noqa: BLE001 - 扫盘失败不能让页面卡住
                    shots = []
                try:
                    self.signals.done.emit((self.token, self.source_key, shots))
                except RuntimeError:
                    pass

        worker = _IndexWorker(key, self._index_epoch)
        worker.signals.done.connect(page._on_index_built)
        self._scan_pool.start(worker)

    def _update_source_labels(self, count: int):
        source = self._current_source()
        png = sum(1 for s in self._shots if s.has_alpha)
        square = sum(1 for s in self._shots if s.is_square_800)
        self.root_label.setText(
            f"{source.hint}　·　共 {count} 张，其中 {png} 张是透明底"
            + (f"，{square} 张 800×800" if square else ""))
        self.count_label.setText(f"{count} 张")
        # 左栏底部：路径 + 索引状态（透明底张数才是真正能用的产品图）
        first_path = Path(self._shots[0].path).parent if self._shots else self.root
        self.rail.set_footer(
            str(first_path),
            f"已索引 {count:,} 张 · 透明底 {png:,} 张 · 800×800 {square:,} 张")

    def _on_index_built(self, payload):
        token, key, shots = payload
        if token != self._index_epoch or key != self._source_key:
            return
        self._source_cache[key] = shots
        self._source_counts[key] = len(shots)
        self._shots = shots
        self._by_path = {shot.path: shot for shot in shots}
        self._refresh_rail()
        self._apply_filter()
        self._update_source_labels(len(shots))
        self._queue_pixel_verify()

    def _queue_pixel_verify(self):
        """把「还没做过像素核查」的图排进队列（只对产品库来源）。

        文件头只能答「有没有 Alpha 通道」，而主图 / 活动图 / 详情页也都是带 Alpha 的
        PNG —— 只看文件头会把它们当成透明底产品，这正是产品库混入宣传图的原因。
        所以要真解码看像素。分批做（每批 10 张）不阻塞界面，算完写缓存并刷新一次。
        """
        if self._source_key != "library":
            self._verify_queue = []
            return
        verifier = pixel_verifier()
        pending = verifier.take_pending()
        self._verify_queue = pending
        self._verify_changed = 0
        if not pending:
            return
        self.status.setText(f"正在核查透明底（{len(pending)} 张待查）…")
        QTimer.singleShot(12, self._verify_step)

    def _verify_step(self):
        """分批核查：每批若干张，全部查完后再统一写盘并刷新一次。"""
        if not self._verify_queue or self._source_key != "library":
            self._verify_queue = []
            return
        batch = self._verify_queue[:10]
        del self._verify_queue[:10]
        verifier = pixel_verifier()
        for path, size, stamp in batch:
            ok = judge_transparent(analyze_alpha(path))
            verifier.record(path, size, stamp, ok)
            shot = self._by_path.get(str(path))
            if shot is not None and shot.has_alpha != ok:
                shot.with_alpha(ok)
                self._verify_changed = getattr(self, "_verify_changed", 0) + 1
        if self._verify_queue:
            QTimer.singleShot(12, self._verify_step)
            return
        verifier.commit()
        if getattr(self, "_verify_changed", 0):
            self._verify_changed = 0
            self._refresh_rail()
            self._apply_filter()
            self._update_source_labels(len(self._shots))
        self.status.setText("就绪")

    def _refresh_rail(self):
        """把统计喂给左侧栏 —— 树按当前分组维度（系列 / 容量）算。"""
        groups = (self._capacity_groups() if self._group_dimension == "capacity"
                  else self._series_groups())
        self.rail.set_groups(groups, len(self._shots))
        self.rail.set_footer(self._current_source().hint.split("　")[0],
                             f"已索引 {len(self._shots):,} 张 · 透明底 {sum(1 for s in self._shots if s.has_alpha):,} 张")
        if (not self._active_series and not self._active_brand
                and not self._active_band and not self._active_capacity):
            self.rail.reset_selection()

    def _series_groups(self) -> list[tuple[str, str, list[tuple[str, int]]]]:
        """大类 → 系列 / 月份。"""
        buckets: dict[str, dict[str, int]] = {}
        for shot in self._shots:
            group = shot.brand if shot.brand in GROUP_ORDER else FALLBACK_BRAND
            buckets.setdefault(group, {}).setdefault(shot.series, 0)
            buckets[group][shot.series] += 1
        groups: list[tuple[str, str, list[tuple[str, int]]]] = []
        for name in GROUP_ORDER:
            if name not in buckets:
                continue
            children = sorted(buckets[name].items(), key=lambda kv: (-kv[1], kv[0]))
            theme_key = {"保健酒系列": "emerald", "白酒系列": "blue",
                         "品牌素材": "amber", "其他素材": "rose"}.get(name, "blue")
            groups.append((name, theme_key, children))
        return groups

    def _capacity_groups(self) -> list[tuple[str, str, list[tuple[str, int]]]]:
        """容量档 → 具体容量（小规格 / 常规装 / 大规格 / 未标注容量）。

        「未标注容量」那组的子项列的是品牌大类 —— 有一半的图文件名里确实没写容量
        （`7号样红印`、`中国劲酒520礼盒`…，那个 520 是产品名不是容量），
        兜底按大类分，至少还能找得到。
        """
        counters: dict[tuple[str, str], int] = {}
        for shot in self._shots:
            band = shot.capacity_band
            if band == CAPACITY_UNKNOWN:
                label = shot.brand if shot.brand in GROUP_ORDER else FALLBACK_BRAND
            else:
                label = shot.capacity_label
            if not label:
                continue
            counters[(band, label)] = counters.get((band, label), 0) + 1
        groups: list[tuple[str, str, list[tuple[str, int]]]] = []
        for band in CAPACITY_BANDS:
            children = [(label, n) for (band_key, label), n in counters.items() if band_key == band]
            if not children:
                continue
            if band == CAPACITY_UNKNOWN:
                children.sort(key=lambda kv: GROUP_ORDER.index(kv[0]) if kv[0] in GROUP_ORDER else 99)
            else:
                children.sort(key=lambda kv: parse_capacity_ml(kv[0]) or 0)   # 50ml → 5L
            groups.append((band, CAPACITY_BAND_COLORS.get(band, "blue"), children))
        return groups

    def _on_rail_selection(self, kind: str, key: str, group: str = ""):
        """左栏选中：全部 / 档位 / 具体项（按当前维度解释）。"""
        if self._group_dimension == "capacity":
            if kind == "all":
                self._active_band = None
                self._active_capacity = None
            elif kind == "group":
                self._active_band = key
                self._active_capacity = None
            else:
                self._active_band = group or None
                self._active_capacity = key
            self._apply_filter()
            return
        if kind == "all":
            self._active_series = "全部"
            self._active_brand = None
        elif kind == "group":
            self._active_brand = key
            self._active_series = "全部"
        else:
            self._active_series = key
            # 企微来源的「系列」就是月份，同一个月会散在多个大类下，
            # 选中时要把大类一起带上，否则会把别的大类的同月图也捞出来。
            self._active_brand = group or None
        self._apply_filter()

    def _on_rail_search(self, text: str):
        """左栏搜索框与顶栏搜索框同步 —— 否则在左栏输入，右侧网格毫无反应。"""
        if self.search.text() != text:
            self.search.setText(text)      # setText 会触发 _apply_filter
        else:
            self._apply_filter()

    def set_kind_filter(self, key: str):
        self._kind_filter = key
        for name, button in self.kind_buttons.items():
            button.setChecked(name == key)
        self._apply_filter()

    def set_alpha_only(self, enabled: bool):
        """只看透明底：纯界面层过滤，不重扫索引，切换是瞬时的。"""
        self._alpha_only = enabled
        self.alpha_check.setChecked(enabled)
        try:
            self.repository.set_setting("product_alpha_only", "1" if enabled else "0")
        except Exception:  # noqa: BLE001
            pass
        self._apply_filter()

    def set_backdrop_mode(self, key: str):
        self.backdrop_mode = key
        for name, button in self.backdrop_buttons.items():
            button.setChecked(name == key)
        self.stage.set_mode(key)
        self.grid.viewport().update()

    def toggle_inspector(self):
        self._inspector_open = not self._inspector_open
        self.inspector.setVisible(self._inspector_open)
        self.inspector_button.setChecked(self._inspector_open)
        if self._inspector_open:
            shots = self._selected_shots()
            if shots:
                self._show_inspector(shots[0])
        self._resize_timer.start()

    def _on_density_changed(self, value: int):
        self._columns = value
        self.density_label.setText(f"{value} 列")
        self._apply_density()
        try:
            self.repository.set_setting("product_columns", str(value))
        except Exception:  # noqa: BLE001
            pass

    def _apply_density(self):
        """保持产品卡片可读；窄窗口自动少排几列。"""
        viewport = max(200, self.grid.viewport().width())
        gap = 8
        # QListView 的实际换行宽度比 viewport 小；留出布局与滚动条余量。
        usable = max(180, viewport - 80)
        columns = max(1, min(self._columns, usable // (MIN_CARD_WIDTH + gap)))
        self._effective_columns = columns
        cell = max(MIN_CARD_WIDTH, (usable - gap * columns) // columns)
        self._set_cell_size(cell, gap)
        self.grid.doItemsLayout()
        for _ in range(5):
            if self.grid.count() < columns or self._row_capacity() >= columns:
                break
            cell = max(MIN_CARD_WIDTH, cell - 6)
            self._set_cell_size(cell, gap)
            self.grid.doItemsLayout()
        self.density_label.setText(f"{columns} 列")
        self.density_label.setToolTip(
            f"偏好 {self._columns} 列；当前窗口可读宽度显示 {columns} 列"
        )

    def _set_cell_size(self, cell: int, gap: int):
        item_height = cell + META_HEIGHT
        self.grid.setGridSize(QSize(cell + gap, item_height + gap))
        side = max(32, cell - 2 * CARD_MARGIN - 2 * THUMB_PAD)
        self.grid.setIconSize(QSize(side, side))
        self.grid.setUpdatesEnabled(False)
        try:
            for row in range(self.grid.count()):
                item = self.grid.item(row)
                if item is not None:
                    item.setSizeHint(QSize(cell, item_height))
        finally:
            self.grid.setUpdatesEnabled(True)

    def _row_capacity(self) -> int:
        """当前实际一行能放几张。"""
        grid = self.grid
        if not grid.count():
            return 0
        first = grid.visualItemRect(grid.item(0))
        count = 1
        for index in range(1, min(grid.count(), 12)):
            rect = grid.visualItemRect(grid.item(index))
            if rect.y() != first.y():
                break
            count += 1
        return count

    def _apply_filter(self):
        keyword = self.search.text().strip().lower()
        series = self._active_series
        brand = self._active_brand
        kind = self._kind_filter
        out: list[ProductShot] = []
        hidden_opaque = 0
        for shot in self._shots:
            if self._group_dimension == "capacity":
                if self._active_band and shot.capacity_band != self._active_band:
                    continue
                if self._active_capacity:
                    # 「未标注容量」那组的子项是大类，其余是具体容量
                    if self._active_band == CAPACITY_UNKNOWN:
                        if shot.brand != self._active_capacity:
                            continue
                    elif shot.capacity_label != self._active_capacity:
                        continue
            elif series != "全部":
                if shot.series != series:
                    continue
                if brand and shot.brand != brand:
                    continue
            elif brand and shot.brand != brand:
                continue
            if kind != KIND_ALL and shot.kind != kind:
                continue
            if keyword:
                haystack = " ".join((shot.name, shot.capacity, shot.degrees,
                                     shot.series)).lower()
                if keyword not in haystack:
                    continue
            # 透明底判定放在最后：这样「已隐藏 N 张非透明底」说的是
            # 当前这个筛选范围内藏了几张，而不是整个来源的总数 —— 否则
            # 点了某个系列还会显示别的系列的隐藏数，对不上。
            if self._alpha_only and not shot.has_alpha:
                hidden_opaque += 1
                continue
            out.append(shot)
        # 显示顺序统一在这里定：时间新的在上
        out.sort(key=time_sort_key)
        self._filtered = out
        self._hidden_opaque = hidden_opaque
        if self._group_dimension == "capacity":
            raw = self._active_capacity or self._active_band or "全部产品"
        else:
            raw = series if series != "全部" else (brand or "全部产品")
        # 标题固定 130px 宽（顶栏防挤压，见 _build_center 的注释），超长手动打省略号
        metrics = self.series_label.fontMetrics()
        self.series_label.setText(metrics.elidedText(
            raw, Qt.TextElideMode.ElideRight, self.series_label.width() - 4))
        self._populate(out)

    def _populate(self, shots: list[ProductShot]):
        self._thumb_epoch += 1
        self._pending_thumbs.clear()
        self._apply_timer.stop()
        self._pending_schedule.clear()
        self._thumb_queued_rows.clear()
        self.grid.clear()
        self.grid.setUpdatesEnabled(False)
        try:
            for row, shot in enumerate(shots):
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, shot.path)
                item.setData(Qt.ItemDataRole.UserRole + 1, CardMeta(
                    name=Path(shot.name).stem,
                    capacity=shot.capacity,
                    resolution=shot.resolution,
                    size_text=_fmt_size(shot.size),
                    date_text=_fmt_time(shot.mtime),
                    format_label=shot.format_label,
                    has_alpha=shot.has_alpha))
                item.setToolTip(f"{shot.name}\n{shot.series}　·　{_fmt_size(shot.size)}\n"
                                f"{shot.resolution} px　·　{_fmt_time(shot.mtime)}\n{shot.path}")
                item.setIcon(painted_icon("image", S._TEXT_TERTIARY, 40))
                self.grid.addItem(item)
                self._pending_schedule.append((shot, row))
        finally:
            self.grid.setUpdatesEnabled(True)
        self._apply_density()
        total = len(shots)
        # 计数胶囊固定 100px 宽（顶栏防挤压，见 _build_center 的注释）。
        # 「已隐藏 N 张非透明底」的提示放在网格标题行——那行空间充裕，
        # 且这是「只看透明底」过滤的直接后果，用户需要看到它。
        self.filtered_label.setText(f"共 {total} 款")
        self.count_label.setText(f"{len(self._shots)} 张")
        hidden = getattr(self, "_hidden_opaque", 0)
        hidden_suffix = f"　·　已隐藏 {hidden} 张非透明底" if hidden else ""
        self.status.setText((f"{self._active_series}　→　{total} 张" if self._active_series != "全部"
                             else f"共 {total} 张产品图") + hidden_suffix)
        if total == 0:
            if not self._shots:
                self.empty_state.set_content(
                    "建立你的产品素材库",
                    "导入产品图片或整个文件夹，按系列管理，并自动检查透明底与分辨率。",
                    "添加产品",
                )
            else:
                self.empty_state.set_content(
                    "没有找到匹配的产品",
                    "调整搜索词或系列筛选，也可以一键清除当前筛选条件。",
                    "清除筛选",
                )
        self.grid.setVisible(total > 0)
        self.empty_state.setVisible(total == 0)
        self._schedule_batch()

    # ══ 缩略图（渐进加载 + 批量刷） ══
    def _schedule_batch(self, count: int = THUMBNAIL_BATCH):
        while count > 0 and self._pending_schedule:
            shot, row = self._pending_schedule.popleft()
            if row in self._thumb_queued_rows:
                continue  # 已经被可见区域优先加载过了，别重复解码
            self._thumb_queued_rows.add(row)
            self._dispatch_thumb(shot, row)
            count -= 1

    def _on_grid_scrolled(self, value: int):
        """滚动就按可见区域补加载。

        旧逻辑是「滚过 60% 才加载下一批」，而一批只有 THUMBNAIL_BATCH(120) 张——
        几千张图时，60% 之前的一大片区域滑过去永远是空白占位图。
        """
        self._prioritize_visible()

    def _visible_row_range(self) -> tuple[int, int]:
        """当前视口覆盖的一维条目索引范围 (first_idx, last_idx)，往下多带一段缓冲。

        注意：bar.value() // row_h 算出的是「视觉行号（第几排）」，必须乘以实际列数。
        换算成 QListWidget 的一维条目下标，否则多列网格下后 75% 的条目永远算不到。
        """
        count = self.grid.count()
        if count == 0:
            return 0, -1
        cols = max(1, self._effective_columns)
        bar = self.grid.verticalScrollBar()
        row_h = self.grid.gridSize().height()
        if bar is None or row_h <= 0:
            return 0, min(count - 1, THUMB_LOOKAHEAD_ROWS)
        viewport_h = self.grid.viewport().height() or row_h * 3
        first_visual_row = max(0, int(bar.value() // row_h))
        last_visual_row = int((bar.value() + viewport_h) // row_h) + 1
        first_idx = first_visual_row * cols
        last_idx = min(count - 1, last_visual_row * cols + THUMB_LOOKAHEAD_ROWS)
        return first_idx, last_idx

    def _prioritize_visible(self) -> None:
        """可见区域的图**立即派发**，不走 _pending_schedule 那条顺序队列。

        _pending_schedule 初始装的是全部行，_schedule_batch 只会从队首取；
        若把可见行 appendleft 进去，队尾那几千行仍然轮不到。所以这里直接派发。
        """
        first, last = self._visible_row_range()
        if last < first:
            return
        dispatched = 0
        for row in range(first, last + 1):
            if row in self._thumb_queued_rows:
                continue
            shot = self._shot_at(row)
            if shot is None:
                continue
            self._thumb_queued_rows.add(row)
            self._dispatch_thumb(shot, row)
            dispatched += 1
        # 可见区域补完后，顺手让顺序队列往前推进一点，滚回去时前后都有图
        if not dispatched:
            self._schedule_batch(count=min(THUMBNAIL_BATCH, 40))

    def _dispatch_thumb(self, shot: ProductShot, row: int) -> None:
        """派发一张缩略图的解码任务。"""
        epoch = self._thumb_epoch
        worker = ThumbWorker(shot.path, token=("thumb", row, epoch),
                             should_run=lambda e=epoch: self._thumb_epoch == e)
        worker.signals.done.connect(self._on_thumb_done)
        # 巨图（1 亿像素级）解码要几百 MB，串行池一次只跑一张
        pool = self._render_pool if shot.width * shot.height > GIANT_PIXELS else self._image_pool
        pool.start(worker)

    def _on_thumb_done(self, token, path: str, image):
        kind, row, epoch = token
        if kind != "thumb" or epoch != self._thumb_epoch:
            return
        if image is None or image.isNull():
            return
        self._pending_thumbs[row] = (path, image)
        if not self._apply_timer.isActive():
            self._apply_timer.start()

    def _flush_pending_thumbs(self):
        if not self._pending_thumbs:
            self._apply_timer.stop()
            return
        self.grid.setUpdatesEnabled(False)
        try:
            for row in sorted(self._pending_thumbs):
                path, image = self._pending_thumbs.pop(row)
                if row >= self.grid.count():
                    continue
                item = self.grid.item(row)
                if item is None or item.data(Qt.ItemDataRole.UserRole) != path:
                    continue
                item.setIcon(QIcon(QPixmap.fromImage(image)))
        except RuntimeError:
            return
        finally:
            self.grid.setUpdatesEnabled(True)
        if self._pending_thumbs:
            self._apply_timer.start()
        else:
            self._apply_timer.stop()

    # ══ 选中与动作 ══
    def _shot_at(self, row: int) -> ProductShot | None:
        if 0 <= row < len(self._filtered):
            return self._filtered[row]
        if row < 0 or row >= self.grid.count():
            return None
        item = self.grid.item(row)
        if item is None:
            return None
        path = item.data(Qt.ItemDataRole.UserRole)
        return self._by_path.get(path)

    def _selected_shots(self) -> list[ProductShot]:
        out = []
        for item in self.grid.selectedItems():
            path = item.data(Qt.ItemDataRole.UserRole)
            hit = self._by_path.get(path) or next((s for s in self._filtered if s.path == path), None)
            if hit:
                out.append(hit)
        return out

    def _on_selection_changed(self):
        shots = self._selected_shots()
        if not shots:
            return
        shot = shots[0]
        more = f"　等 {len(shots)} 张" if len(shots) > 1 else ""
        self.status.setText(
            f"{shot.name}{more}　·　{shot.series}　·　{_fmt_size(shot.size)}　·　"
            f"{shot.resolution} px　·　{_fmt_time(shot.mtime)}")
        if self._inspector_open:
            self._show_inspector(shot)
        elif self.inspector.isVisible():
            self._show_inspector(shot)

    def _show_inspector(self, shot: ProductShot):
        self._current_shot = shot
        self.inspector_name.setText(shot.name)
        self.inspector_footer.setText(f"{shot.series}　·　{shot.brand}")
        self.spec_rows["分辨率"].setText(
            f"{shot.resolution} px" + ("　(1:1)" if shot.is_square_800 else ""))
        self.spec_rows["文件大小"].setText(_fmt_size(shot.size))
        self.spec_rows["更新时间"].setText(_fmt_time(shot.mtime))
        self.spec_rows["格式"].setText(shot.format_label)
        self.spec_rows["透明通道"].setText("校验中…")
        _fill_tags(self.tags_layout, asset_tags(shot))

        # 大图（比网格缩略图大得多，单独缓存 tag，别互相覆盖）
        self._alpha_epoch += 1
        epoch = self._alpha_epoch
        self.stage.set_image(None)
        worker = ThumbWorker(shot.path, token=("hi", epoch), box=HI_RENDER, tag="prodhi",
                             should_run=lambda e=epoch: self._alpha_epoch == e)
        worker.signals.done.connect(self._on_hi_done)
        self._render_pool.start(worker)

        # Alpha 校验也丢后台：巨图光解码就要几秒，放界面线程会卡住整页
        alpha_worker = AlphaWorker(shot.path, token=("alpha", epoch),
                                   should_run=lambda e=epoch: self._alpha_epoch == e)
        alpha_worker.signals.done.connect(self._on_alpha_done)
        self._render_pool.start(alpha_worker)

    def _on_alpha_done(self, token, path: str, payload):
        kind, epoch = token
        if kind != "alpha" or epoch != self._alpha_epoch:
            return  # 已经换了选择
        fmt, alpha_text, ok = payload
        self._last_alpha_ok = ok
        self.spec_rows["格式"].setText(fmt)
        self.spec_rows["透明通道"].setText(alpha_text)
        self.spec_rows["透明通道"].setStyleSheet(
            f"color: {S._SUCCESS if ok else S._DANGER}; font-size: 11px; font-weight: 600; background: transparent;")
        shot = self._current_shot
        if shot is not None:
            _fill_tags(self.tags_layout, asset_tags(shot, alpha_ok=ok))

    def _on_hi_done(self, token, path: str, image):
        kind, epoch = token
        if kind != "hi" or epoch != self._alpha_epoch:
            return  # 用户已经换了选择，这次结果作废
        if image is None or image.isNull():
            return
        try:
            self.stage.set_image(image)
        except RuntimeError:
            return  # 面板已销毁

    def _copy_row(self, row: int):
        shot = self._shot_at(row)
        if shot is None:
            return
        self.grid.clearSelection()
        item = self.grid.item(row)
        if item is not None:
            item.setSelected(True)
        if _copy_image_to_clipboard([shot.path]):
            self._flash(f"已复制【{shot.name}】免抠 PNG，可直接在 PS 里 Ctrl+V")

    def _open_row(self, row: int):
        shot = self._shot_at(row)
        if shot is not None:
            open_file(shot.path)

    def _open_selected(self):
        for shot in self._selected_shots()[:5]:
            open_file(shot.path)

    def _copy_selected_paths(self):
        shots = self._selected_shots()
        if shots:
            QApplication.clipboard().setText("\n".join(s.path for s in shots))
            self._flash(f"已复制 {len(shots)} 个路径")

    def _copy_selected_images(self):
        shots = self._selected_shots()
        if not shots:
            return
        if _copy_image_to_clipboard([s.path for s in shots]):
            self._flash(f"已复制 {min(len(shots), 8)} 张免抠 PNG（PS 里 Ctrl+V）")

    def _reveal_selected(self):
        shots = self._selected_shots()
        if shots:
            reveal_file(shots[0].path)

    def _open_series_folder(self):
        """打开当前系列 / 月份的真实目录（不同来源的目录结构不一样，按选中项反推）。"""
        target: Path | None = None
        shots = self._selected_shots() or self._filtered
        if shots and self._active_series != "全部":
            target = Path(shots[0].path).parent
        if target is None:
            source = self._current_source()
            if source.key == "library":
                target = self.root
            elif shots:
                target = Path(shots[0].path).parent
        if target is None:
            return
        try:
            os.startfile(str(target))  # noqa: S606
        except Exception:
            reveal_file(str(target))

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            paths = [u.toLocalFile() for u in event.mimeData().urls() if u.toLocalFile()]
            if paths:
                event.acceptProposedAction()
                self.open_add_product_dialog(paths)
                return
        super().dropEvent(event)

    def ensure_writable_library_root(self) -> Path:
        """确保产品库根目录可写：若默认 G 盘不存在，则自动在用户文档下初始化「视觉产品库PNG」。"""
        self.root = self._resolve_library_root()
        try:
            anchor = Path(self.root.anchor) if self.root.anchor else self.root.parent
            if self.root.exists() or anchor.exists():
                self.root.mkdir(parents=True, exist_ok=True)
                return self.root
        except OSError:
            pass
        docs = user_known_folders().get("Documents", Path.home() / "Documents")
        fallback = docs / "视觉产品库PNG"
        fallback.mkdir(parents=True, exist_ok=True)
        self.root = fallback
        try:
            self.repository.set_setting("product_library_root", str(fallback))
            self.repository.set_product_folder(str(fallback))
        except Exception:
            pass
        return self.root

    def existing_series_names(self) -> list[str]:
        """返回当前产品库下已有的全部产品系列名称（合并磁盘子目录、内存索引与常用默认系列）。"""
        names: list[str] = []
        seen: set[str] = set()
        if self.root.is_dir():
            try:
                for entry in sorted(self.root.iterdir(), key=lambda p: p.name.lower()):
                    if entry.is_dir() and not entry.name.startswith("."):
                        names.append(entry.name)
                        seen.add(entry.name)
            except OSError:
                pass
        for shot in self._shots:
            if shot.series and shot.series != "（根目录）" and shot.series not in seen:
                names.append(shot.series)
                seen.add(shot.series)
        for starter in STARTER_SERIES:
            if starter not in seen:
                names.append(starter)
                seen.add(starter)
        return names

    def open_add_product_dialog(
        self,
        initial_paths: list[str] | None = None,
        default_series: str | None = None,
    ) -> list[ProductShot]:
        """打开《添加产品入库》对话框（可从产品库顶栏、拖拽或工作台首页启动台调用）。"""
        series_hint = default_series
        if not series_hint and self._active_series and self._active_series != "全部":
            series_hint = self._active_series
        dlg = AddProductDialog(
            self,
            initial_paths=initial_paths,
            default_series=series_hint or "",
            parent=self.window() or self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            return dlg.imported_shots
        return []

    def import_product_files(
        self,
        source_paths: list[str | Path],
        series_name: str,
        *,
        brand_override: str | None = None,
        custom_name_or_prefix: str = "",
        move_files: bool = False,
    ) -> list[ProductShot]:
        """将一批产品图片导入产品库指定系列文件夹，并 O(1) 增量刷新内存索引与界面。"""
        valid_sources: list[Path] = []
        for raw in source_paths:
            p = Path(raw)
            if p.is_dir():
                for sub in sorted(p.rglob("*")):
                    if sub.is_file() and sub.suffix.lower() in IMAGE_SUFFIXES:
                        valid_sources.append(sub)
            elif p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES:
                valid_sources.append(p)
        if not valid_sources:
            return []

        lib_root = self.ensure_writable_library_root()
        clean_series = organizer.sanitize_name(series_name) if hasattr(organizer, "sanitize_name") else (
            "".join(ch for ch in (series_name or "").strip() if ch not in '\\/:*?"<>|').strip()
        )
        if not clean_series or clean_series in ("全部", "（根目录）"):
            clean_series = "默认产品系列"

        if brand_override and brand_override in GROUP_ORDER:
            set_custom_series_group(clean_series, brand_override)
            try:
                self.repository.set_setting("custom_series_groups", dump_custom_series_groups())
            except Exception:
                pass

        series_dir = lib_root / clean_series
        series_dir.mkdir(parents=True, exist_ok=True)

        probe = SizeProbe("library")
        verifier = pixel_verifier()
        imported: list[ProductShot] = []
        single_mode = len(valid_sources) == 1
        custom_text = "".join(
            ch for ch in (custom_name_or_prefix or "").strip() if ch not in '\\/:*?"<>|'
        ).strip()

        for src in valid_sources:
            suffix = src.suffix.lower()
            if single_mode and custom_text:
                stem = Path(custom_text).stem if custom_text.lower().endswith(IMAGE_SUFFIXES) else custom_text
                target_name = f"{stem}{suffix}"
            elif not single_mode and custom_text:
                target_name = f"{custom_text}{src.stem}{suffix}"
            else:
                target_name = src.name

            dst = series_dir / target_name
            try:
                same_file = dst.exists() and src.resolve() == dst.resolve()
            except OSError:
                same_file = False

            if not same_file:
                dst = organizer.unique_dest(dst)
                if move_files:
                    shutil.move(str(src), str(dst))
                else:
                    shutil.copy2(str(src), str(dst))

            try:
                # 刷新文件修改时间为当前入库时间，使新入库的产品排在最前面
                now_ts = time.time()
                os.utime(dst, (now_ts, now_ts))
                stat = dst.stat()
            except OSError:
                continue

            width, height, header_alpha = probe.info_of(dst, stat)
            if header_alpha:
                real_alpha = judge_transparent(analyze_alpha(dst))
            else:
                real_alpha = False
            verifier.record(dst, stat.st_size, int(stat.st_mtime_ns), real_alpha)

            shot = ProductShot(
                path=str(dst),
                name=dst.name,
                series=clean_series,
                brand=group_for_series(clean_series),
                size=stat.st_size,
                mtime=stat.st_mtime,
                width=width,
                height=height,
                has_alpha=real_alpha,
                capacity_ml=parse_capacity_ml(dst.name),
            )
            imported.append(shot)

        probe.commit()
        verifier.commit()

        if not imported:
            return []

        # 若产品库位于资产库根目录下，同步写入 SQLite files 表以联动首页计数与最近更新
        try:
            from ..classifier import classify_extension
            from ..models import FileRecord
            from ..scanner import file_signature

            asset_root = self.repository.get_root_directory()
            root_base = Path(asset_root).resolve() if asset_root and Path(asset_root).exists() else lib_root.resolve()
            records: list[FileRecord] = []
            scan_now = time.time()
            for shot in imported:
                p = Path(shot.path).resolve()
                try:
                    rel = str(p.relative_to(root_base))
                except ValueError:
                    rel = f"{clean_series}/{p.name}"
                records.append(FileRecord(
                    absolute_path=str(p),
                    relative_path=rel,
                    file_name=p.name,
                    extension=p.suffix.lower(),
                    category=classify_extension(p.suffix),
                    size_bytes=shot.size,
                    modified_at=shot.mtime,
                    last_scanned_at=scan_now,
                    content_hash=file_signature(p, shot.size),
                ))
            self.repository.upsert_files(records)
        except Exception:
            pass

        # 切换到产品库来源并增量合并到内存索引
        if self._source_key != "library":
            self._source_key = "library"
            idx = self.source_combo.findData("library")
            if idx >= 0:
                self.source_combo.blockSignals(True)
                self.source_combo.setCurrentIndex(idx)
                self.source_combo.blockSignals(False)

        new_paths = {s.path for s in imported}
        existing_lib = [s for s in self._source_cache.get("library", self._shots) if s.path not in new_paths]
        merged = sorted(imported + existing_lib, key=time_sort_key)
        self._source_cache["library"] = merged
        self._source_counts["library"] = len(merged)
        self._shots = merged
        self._by_path = {s.path: s for s in merged}

        # 如果导入的产品含非透明底图片（如白底 JPG），自动关闭「只看透明底」以免用户看不到刚导入的图
        has_opaque = any(not s.has_alpha for s in imported)
        if has_opaque and self._alpha_only:
            self._alpha_only = False
            self.alpha_check.blockSignals(True)
            self.alpha_check.setChecked(False)
            self.alpha_check.blockSignals(False)
            try:
                self.repository.set_setting("product_alpha_only", "0")
            except Exception:
                pass

        # 自动定位到刚入库的系列并刷新网格
        self._group_dimension = "series"
        self.rail.set_dimension("series")
        self._active_series = clean_series
        self._active_brand = group_for_series(clean_series)
        self._refresh_rail()
        self._apply_filter()
        self._update_source_labels(len(self._shots))

        # 在网格中高亮选中新添加的产品
        self.grid.clearSelection()
        first_item = None
        for row in range(self.grid.count()):
            item = self.grid.item(row)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) in new_paths:
                item.setSelected(True)
                if first_item is None:
                    first_item = item
        if first_item is not None:
            self.grid.scrollToItem(first_item, QAbstractItemView.ScrollHint.PositionAtTop)

        if len(imported) == 1:
            self._show_inspector(imported[0])

        toast_msg = f"已成功添加 {len(imported)} 款产品到「{clean_series}」"
        if has_opaque:
            toast_msg += "（含非透明底图，已自动显示全部）"
        self._flash(toast_msg)
        self.productsAdded.emit(len(imported))
        return imported

    def _rename_selected_product(self):
        shots = self._selected_shots()
        if len(shots) != 1:
            return
        shot = shots[0]
        src = Path(shot.path)
        if not src.exists():
            return
        new_stem, ok = QInputDialog.getText(
            self, "重命名产品", "输入新的产品名称（建议含容量/度数规格）：", text=src.stem
        )
        if not ok or not new_stem.strip():
            return
        clean_stem = "".join(ch for ch in new_stem.strip() if ch not in '\\/:*?"<>|').strip()
        if not clean_stem or clean_stem == src.stem:
            return
        dst = organizer.unique_dest(src.with_name(f"{clean_stem}{src.suffix}"))
        try:
            src.rename(dst)
            stat = dst.stat()
        except OSError as exc:
            QMessageBox.warning(self, "重命名失败", str(exc))
            return
        updated = ProductShot(
            path=str(dst),
            name=dst.name,
            series=shot.series,
            brand=shot.brand,
            size=stat.st_size,
            mtime=stat.st_mtime,
            width=shot.width,
            height=shot.height,
            has_alpha=shot.has_alpha,
            capacity_ml=parse_capacity_ml(dst.name),
        )
        self._shots = [updated if s.path == shot.path else s for s in self._shots]
        if self._source_key in self._source_cache:
            self._source_cache[self._source_key] = self._shots
        self._by_path.pop(shot.path, None)
        self._by_path[updated.path] = updated
        self._refresh_rail()
        self._apply_filter()
        self._flash(f"已重命名为「{dst.name}」")

    def _move_selected_to_series(self):
        shots = self._selected_shots()
        if not shots:
            return
        series_list = self.existing_series_names()
        target_series, ok = QInputDialog.getItem(
            self, "移至其他系列", "选择或输入目标产品系列名称：", series_list, 0, True
        )
        if not ok or not target_series.strip():
            return
        clean_series = "".join(ch for ch in target_series.strip() if ch not in '\\/:*?"<>|').strip()
        if not clean_series:
            return
        lib_root = self.ensure_writable_library_root()
        target_dir = lib_root / clean_series
        target_dir.mkdir(parents=True, exist_ok=True)
        moved_map: dict[str, ProductShot] = {}
        for shot in shots:
            src = Path(shot.path)
            if not src.exists():
                continue
            dst = organizer.unique_dest(target_dir / src.name)
            try:
                shutil.move(str(src), str(dst))
                stat = dst.stat()
                moved_map[shot.path] = ProductShot(
                    path=str(dst),
                    name=dst.name,
                    series=clean_series,
                    brand=group_for_series(clean_series),
                    size=stat.st_size,
                    mtime=stat.st_mtime,
                    width=shot.width,
                    height=shot.height,
                    has_alpha=shot.has_alpha,
                    capacity_ml=shot.capacity_ml,
                )
            except OSError:
                continue
        if moved_map:
            self._shots = [moved_map.get(s.path, s) for s in self._shots]
            if self._source_key in self._source_cache:
                self._source_cache[self._source_key] = self._shots
            self._by_path = {s.path: s for s in self._shots}
            self._refresh_rail()
            self._apply_filter()
            self._flash(f"已将 {len(moved_map)} 款产品移至「{clean_series}」")

    def _remove_selected_products(self):
        shots = self._selected_shots()
        if not shots:
            return
        reply = QMessageBox.question(
            self,
            "从产品库移除",
            f"确定要从产品库文件夹中移除选中的 {len(shots)} 款产品吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        removed_paths: set[str] = set()
        for shot in shots:
            try:
                Path(shot.path).unlink(missing_ok=True)
                removed_paths.add(shot.path)
            except OSError:
                continue
        if removed_paths:
            self._shots = [s for s in self._shots if s.path not in removed_paths]
            if self._source_key in self._source_cache:
                self._source_cache[self._source_key] = self._shots
            for p in removed_paths:
                self._by_path.pop(p, None)
            self._refresh_rail()
            self._apply_filter()
            self._update_source_labels(len(self._shots))
            self._flash(f"已移除 {len(removed_paths)} 款产品")

    def _show_context_menu(self, position):
        item = self.grid.itemAt(position)
        menu = QMenu(self)
        actions: dict[object, str] = {}
        if item is None:
            actions[menu.addAction("＋ 添加产品入库…")] = "add_product"
            actions[menu.addAction("打开产品库文件夹")] = "open_folder"
            chosen = menu.exec(self.grid.viewport().mapToGlobal(position))
            act = actions.get(chosen)
            if act == "add_product":
                self.open_add_product_dialog()
            elif act == "open_folder":
                self._open_series_folder()
            return

        if not item.isSelected():
            self.grid.clearSelection()
            item.setSelected(True)
        shots = self._selected_shots()
        if not shots:
            return
        single = len(shots) == 1
        actions[menu.addAction("复制图片（PS 里直接粘贴）")] = "copy_image"
        if single:
            actions[menu.addAction("打开")] = "open"
            actions[menu.addAction("在资源管理器中定位")] = "reveal"
        menu.addSeparator()
        actions[menu.addAction("＋ 添加新产品到此系列…")] = "add_product"
        if single and self._source_key == "library":
            actions[menu.addAction("重命名此产品…")] = "rename"
        if self._source_key == "library":
            actions[menu.addAction("移至其他系列…")] = "move_series"
            actions[menu.addAction("从产品库移除…")] = "remove"
        menu.addSeparator()
        actions[menu.addAction("复制完整路径" if single else f"复制 {len(shots)} 个路径")] = "copy_path"
        actions[menu.addAction("打开系列文件夹")] = "open_folder"
        chosen = menu.exec(self.grid.viewport().mapToGlobal(position))
        act = actions.get(chosen)
        if act == "copy_image":
            self._copy_selected_images()
        elif act == "open":
            self._open_selected()
        elif act == "reveal":
            self._reveal_selected()
        elif act == "add_product":
            self.open_add_product_dialog(default_series=shots[0].series)
        elif act == "rename":
            self._rename_selected_product()
        elif act == "move_series":
            self._move_selected_to_series()
        elif act == "remove":
            self._remove_selected_products()
        elif act == "copy_path":
            self._copy_selected_paths()
        elif act == "open_folder":
            self._open_series_folder()

    def _flash(self, text: str):
        window = self.window()
        if hasattr(window, "show_toast"):
            window.show_toast(text)
        else:
            self.status.setText(text)

    # ══ 主题 ══
    def refresh_theme(self):
        self.empty_state.refresh_theme()
        self.rail.refresh_theme()
        self.grid.itemDelegate().refresh_theme()
        self.grid.setStyleSheet(grid_qss())
        self.stage.refresh_theme()
        if hasattr(self, "_density_icon"):
            self._density_icon.setPixmap(painted_icon("grid", S._TEXT_TERTIARY, 13).pixmap(13, 13))
        if hasattr(self, "inspector_button"):
            self.inspector_button.setIcon(painted_icon("sliders", S._TEXT_SECONDARY, 15))
        if hasattr(self, "_inspector_close"):
            self._inspector_close.setIcon(painted_icon("close", S._TEXT_TERTIARY, 14))
        if hasattr(self, "reveal_button"):
            self.reveal_button.setIcon(painted_icon("external", S._TEXT_SECONDARY, 15))
        if hasattr(self, "_last_alpha_ok") and "透明通道" in self.spec_rows:
            ok = self._last_alpha_ok
            self.spec_rows["透明通道"].setStyleSheet(
                f"color: {S._SUCCESS if ok else S._DANGER}; font-size: 11px; font-weight: 600; background: transparent;"
            )
        self.grid.viewport().update()


def _copy_image_to_clipboard(paths: list[str]) -> bool:
    """把图片放进剪贴板：同时给 imageData 和文件 URL，PS / 资源管理器都能接。"""
    from PySide6.QtCore import QMimeData

    data = QMimeData()
    for path in paths[:8]:
        image = QImage(path)
        if not image.isNull():
            data.setImageData(image)
            break
    if not data.hasImage():
        return False
    data.setUrls([QUrl.fromLocalFile(p) for p in paths])
    QApplication.clipboard().setMimeData(data)
    return True


def _fill_tags(layout, tags: list[str]):
    """清空并重填标签（支持真正的多行流式折行，在 272px 检视面板里不挤字）。"""
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
    for text in tags:
        chip = QLabel(text)
        chip.setObjectName("TagChip")
        layout.addWidget(chip)
    if layout.parentWidget() is not None:
        layout.parentWidget().updateGeometry()


class _FlowLayout(QLayout):
    """真正支持自动折行的流式布局：解决右侧 272px 检视面板里 6~8 个资产标签挤在一行被截断的问题。"""

    def __init__(self, parent=None, margin: int = 0, h_spacing: int = 6, v_spacing: int = 6):
        super().__init__(parent)
        self.setContentsMargins(margin, margin, margin, margin)
        self._h_space = h_spacing
        self._v_space = v_spacing
        self._items: list = []

    def __del__(self):
        item = self.takeAt(0)
        while item:
            item = self.takeAt(0)

    def addItem(self, item):  # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int):  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect):  # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        left, top, right, bottom = self.getContentsMargins()
        size += QSize(left + right, top + bottom)
        parent = self.parentWidget()
        if parent is not None and parent.width() > 0:
            h = self.heightForWidth(parent.width())
            size.setHeight(max(size.height(), h))
        return size

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        left, top, right, bottom = self.getContentsMargins()
        effective = rect.adjusted(left, top, -right, -bottom)
        x = effective.x()
        y = effective.y()
        line_height = 0

        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + self._h_space
            if next_x - self._h_space > effective.right() and line_height > 0:
                x = effective.x()
                y = y + line_height + self._v_space
                next_x = x + hint.width() + self._h_space
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())

        return y + line_height - rect.y() + bottom



class _ProductDropZone(QFrame):
    """添加产品弹窗内的拖拽区：支持点击选图或直接拖入图片/文件夹。"""

    filesDropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(86)
        self._drag_active = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(4)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel("将免抠 PNG / 产品图拖入此处，或点击选择图片文件")
        title.setObjectName("DropZoneTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)

        hint = QLabel("支持 PNG · JPG · WebP  |  可一次选择多张图片或整个产品文件夹，入库时自动检测透明底与分辨率")
        hint.setObjectName("HintLabel")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(hint)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            files, _ = QFileDialog.getOpenFileNames(
                self, "选择要入库的产品图片", "", "产品图片 (*.png *.jpg *.jpeg *.webp)"
            )
            if files:
                self.filesDropped.emit(files)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._drag_active = True
            self.update()

    def dragLeaveEvent(self, event):
        self._drag_active = False
        self.update()

    def dropEvent(self, event):
        self._drag_active = False
        self.update()
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.toLocalFile()]
        if paths:
            event.acceptProposedAction()
            self.filesDropped.emit(paths)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._drag_active:
            painter.setBrush(_to_qcolor(S._ACCENT_LIGHT))
            pen = QPen(_to_qcolor(S._ACCENT), 1.6, Qt.PenStyle.DashLine)
        else:
            painter.setBrush(_to_qcolor(S._SURFACE_2))
            pen = QPen(_to_qcolor(S._BORDER_STRONG), 1.2, Qt.PenStyle.DashLine)
        pen.setDashPattern([5, 4])
        painter.setPen(pen)
        path = QPainterPath()
        path.addRoundedRect(1.0, 1.0, self.width() - 2.0, self.height() - 2.0, 12, 12)
        painter.drawPath(path)
        painter.end()


class AddProductDialog(QDialog):
    """自主添加产品入库对话框（支持单张/批量/文件夹导入、新建系列、指定四大分类与透明底实时核查）。"""

    def __init__(
        self,
        product_page: ProductPage,
        initial_paths: list[str] | None = None,
        default_series: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.product_page = product_page
        self._paths: list[str] = []
        self.imported_shots: list[ProductShot] = []

        self.setWindowTitle("添加产品入库 · 产品库")
        self.setMinimumSize(660, 560)
        self.resize(700, 600)
        self.setAcceptDrops(True)

        self._build_ui(default_series)
        if initial_paths:
            self.add_paths(initial_paths)

    def _build_ui(self, default_series: str):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        # 1. 顶部标题与目标库路径
        head = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title = QLabel("添加产品到产品库")
        title.setObjectName("SectionTitle")
        title_col.addWidget(title)
        lib_root = self.product_page.ensure_writable_library_root()
        self.root_info_label = QLabel(f"目标产品库：{lib_root}")
        self.root_info_label.setObjectName("HintLabel")
        title_col.addWidget(self.root_info_label)
        head.addLayout(title_col, 1)

        change_root_btn = QPushButton("更换库目录…")
        change_root_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        change_root_btn.setFixedHeight(28)
        change_root_btn.clicked.connect(self._choose_library_root)
        head.addWidget(change_root_btn)
        root.addLayout(head)

        # 2. 拖拽区与选图按钮行
        self.drop_zone = _ProductDropZone(self)
        self.drop_zone.filesDropped.connect(self.add_paths)
        root.addWidget(self.drop_zone)

        pick_row = QHBoxLayout()
        pick_row.setSpacing(8)
        btn_files = QPushButton("  选择产品图片…")
        btn_files.setObjectName("GhostAccentButton")
        btn_files.setIcon(painted_icon("plus", S._ACCENT, 13))
        btn_files.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_files.clicked.connect(self._pick_files)
        pick_row.addWidget(btn_files)

        btn_folder = QPushButton("  导入整个文件夹…")
        btn_folder.setIcon(painted_icon("folder", S._TEXT_SECONDARY, 13))
        btn_folder.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_folder.clicked.connect(self._pick_folder)
        pick_row.addWidget(btn_folder)

        pick_row.addStretch()
        self.count_summary = QLabel("尚未选择图片")
        self.count_summary.setObjectName("HintLabel")
        pick_row.addWidget(self.count_summary)

        btn_clear = QPushButton("清空列表")
        btn_clear.setObjectName("GhostButton")
        btn_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_clear.clicked.connect(self._clear_paths)
        pick_row.addWidget(btn_clear)
        root.addLayout(pick_row)

        # 3. 待入库图片预览表格
        self.table = QTableWidget(0, 5)
        self.table.setObjectName("BorderlessTable")
        self.table.setHorizontalHeaderLabels(["预览", "文件名", "分辨率 / 大小", "透明通道检测", ""])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 52)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(4, 36)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setMinimumHeight(148)
        root.addWidget(self.table, 1)

        # 4. 产品分类与命名配置卡片
        form_card = QFrame()
        form_card.setObjectName("Surface")
        form_lay = QVBoxLayout(form_card)
        form_lay.setContentsMargins(16, 14, 16, 14)
        form_lay.setSpacing(10)

        row_series = QHBoxLayout()
        row_series.setSpacing(10)
        lbl_series = QLabel("产品系列")
        lbl_series.setObjectName("SubSectionTitle")
        lbl_series.setFixedWidth(68)
        row_series.addWidget(lbl_series)

        self.series_combo = QComboBox()
        self.series_combo.setEditable(True)
        self.series_combo.setMinimumHeight(32)
        existing_series = self.product_page.existing_series_names()
        for s_name in existing_series:
            self.series_combo.addItem(s_name)
        if default_series:
            self.series_combo.setCurrentText(default_series)
        elif existing_series:
            self.series_combo.setCurrentIndex(0)
        self.series_combo.lineEdit().setPlaceholderText("选择已有系列，或直接输入新系列名称自动建夹（如：26年中秋礼盒）")
        self.series_combo.currentTextChanged.connect(self._on_series_text_changed)
        row_series.addWidget(self.series_combo, 2)

        lbl_brand = QLabel("所属大类")
        lbl_brand.setObjectName("SubSectionTitle")
        row_series.addWidget(lbl_brand)

        self.brand_combo = QComboBox()
        self.brand_combo.setMinimumHeight(32)
        self.brand_combo.addItem("自动识别", "")
        for b_name in GROUP_ORDER:
            self.brand_combo.addItem(b_name, b_name)
        row_series.addWidget(self.brand_combo, 1)
        form_lay.addLayout(row_series)

        # 快捷系列胶囊行
        quick_row = QHBoxLayout()
        quick_row.setSpacing(6)
        quick_hint = QLabel("快捷选系列：")
        quick_hint.setObjectName("HintLabel")
        quick_row.addWidget(quick_hint)
        for s_name in existing_series[:6]:
            chip = QPushButton(s_name)
            chip.setObjectName("GhostButton")
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setFixedHeight(22)
            chip.clicked.connect(lambda _c=False, n=s_name: self.series_combo.setCurrentText(n))
            quick_row.addWidget(chip)
        quick_row.addStretch()
        form_lay.addLayout(quick_row)

        # 命名与入库方式行
        row_name = QHBoxLayout()
        row_name.setSpacing(10)
        self.name_label = QLabel("产品命名")
        self.name_label.setObjectName("SubSectionTitle")
        self.name_label.setFixedWidth(68)
        row_name.addWidget(self.name_label)

        self.name_input = QLineEdit()
        self.name_input.setMinimumHeight(32)
        self.name_input.setPlaceholderText("可直接修改入库文件名（建议带容量/度数如：中国劲酒红标125ml_35度）")
        row_name.addWidget(self.name_input, 1)

        self.move_check = QCheckBox("移动入库（清理原位置散图）")
        self.move_check.setToolTip("不勾选则复制一份到产品库并保留原文件；勾选则把原文件直接移入产品库")
        row_name.addWidget(self.move_check)
        form_lay.addLayout(row_name)

        root.addWidget(form_card)

        # 5. 底部按钮行
        foot = QHBoxLayout()
        foot.setSpacing(10)
        self.auto_brand_hint = QLabel("")
        self.auto_brand_hint.setObjectName("HintLabel")
        foot.addWidget(self.auto_brand_hint, 1)

        cancel_btn = QPushButton("取消")
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setMinimumHeight(36)
        cancel_btn.clicked.connect(self.reject)
        foot.addWidget(cancel_btn)

        self.ok_btn = QPushButton("  确认添加入库")
        self.ok_btn.setObjectName("PrimaryButton")
        self.ok_btn.setIcon(painted_icon("upload", "#FFFFFF", 15))
        self.ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ok_btn.setMinimumHeight(36)
        self.ok_btn.setEnabled(False)
        self.ok_btn.clicked.connect(self._on_confirm)
        foot.addWidget(self.ok_btn)
        root.addLayout(foot)

        self._on_series_text_changed(self.series_combo.currentText())

    def _choose_library_root(self):
        selected = QFileDialog.getExistingDirectory(
            self, "选择产品库根目录", str(self.product_page.root)
        )
        if not selected:
            return
        self.product_page.root = Path(selected)
        try:
            self.product_page.repository.set_setting("product_library_root", selected)
            self.product_page.repository.set_product_folder(selected)
        except Exception:
            pass
        self.root_info_label.setText(f"目标产品库：{selected}")
        self.series_combo.clear()
        for s_name in self.product_page.existing_series_names():
            self.series_combo.addItem(s_name)

    def _on_series_text_changed(self, text: str):
        series = (text or "").strip() or "默认产品系列"
        auto_group = group_for_series(series)
        self.brand_combo.setItemText(0, f"自动识别（归入：{auto_group}）")
        self.auto_brand_hint.setText(f"入库路径：{self.product_page.root / series}")

    def _pick_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择要入库的产品图片", "", "产品图片 (*.png *.jpg *.jpeg *.webp)"
        )
        if files:
            self.add_paths(files)

    def _pick_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择包含产品图的文件夹")
        if folder:
            self.add_paths([folder])

    def _clear_paths(self):
        self._paths.clear()
        self._refresh_table()

    def add_paths(self, raw_paths: list[str | Path]) -> int:
        existing = set(self._paths)
        added = 0
        first_folder_name = ""
        for raw in raw_paths:
            p = Path(raw)
            if p.is_dir():
                if not first_folder_name:
                    first_folder_name = p.name
                for sub in sorted(p.rglob("*")):
                    if sub.is_file() and sub.suffix.lower() in IMAGE_SUFFIXES:
                        norm = os.path.normpath(str(sub))
                        if norm not in existing:
                            self._paths.append(norm)
                            existing.add(norm)
                            added += 1
            elif p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES:
                norm = os.path.normpath(str(p))
                if norm not in existing:
                    self._paths.append(norm)
                    existing.add(norm)
                    added += 1
        if first_folder_name and (not self.series_combo.currentText().strip() or self.series_combo.currentText() in STARTER_SERIES):
            self.series_combo.setCurrentText(first_folder_name)
        if added:
            self._refresh_table()
        return added

    def _remove_at(self, index: int):
        if 0 <= index < len(self._paths):
            self._paths.pop(index)
            self._refresh_table()

    def _refresh_table(self):
        self.table.setRowCount(len(self._paths))
        checker = checker_tile(S.get_theme() == "dark")
        alpha_count = 0
        for row, path_str in enumerate(self._paths):
            p = Path(path_str)
            self.table.setRowHeight(row, 44)

            # 1. 缩略预览（带棋盘格衬底）
            thumb_lbl = QLabel()
            thumb_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            canvas = QPixmap(36, 36)
            canvas.fill(Qt.GlobalColor.transparent)
            painter = QPainter(canvas)
            painter.fillRect(0, 0, 36, 36, QBrush(checker))
            reader = QImageReader(path_str)
            reader.setAutoTransform(True)
            orig = reader.size()
            w, h = (orig.width(), orig.height()) if orig.isValid() else (0, 0)
            if w and h:
                reader.setScaledSize(QSize(36, 36))
            img = reader.read()
            if not img.isNull():
                scaled = img.scaled(34, 34, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                painter.drawImage((36 - scaled.width()) // 2, (36 - scaled.height()) // 2, scaled)
            painter.end()
            thumb_lbl.setPixmap(canvas)
            self.table.setCellWidget(row, 0, thumb_lbl)

            # 2. 文件名
            name_item = QTableWidgetItem(p.name)
            name_item.setToolTip(path_str)
            self.table.setItem(row, 1, name_item)

            # 3. 尺寸与体积
            try:
                sz = p.stat().st_size
            except OSError:
                sz = 0
            dim_text = f"{w}×{h}" if (w and h) else "—"
            self.table.setItem(row, 2, QTableWidgetItem(f"{dim_text} · {_fmt_size(sz)}"))

            # 4. 透明底检测徽章
            has_a = probe_alpha(p)
            if has_a:
                alpha_count += 1
            badge_wrap = QWidget()
            badge_wrap.setStyleSheet("background: transparent;")
            bl = QHBoxLayout(badge_wrap)
            bl.setContentsMargins(4, 0, 4, 0)
            badge = QLabel(" ✓ 免抠透明底 " if has_a else " ⚠ 非透明底 ")
            if has_a:
                badge.setStyleSheet(
                    f"color:{S._SUCCESS};background:{S._ACCENT_LIGHT};border-radius:6px;"
                    "padding:2px 8px;font-size:11px;font-weight:700;"
                )
            else:
                badge.setStyleSheet(
                    f"color:{S._WARNING};background:{S._SURFACE_2};border-radius:6px;"
                    "padding:2px 8px;font-size:11px;font-weight:600;"
                )
            bl.addWidget(badge)
            bl.addStretch()
            self.table.setCellWidget(row, 3, badge_wrap)

            # 5. 移除按钮
            del_btn = QPushButton("×")
            del_btn.setObjectName("DangerIconButton")
            del_btn.setFixedSize(24, 24)
            del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            del_btn.clicked.connect(lambda _c=False, r=row: self._remove_at(r))
            self.table.setCellWidget(row, 4, del_btn)

        total = len(self._paths)
        self.ok_btn.setEnabled(total > 0)
        if total == 0:
            self.count_summary.setText("尚未选择图片")
            self.name_label.setText("产品命名")
            self.name_input.clear()
            self.name_input.setPlaceholderText("可直接修改入库文件名（建议带容量/度数如：中国劲酒红标125ml_35度）")
        elif total == 1:
            self.count_summary.setText(f"已选 1 款产品（{'透明底免抠' if alpha_count else '非透明底'}）")
            self.name_label.setText("产品命名")
            self.name_input.setText(Path(self._paths[0]).stem)
            self.name_input.setPlaceholderText("可直接修改入库文件名（建议带容量/度数如：中国劲酒红标125ml_35度）")
        else:
            self.count_summary.setText(f"已选 {total} 款产品 · 其中 {alpha_count} 款为透明底免抠")
            self.name_label.setText("批量前缀")
            self.name_input.clear()
            self.name_input.setPlaceholderText("可选：统一添加文件名前缀（如 26新品_ ，留空则保留原文件名）")

    def execute_import(self) -> list[ProductShot]:
        """执行产品导入并返回新入库的 ProductShot 列表。"""
        if not self._paths:
            return []
        series = self.series_combo.currentText().strip() or "默认产品系列"
        brand_override = self.brand_combo.currentData() or None
        custom_name = self.name_input.text().strip()
        move_mode = self.move_check.isChecked()
        self.imported_shots = self.product_page.import_product_files(
            self._paths,
            series,
            brand_override=brand_override,
            custom_name_or_prefix=custom_name,
            move_files=move_mode,
        )
        return self.imported_shots

    def _on_confirm(self):
        shots = self.execute_import()
        if shots:
            self.accept()
