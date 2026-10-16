# -*- coding: utf-8 -*-
"""PSD 一级分组批量导出：把每个最外层（一级）分组各导出一张图。

沿用用户既有工具 PsTopLevelGroupExporter 的规则：

- 只导出「一级分组」（根节点的直接子分组），顶层散层不单独导出；
- 位于图层面板**最顶部**、连续排列且已锁定的一级图层/分组 → 所有图片的公共前景层；
- 位于图层面板**最底部**、连续排列且已锁定的一级图层/分组 → 所有图片的公共背景层；
- 两端公共层不单独导出，但会合成进每一张图；
- 一级分组内部的隐藏内容保持隐藏（不会被强制显示）；
- 文件名直接用分组名，Windows 非法字符自动删除，重名依次追加 _2、_3；
- 全程在内存合成，不修改原 PSD。

⚠️ psd-tools 的迭代顺序是**自下而上**：``list(psd)[0]`` 是图层面板最底部那个
（通常是「背景」层），最后一个才是面板最顶部。所以「面板顶部」要倒序扫。

⚠️ ``composite(layer_filter=...)`` 的过滤器一旦对某个分组返回 False，整棵子树都会被
跳过；而对某个图层返回 True 只代表"允许参与合成"，它自己隐藏与否仍由我们决定。
因此本模块的过滤器是「白名单 + 逐层可见性判断」的组合，而不是简单的 in/not in。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, Optional

# Windows 文件名非法字符（原工具的做法是直接删除，不是替换成下划线）
WINDOWS_ILLEGAL = '\\/:*?"<>|'
_NAME_TOO_LONG = 100

DEFAULT_SUBFOLDER = "分组导出"
SUPPORTED_FORMATS = ("png", "jpg")


# ══════════════════════════════════════════════════════════════
#  数据结构
# ══════════════════════════════════════════════════════════════
@dataclass
class TopEntry:
    """根节点的一个直接子项（一级图层或一级分组）。"""

    layer: object
    index: int
    is_group: bool
    locked: bool

    @property
    def name(self) -> str:
        return str(getattr(self.layer, "name", "") or "")


@dataclass
class GroupExportResult:
    group: str
    output: Optional[str] = None
    ok: bool = False            # 是否真的产出了文件
    forced: bool = False        # 是否回退成「强制显示组内隐藏图层」才出图
    empty: bool = False         # 组内确实没有可显示内容，已跳过（不产生空白图）
    message: str = ""


@dataclass
class PsdExportResult:
    source: str
    out_dir: str = ""
    groups_total: int = 0
    groups_ok: int = 0
    groups_empty: int = 0
    skipped_locked: int = 0
    public_foreground: list[str] = field(default_factory=list)
    public_background: list[str] = field(default_factory=list)
    items: list[GroupExportResult] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def failed(self) -> int:
        return sum(1 for item in self.items if not item.ok and not item.empty)

    @property
    def forced(self) -> int:
        return sum(1 for item in self.items if item.forced)


@dataclass
class ExportOptions:
    out_dir: Optional[Path] = None      # None = 与 PSD 同目录下的「分组导出」
    subfolder: str = DEFAULT_SUBFOLDER
    fmt: str = "png"                    # png（透明底）/ jpg（白底）
    quality: int = 92                   # 仅 jpg 生效
    keep_public: bool = True            # 是否把两端连续锁定层当公共层

    def normalized_format(self) -> str:
        fmt = (self.fmt or "png").lower().lstrip(".")
        return fmt if fmt in SUPPORTED_FORMATS else "png"


# ══════════════════════════════════════════════════════════════
#  文件名处理
# ══════════════════════════════════════════════════════════════
def sanitize_name(name: str) -> str:
    """分组名 → 可用的 Windows 文件名（非法字符删除、首尾空白与句点清理）。"""
    text = "".join(ch for ch in str(name or "") if ch not in WINDOWS_ILLEGAL)
    text = text.replace("\n", " ").replace("\r", " ").replace("\t", " ")
    text = re.sub(r"\s+", " ", text).strip().rstrip(".")
    if not text:
        text = "未命名分组"
    if len(text) > _NAME_TOO_LONG:
        text = text[:_NAME_TOO_LONG].rstrip()
    return text


def unique_path(dst: Path) -> Path:
    """目标已存在时依次追加 _2、_3（与既有工具一致）。"""
    if not dst.exists():
        return dst
    stem, suffix = dst.stem, dst.suffix
    index = 2
    while dst.with_name(f"{stem}_{index}{suffix}").exists():
        index += 1
    return dst.with_name(f"{stem}_{index}{suffix}")


# ══════════════════════════════════════════════════════════════
#  图层树分析
# ══════════════════════════════════════════════════════════════
def _is_locked(layer: object) -> bool:
    """PSD 的锁定是位掩码（lock transparent / position / all…），非 0 即视为锁定。"""
    try:
        return bool(getattr(layer, "locks", 0))
    except Exception:
        return False


def plan_top_level(psd) -> list[TopEntry]:
    """列出根节点的全部直接子项。顺序 = psd-tools 迭代顺序 = 面板自下而上。"""
    entries: list[TopEntry] = []
    for index, layer in enumerate(psd):
        try:
            is_group = bool(layer.is_group())
        except Exception:
            is_group = False
        entries.append(TopEntry(
            layer=layer,
            index=index,
            is_group=is_group,
            locked=_is_locked(layer),
        ))
    return entries


def split_public_layers(entries: list[TopEntry]) -> tuple[list[TopEntry], list[TopEntry]]:
    """切出两端的公共层，返回 (前景层, 背景层)。

    背景层：从面板最底部（列表头部）往上连续锁定；
    前景层：从面板最顶部（列表尾部）往下连续锁定。
    """
    background: list[TopEntry] = []
    for entry in entries:
        if not entry.locked:
            break
        background.append(entry)

    foreground: list[TopEntry] = []
    for entry in reversed(entries):
        if not entry.locked:
            break
        foreground.append(entry)

    # 整个文档都被锁定时两端会重叠，去重（按图层身份）
    seen: set[int] = set()
    foreground = [e for e in foreground if not (id(e.layer) in seen or seen.add(id(e.layer)))]
    return foreground, background


def exportable_groups(
    entries: list[TopEntry],
    public: list[TopEntry],
) -> list[TopEntry]:
    """可导出的一级分组 = 是分组，且没被当成公共层。"""
    excluded = {id(e.layer) for e in public}
    return [e for e in entries if e.is_group and id(e.layer) not in excluded]


def walk_layers(layer) -> Iterator[object]:
    """深度优先遍历一个图层/分组及其全部后代。"""
    yield layer
    try:
        is_group = layer.is_group()
    except Exception:
        is_group = False
    if not is_group:
        return
    for child in layer:
        yield from walk_layers(child)


def analyze(source: str | Path, keep_public: bool = True) -> dict:
    """只读分析：不导出，只回报识别结果（给界面上做预览用）。"""
    from psd_tools import PSDImage

    psd = PSDImage.open(str(source))
    entries = plan_top_level(psd)
    foreground, background = split_public_layers(entries) if keep_public else ([], [])
    public = foreground + background
    groups = exportable_groups(entries, public)
    return {
        "groups": [sanitize_name(e.name) for e in groups],
        "foreground": [e.name for e in foreground],
        "background": [e.name for e in background],
        "loose_layers": [e.name for e in entries
                         if not e.is_group and id(e.layer) not in {id(p.layer) for p in public}],
        "total_top": len(entries),
    }


# ══════════════════════════════════════════════════════════════
#  导出
# ══════════════════════════════════════════════════════════════
def group_has_visible_content(group) -> bool:
    """组内是否存在「整条祖先链都可见、且自己有实际像素」的图层。

    用来判断要不要回退成强制显示。纯结构判断，零合成开销 —— 不能靠"最终图是否
    全透明"来判断：一旦合成了公共背景层（不透明），空白也会被盖住。

    ⚠️ 必须累计祖先链的可见性。本文件里就有这种结构：某个中间分组整体被隐藏，
    但它下面的孙层 visible 仍然是 True —— 只看图层自己的标志会误判成"有内容"，
    于是走严格过滤，最后导出一张只有背景的空图。
    """
    def _visit(layer, chain_visible: bool) -> bool:
        try:
            is_group = layer.is_group()
        except Exception:
            is_group = False
        if not is_group:
            if not chain_visible:
                return False
            bbox = getattr(layer, "bbox", None)
            return bool(bbox and bbox[2] > bbox[0] and bbox[3] > bbox[1])
        for child in layer:
            child_visible = chain_visible and bool(getattr(child, "visible", True))
            if _visit(child, child_visible):
                return True
        return False

    # 目标分组自身的可见性会被强制成 True（我们就是要导出它），所以从 True 起算
    return _visit(group, True)


def _is_blank(image) -> bool:
    """整张图完全透明 = 这个分组没有任何可显示内容。"""
    try:
        if image.mode in ("RGBA", "LA"):
            return image.getchannel("A").getextrema()[1] == 0
        return False
    except Exception:
        return False


def _save(image, dst: Path, fmt: str, quality: int) -> None:
    if fmt == "jpg":
        from PIL import Image

        if image.mode in ("RGBA", "LA", "P"):
            rgba = image.convert("RGBA")
            canvas = Image.new("RGB", rgba.size, (255, 255, 255))
            canvas.paste(rgba, mask=rgba.split()[-1])
            canvas.save(dst, "JPEG", quality=quality, subsampling=0)
        else:
            image.convert("RGB").save(dst, "JPEG", quality=quality, subsampling=0)
    else:
        image.save(dst, "PNG")


def export_psd_groups(
    source: str | Path,
    options: Optional[ExportOptions] = None,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> PsdExportResult:
    """导出一个 PSD 的全部一级分组。返回逐组结果，单个失败不中断整批。

    ``on_progress(done, total, group_name)``；``should_cancel()`` 返回 True 时提前收尾
    （已导出的保留，不算失败）。
    """

    options = options or ExportOptions()
    fmt = options.normalized_format()
    src = Path(source)
    result = PsdExportResult(source=str(src))

    out_dir = options.out_dir if options.out_dir else (src.parent / options.subfolder)
    out_dir = Path(out_dir)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        result.error = f"无法创建输出目录：{exc}"
        return result
    result.out_dir = str(out_dir)

    try:
        from psd_tools import PSDImage
        psd = PSDImage.open(str(src))
    except Exception as exc:
        result.error = f"无法打开 PSD：{exc}"
        return result

    entries = plan_top_level(psd)
    if not entries:
        result.error = "这个文件里没有图层"
        return result

    if options.keep_public:
        foreground, background = split_public_layers(entries)
    else:
        foreground, background = [], []
    public = foreground + background
    groups = exportable_groups(entries, public)

    result.public_foreground = [e.name for e in foreground]
    result.public_background = [e.name for e in background]
    result.skipped_locked = len(public)
    result.groups_total = len(groups)

    if not groups:
        result.error = "没有找到可导出的一级分组（顶层只有散层，或分组都被当作公共层了）"
        return result

    # 公共层白名单：公共层自身 + 其全部后代
    public_allow: set[int] = set()
    for entry in public:
        public_allow.update(id(item) for item in walk_layers(entry.layer))
    public_root_ids = {id(e.layer) for e in public}

    total = len(groups)
    for done, entry in enumerate(groups):
        if should_cancel and should_cancel():
            break
        name = sanitize_name(entry.name)
        if on_progress:
            on_progress(done, total, name)

        group = entry.layer
        group_id = id(group)
        group_ids = {id(item) for item in walk_layers(group)}

        def _filter(layer, _gid=group_id, _gids=group_ids, _allow=public_allow,
                    _roots=public_root_ids):
            lid = id(layer)
            if lid == _gid:
                # 目标分组本身：无视它自己的隐藏状态强制导出
                # （设计师常把各 SKU 分组设成隐藏，靠开/关预览，这是常态）
                return True
            if lid in _gids:
                # 组内部：隐藏的保持隐藏
                return bool(getattr(layer, "visible", True))
            if lid in _allow:
                # 公共层：同样尊重可见性；公共层自身进白名单
                return bool(getattr(layer, "visible", True)) or lid in _roots
            return False

        def _filter_forced(layer, _gid=group_id, _gids=group_ids, _allow=public_allow,
                           _roots=public_root_ids):
            """回退用：把目标组内部全部强制显示，公共层仍按原可见性。"""
            lid = id(layer)
            if lid == _gid or lid in _gids:
                return True
            if lid in _allow:
                return bool(getattr(layer, "visible", True)) or lid in _roots
            return False

        try:
            # 组内一个可见像素都没有 → 直接走强制显示，否则只会导出一张光秃秃的
            # 背景图（公共背景层是不透明的，靠"结果全透明"判断不出来）。
            has_content = group_has_visible_content(group)
            image = psd.composite(
                layer_filter=_filter if has_content else _filter_forced)
            forced = not has_content

            if _is_blank(image):
                result.items.append(GroupExportResult(
                    group=name, ok=False, empty=True,
                    message="组内无可显示内容，已跳过"))
                result.groups_empty += 1
                continue

            dst = unique_path(out_dir / f"{name}.{fmt}")
            _save(image, dst, fmt, options.quality)
            result.items.append(GroupExportResult(
                group=name, output=str(dst), ok=True, forced=forced,
                message="组内隐藏图层已强制显示" if forced else ""))
            result.groups_ok += 1
        except Exception as exc:  # 单组失败不中断整批
            result.items.append(GroupExportResult(group=name, ok=False, message=str(exc)))

    if on_progress:
        on_progress(result.groups_ok, total, "完成")
    return result
