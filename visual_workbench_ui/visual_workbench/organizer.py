# -*- coding: utf-8 -*-
"""一键归档核心逻辑：扫描(只读) -> 移动计划 -> 分批执行(带撤销日志)。

按用户 G 盘既有规则归位：
- 目标位置：G:\\26年文件（2026 年）；G:\\25年X月文件（2025 年按月）；更早年份建 G:\\YY年文件
- 系统截图（Pictures\\Screenshots）单独放 G:\\YY年截图
- 文件名加 YYMMDD 日期前缀（按修改时间），已有合法前缀的不重复加
安全原则：
- 只移动、永不删除；
- 每次移动写入撤销日志，可整体回滚；
- 每批最多移动 BATCH_SIZE 个文件并逐一校验；
- 跳过系统目录、工具软件目录、缓存目录。
"""
from __future__ import annotations

import datetime
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .config import app_data_dir, user_known_folders

BATCH_SIZE = 10

# 整理范围：随手存文件的四个常用目录
SCAN_ROOTS = ["Desktop", "Downloads", "Documents", "Pictures"]

# 归档目标盘（沿用用户 G 盘的目录习惯）：G:\26年8月文件\设计源文件\xxx.psd
# 可在「设置」页改；各函数都在调用时读取这个全局变量，所以 set_dest_drive() 立即生效。
DEFAULT_DEST_DRIVE = Path("G:/")
DEST_DRIVE = DEFAULT_DEST_DRIVE if DEFAULT_DEST_DRIVE.exists() else (user_known_folders().get('Documents', Path.home() / 'Documents') / '视觉归档库')


def set_dest_drive(path: "Path | str | None") -> Path:
    """设置归档目标位置。传空则恢复默认的 G:/（若本机无 G 盘则回退到用户文档下的「视觉归档库」）。"""
    global DEST_DRIVE
    text = str(path).strip() if path else ""
    if text:
        DEST_DRIVE = Path(text)
    elif DEFAULT_DEST_DRIVE.exists():
        DEST_DRIVE = DEFAULT_DEST_DRIVE if DEFAULT_DEST_DRIVE.exists() else (user_known_folders().get('Documents', Path.home() / 'Documents') / '视觉归档库')
    else:
        docs = user_known_folders().get("Documents", Path.home() / "Documents")
        DEST_DRIVE = docs / "视觉归档库"
    return DEST_DRIVE


def is_dest_accessible() -> bool:
    """检查当前归档目标盘符/上级目录是否可访问（未插移动硬盘时返回 False）。"""
    try:
        if DEST_DRIVE.exists():
            return True
        anchor = Path(DEST_DRIVE.anchor) if DEST_DRIVE.anchor else DEST_DRIVE.parent
        return anchor.exists()
    except OSError:
        return False
FOLDER_MONTH = "{yy}年{m}月文件"        # 所有年份统一按月
FOLDER_SCREENSHOT = "截图"

# 月份文件夹内的分类子文件夹名（用户指定）
CATEGORY_EXTS = {
    "设计源文件": {".psd", ".psb", ".ai", ".eps", ".cdr", ".indd", ".sketch", ".fig",
                  ".xcf", ".aep", ".prproj", ".c4d"},
    "图片": {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".svg"},
    "字体": {".ttf", ".otf", ".ttc"},
    "视频": {".mp4", ".mov", ".avi", ".mkv", ".webm"},
    "文档": {".pdf", ".xls", ".xlsx", ".doc", ".docx", ".pptx", ".csv"},
    "压缩包": {".zip", ".rar", ".7z"},
}

# 合法的 YYMMDD 日期前缀（已有则不再改名）
_DATE_PREFIX_RE = re.compile(r"^(2\d)(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])")
# 2025 年风格的 月.日 前缀（如 10.10自购页.png），同样视为已有日期
_MD_PREFIX_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})")

# 绝对不能动的目录（工具、软件、缓存、聊天记录等），小写匹配
EXCLUDE_DIRS = {
    # 系统与开发
    "windows", "program files", "program files (x86)", "appdata",
    "$recycle.bin", "system volume information", "node_modules", ".git",
    "__pycache__", ".venv", "venv", "build", "dist", "anaconda3",
    # 工具软件文件夹（桌面上的）
    "5347345734", "visual_workbench_windows_package", "comfyui-manager",
    "psdthumbfree-win-x64", "pstoplevelgroupexporter-portable", "格式方舟",
    "psd文件角标工具", "jimeng-liquor-visual-prompts-windows-package",
    "流量看板工具_windows打包包",
    # 软件数据 / 缓存 / 聊天记录
    "adobe", "dseetem", "wechat files", "wps cloudfiles", "onedrive",
    "tencent files", "dingtalk",
    # 应用数据 / 系统用户目录（整文件夹规则必须避开，否则会搬走软件数据）
    "wxwork", "wechat", "weixin", "qq", "dwgcloudgallery", "mycad",
    "dsh-desktop", "my music", "my pictures", "my videos", "saved games",
    "favorites", "contacts", "searches", "links", "3d objects",
    "creative cloud files", "dropbox", "google drive", "icloud", "my games",
    "visual studio 2022", "intel", "hp", "lenovo", "edge", "chrome",
    "windowsapps", ".thumbnails", ".cache", "系统默认",
    # 用户指定保护：产品图库，不做分类
    "劲牌产品png",
}
EXCLUDE_FILES = {"desktop.ini", "thumbs.db"}

CATEGORY_ORDER = list(CATEGORY_EXTS)

# ── 系统截图专用规则 ──
# 系统“屏幕截图”文件夹（Pictures\Screenshots 等）是 Windows 常驻目录：
# 绝不整体搬运（搬走后系统会重建，导致反复归档、生成 截图_1/_2/_3 重复文件夹）。
# 只把里面的散截图按修改时间并入已有的 G:\{yy}年截图（不新建月份/分类文件夹）。
SCREENSHOT_DIR_NAMES = {"screenshots", "截图", "屏幕截图"}
SCREENSHOT_EXTS = {".png", ".jpg", ".jpeg"}


def _screenshot_dest_dir(mtime_dt: datetime.datetime) -> Path:
    """系统截图的归档目标：复用已存在的 G:\\{yy}年截图，不存在时才新建。"""
    yy = f"{mtime_dt.year % 100:02d}"
    if DEST_DRIVE.is_dir():
        try:
            existing = [d for d in DEST_DRIVE.iterdir()
                        if d.is_dir() and d.name in (f"{yy}年截图", "截图")]
            if existing:
                existing.sort(key=lambda d: (d.name != f"{yy}年截图", -d.stat().st_mtime))
                return existing[0]
        except OSError:
            pass
    return DEST_DRIVE / f"{yy}年截图"


def _is_screenshot_dir(path: Path) -> bool:
    """判断是否系统截图文件夹（按名字，含 GBK 之外的变体）。"""
    return path.name.lower() in SCREENSHOT_DIR_NAMES


def categorize(ext: str) -> str | None:
    ext = ext.lower()
    for cat, exts in CATEGORY_EXTS.items():
        if ext in exts:
            return cat
    return None


@dataclass
class MoveRecord:
    src: str
    dst: str
    category: str
    month: str
    size: int
    mtime: str


@dataclass
class ScanResult:
    generated_at: str
    records: list[MoveRecord] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.records)

    @property
    def total_bytes(self) -> int:
        return sum(r.size for r in self.records)

    def count_by_category(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for r in self.records:
            result[r.category] = result.get(r.category, 0) + 1
        return {cat: result[cat] for cat in CATEGORY_ORDER if cat in result}


def _iter_roots() -> list[Path]:
    known = user_known_folders()
    home = Path.home()
    roots: list[Path] = []
    seen: set[str] = set()
    for name in SCAN_ROOTS:
        for candidate in (known.get(name), home / name):
            if candidate is not None and candidate.is_dir():
                try:
                    key = str(candidate.resolve()).lower()
                except OSError:
                    key = str(candidate).lower()
                if key not in seen:
                    seen.add(key)
                    roots.append(candidate)
    return roots


def _folder_scan(folder: Path) -> tuple[datetime.datetime, int, bool]:
    """一次遍历返回 (最新修改时间, 文件总大小, 是否含设计文件)。
    是否含设计文件用于判断「该文件夹是不是设计项目夹」，避免把应用/缓存目录整体搬走。"""
    newest = folder.stat().st_mtime
    total_size = 0
    has_design = False
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [d for d in dirnames
                       if d.lower() not in EXCLUDE_DIRS and not d.startswith(".")]
        for name in filenames:
            if name.lower() in EXCLUDE_FILES or name.startswith("~$"):
                continue
            full = os.path.join(dirpath, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            total_size += st.st_size
            if st.st_mtime > newest:
                newest = st.st_mtime
            if not has_design and categorize(os.path.splitext(name)[1]):
                has_design = True
    return datetime.datetime.fromtimestamp(newest), total_size, has_design


def unique_dest(dst: Path) -> Path:
    if not dst.exists():
        return dst
    stem, suffix = dst.stem, dst.suffix
    i = 1
    while (dst.with_name(f"{stem}_{i}{suffix}")).exists():
        i += 1
    return dst.with_name(f"{stem}_{i}{suffix}")


def scan(on_progress: Optional[Callable[[int, int], None]] = None,
         on_done: Optional[Callable[[ScanResult], None]] = None) -> ScanResult:
    """只读扫描，生成移动计划（不写库不移动）。

    规则（用户确认版）：
    - 散落的单文件 → 按修改时间归入 G:\\{yy}年{m}月文件\\{分类}\\
    - 用户创建的文件夹 → 不拆分，整个文件夹归入 G:\\{yy}年{m}月文件\\（按夹内最新修改时间定月份）
    - Pictures\\Screenshots 系统截图：不整体搬运文件夹；只移动里面的散截图，
      并入已有的 G:\\{yy}年截图（无则新建），避免反复归档产生 截图_1/_2/_3
    """
    records: list[MoveRecord] = []
    entries: list[tuple[Path, bool]] = []  # (path, is_dir)
    shot_files: list[Path] = []            # 系统截图文件夹内的散截图
    for root in _iter_roots():
        try:
            children = list(root.iterdir())
        except OSError:
            continue
        for entry in sorted(children, key=lambda p: p.name):
            name = entry.name
            if name.lower() in EXCLUDE_FILES or name.startswith(("~$", ".")):
                continue
            try:
                entry.stat()
            except OSError:
                continue
            if entry.is_dir():
                if name.lower() in EXCLUDE_DIRS:
                    continue
                # 系统截图文件夹：只收集里面的散截图，文件夹本身不进计划
                if _is_screenshot_dir(entry):
                    try:
                        for f in entry.iterdir():
                            if (f.is_file() and f.suffix.lower() in SCREENSHOT_EXTS
                                    and f.name.lower() not in EXCLUDE_FILES):
                                shot_files.append(f)
                    except OSError:
                        pass
                    continue
                entries.append((entry, True))
            else:
                if categorize(entry.suffix):
                    entries.append((entry, False))
    # 系统截图：并入已有的 YY年截图 文件夹
    for f in shot_files:
        try:
            st = f.stat()
        except OSError:
            continue
        mt_dt = datetime.datetime.fromtimestamp(st.st_mtime)
        dest_dir = _screenshot_dest_dir(mt_dt)
        records.append(MoveRecord(
            src=str(f), dst=str(dest_dir / f.name),
            category="系统截图", month=mt_dt.strftime("%Y-%m"),
            size=st.st_size, mtime=mt_dt.strftime("%Y-%m-%d %H:%M"),
        ))
    total = len(entries)
    for idx, (entry, is_dir) in enumerate(entries, start=1):
        name = entry.name
        try:
            st = entry.stat()
        except OSError:
            if on_progress:
                on_progress(idx, total)
            continue
        if is_dir:
            mt_dt, size, has_design = _folder_scan(entry)
            if not has_design:
                # 不含任何设计文件的文件夹（应用/缓存/普通资料夹）→ 不归档，避免搬走软件数据
                if on_progress:
                    on_progress(idx, total)
                continue
            yy, m = f"{mt_dt.year % 100:02d}", mt_dt.month
            month = mt_dt.strftime("%Y-%m")
            # 系统截图文件夹不整体搬运（在 scan() 前置阶段已按散截图处理）
            dest_dir = DEST_DRIVE / FOLDER_MONTH.format(yy=yy, m=m) / name
            cat_label = "整文件夹"
            records.append(MoveRecord(
                src=str(entry), dst=str(dest_dir), category=cat_label,
                month=month, size=size, mtime=mt_dt.strftime("%Y-%m-%d %H:%M"),
            ))
        else:
            cat = categorize(entry.suffix)
            if not cat:
                if on_progress:
                    on_progress(idx, total)
                continue
            mt_dt = datetime.datetime.fromtimestamp(st.st_mtime)
            yy, m = f"{mt_dt.year % 100:02d}", mt_dt.month
            dest_dir = DEST_DRIVE / FOLDER_MONTH.format(yy=yy, m=m) / cat
            month = mt_dt.strftime("%Y-%m")
            records.append(MoveRecord(
                src=str(entry), dst=str(dest_dir / entry.name),
                category=cat, month=month, size=st.st_size,
                mtime=mt_dt.strftime("%Y-%m-%d %H:%M"),
            ))
        if on_progress:
            on_progress(idx, total)
    result = ScanResult(
        generated_at=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        records=records,
    )
    save_plan(result)
    if on_done:
        on_done(result)
    return result


def describe_dest() -> str:
    """给界面展示的目标位置说明。"""
    return f"{DEST_DRIVE}\\26年8月文件\\{{设计源文件|图片|…|整个文件夹}}"


# ---------- 计划持久化 ----------

def _plan_file() -> Path:
    return app_data_dir() / "organizer_plan.json"


def _undo_log() -> Path:
    return app_data_dir() / "organizer_undo.jsonl"


def save_plan(result: ScanResult) -> None:
    data = {
        "generated_at": result.generated_at,
        "dest_root": str(DEST_DRIVE),
        "moves": [r.__dict__ for r in result.records],
    }
    _plan_file().write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def load_plan() -> ScanResult | None:
    f = _plan_file()
    if not f.exists():
        return None
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    records = [MoveRecord(**m) for m in data.get("moves", [])]
    return ScanResult(generated_at=data.get("generated_at", ""), records=records)


# ---------- 执行 / 回滚 ----------

@dataclass
class ApplyReport:
    moved: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)


def apply_moves(on_progress: Optional[Callable[[int, int], None]] = None,
                on_batch: Optional[Callable[[int], None]] = None) -> ApplyReport:
    """按最近一次扫描的计划执行移动。每批校验，逐条写撤销日志。"""
    plan = load_plan()
    report = ApplyReport()
    if not plan:
        report.failed.append(("", "没有可用的整理计划，请先扫描"))
        return report
    moves = [r for r in plan.records if not r.src.startswith(str(DEST_DRIVE))]
    total = len(moves)
    log_f = _undo_log().open("a", encoding="utf-8")
    # 会话标记：撤销时只回滚本次会话（最近一次执行），不翻旧账
    log_f.write(json.dumps({"__session__": True}) + "\n")
    log_f.flush()
    batch: list[MoveRecord] = []

    def flush(batch: list[MoveRecord]) -> None:
        for rec in batch:
            src, dst = Path(rec.src), Path(rec.dst)
            try:
                if not src.exists():
                    report.failed.append((rec.src, "源文件不存在"))
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                final_dst = unique_dest(dst)
                shutil.move(str(src), str(final_dst))
                if final_dst.exists():
                    log_f.write(json.dumps({"src": rec.src, "dst": str(final_dst)},
                                           ensure_ascii=False) + "\n")
                    log_f.flush()
                    report.moved += 1
                else:
                    report.failed.append((rec.src, "移动后校验失败"))
            except Exception as exc:  # 单个失败不中断整批
                report.failed.append((rec.src, str(exc)))
        batch.clear()
        if on_batch:
            on_batch(report.moved)
        if on_progress:
            on_progress(report.moved, total)

    for rec in moves:
        batch.append(rec)
        if len(batch) >= BATCH_SIZE:
            flush(batch)
    if batch:
        flush(batch)
    log_f.close()
    return report


def undo_last(on_progress: Optional[Callable[[int, int], None]] = None) -> int:
    """按撤销日志倒序回滚所有已移动文件/文件夹，并清理因此产生的空文件夹。"""
    log = _undo_log()
    if not log.exists():
        return 0
    entries = []
    for line in log.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("__session__"):
            entries = []  # 新会话开始，丢弃更早的记录，只回滚最近一次
            continue
        entries.append(entry)
    restored = 0
    failed = 0
    for i, entry in enumerate(reversed(entries)):
        dst, src = Path(entry["dst"]), Path(entry["src"])
        if not dst.exists():
            # 兼容 post_process_month_folders 二次归入 归档汇总/字体/图片 等子文件夹的情况
            for sub in ("归档汇总", "字体", "图片", *CATEGORY_EXTS.keys()):
                candidate = dst.parent / sub / dst.name
                if candidate.exists():
                    dst = candidate
                    break
        if dst.exists():
            try:
                src.parent.mkdir(parents=True, exist_ok=True)
                back = unique_dest(src)
                shutil.move(str(dst), str(back))
                restored += 1
            except OSError:
                failed += 1
        else:
            failed += 1
        if on_progress:
            on_progress(i + 1, len(entries))
    # 清理归档盘上月/分类文件夹里搬空后遗留的空目录（只删空目录，安全）
    _cleanup_empty_dirs(DEST_DRIVE)
    if failed == 0:
        log.write_text("", encoding="utf-8")
    return restored


def _rewrite_undo_log_destinations(remapped: dict[str, str]) -> None:
    """当 post_process_month_folders 将月目录顶层条目二次移入子文件夹时，同步更新撤销日志中的 dst。"""
    if not remapped:
        return
    log = _undo_log()
    if not log.exists():
        return
    try:
        out_lines: list[str] = []
        for line in log.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entry = json.loads(stripped)
                if "dst" in entry and entry["dst"] in remapped:
                    entry["dst"] = remapped[entry["dst"]]
                out_lines.append(json.dumps(entry, ensure_ascii=False))
            except json.JSONDecodeError:
                out_lines.append(stripped)
        log.write_text("\n".join(out_lines) + ("\n" if out_lines else ""), encoding="utf-8")
    except OSError:
        pass


def _cleanup_empty_dirs(root: Path) -> int:
    """自底向上删除 root 下的空目录（跳过根目录本身），返回删除数。"""
    removed = 0
    if not root.is_dir():
        return 0
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        if dirnames or filenames:
            continue
        d = Path(dirpath)
        if d == root:
            continue
        for fn in ("归档汇总.txt",):
            pass
        try:
            d.rmdir()
            removed += 1
        except OSError:
            pass
    return removed


# ── 归档后处理：月份文件夹内分类 + 生成归档汇总 ──

# 分类子文件夹名（与 CATEGORY_EXTS 对应）
_CATEGORY_DIRS = {
    "设计源文件": CATEGORY_EXTS["设计源文件"],
    "图片": CATEGORY_EXTS["图片"],
    "字体": CATEGORY_EXTS["字体"],
    "视频": CATEGORY_EXTS["视频"],
    "文档": CATEGORY_EXTS["文档"],
    "压缩包": CATEGORY_EXTS["压缩包"],
}


def _categorize_in_month(month_dir: Path, remapped: Optional[dict[str, str]] = None) -> int:
    """把月份文件夹顶层的散文件按类型归入分类子文件夹，项目文件夹移入「归档汇总」。
    特殊文件夹（截图/字体/素材）自动识别并归入正确位置。返回处理数。"""
    moved = 0
    archive_dir = month_dir / "归档汇总"
    _font_exts = {".ttf", ".otf", ".ttc"}
    for entry in list(month_dir.iterdir()):
        if entry.name in _CATEGORY_DIRS or entry.name in ("归档汇总", "归档汇总.txt"):
            continue
        try:
            if entry.is_dir():
                name_lower = entry.name.lower()
                # 特殊处理：系统截图文件夹 → 截图并入已有的 YY年截图（不新建重复文件夹）
                if name_lower in SCREENSHOT_DIR_NAMES:
                    shot_dest_root = _screenshot_dest_dir(
                        datetime.datetime.fromtimestamp(entry.stat().st_mtime))
                    shot_dest_root.mkdir(parents=True, exist_ok=True)
                    for item in entry.iterdir():
                        if item.is_file() and item.suffix.lower() in SCREENSHOT_EXTS:
                            t = unique_dest(shot_dest_root / item.name)
                            shutil.move(str(item), str(t))
                    try:
                        entry.rmdir()
                    except OSError:
                        pass
                    moved += 1
                    continue
                # 特殊处理：纯字体文件夹 → 字体/
                all_files = [f for f in entry.rglob("*") if f.is_file()]
                if all_files and all(f.suffix.lower() in _font_exts for f in all_files):
                    font_dest = unique_dest(month_dir / "字体" / entry.name)
                    font_dest.parent.mkdir(parents=True, exist_ok=True)
                    old_key = str(entry)
                    shutil.move(old_key, str(font_dest))
                    if remapped is not None:
                        remapped[old_key] = str(font_dest)
                    moved += 1
                    continue
                # 特殊处理：png素材文件夹 → 图片/
                if "png" in name_lower and "素材" in name_lower:
                    img_dest = unique_dest(month_dir / "图片" / entry.name)
                    img_dest.parent.mkdir(parents=True, exist_ok=True)
                    old_key = str(entry)
                    shutil.move(old_key, str(img_dest))
                    if remapped is not None:
                        remapped[old_key] = str(img_dest)
                    moved += 1
                    continue
                # 空文件夹 → 删除
                if not any(entry.iterdir()):
                    try:
                        entry.rmdir()
                    except OSError:
                        pass
                    moved += 1
                    continue
                # 普通项目文件夹 → 归档汇总
                archive_dir.mkdir(parents=True, exist_ok=True)
                dst = unique_dest(archive_dir / entry.name)
                old_key = str(entry)
                shutil.move(old_key, str(dst))
                if remapped is not None:
                    remapped[old_key] = str(dst)
                moved += 1
            else:
                # 散文件 → 按类型归入分类子文件夹
                ext = entry.suffix.lower()
                cat = None
                for cat_name, exts in _CATEGORY_DIRS.items():
                    if ext in exts:
                        cat = cat_name
                        break
                if not cat:
                    continue
                target_dir = month_dir / cat
                target_dir.mkdir(parents=True, exist_ok=True)
                dst = unique_dest(target_dir / entry.name)
                old_key = str(entry)
                shutil.move(old_key, str(dst))
                if remapped is not None:
                    remapped[old_key] = str(dst)
                moved += 1
        except OSError:
            pass  # 单个文件/文件夹操作失败不中断整体
    return moved


def _generate_summary(month_dir: Path) -> None:
    """在月份文件夹里生成归档汇总.txt。"""
    lines = [f"{'='*50}", f"  {month_dir.name} · 归档汇总", f"{'='*50}", ""]
    total_files = 0
    lines.append("【分类文件夹】")
    for cat in ["设计源文件", "图片", "字体", "视频", "文档", "压缩包"]:
        cat_dir = month_dir / cat
        if not cat_dir.exists():
            continue
        n = sum(1 for _ in cat_dir.rglob("*") if _.is_file())
        if n == 0:
            continue
        total_files += n
        lines.append(f"  {cat}/  {n} 个文件")
    lines.append(f"  小计: {total_files} 个文件\n")
    archive_dir = month_dir / "归档汇总"
    if archive_dir.exists():
        project_dirs = []
        for entry in sorted(archive_dir.iterdir()):
            if entry.is_dir():
                n = sum(1 for _ in entry.rglob("*") if _.is_file())
                if n > 0:
                    project_dirs.append((entry.name, n))
                    total_files += n
        if project_dirs:
            lines.append("【归档汇总（项目文件夹，整体保留）】")
            for name, n in project_dirs:
                lines.append(f"  {name}/  {n} 个文件")
            lines.append(f"  小计: {len(project_dirs)} 个文件夹\n")
    lines.append(f"【合计】{total_files} 个文件")
    (month_dir / "归档汇总.txt").write_text("\n".join(lines), encoding="utf-8")


def post_process_month_folders(
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> tuple[int, int]:
    """对归档盘所有月份文件夹执行：分类散文件 + 生成归档汇总，并同步更新撤销日志中的二次移动路径。
    返回 (分类文件数, 生成汇总数)。"""
    if not DEST_DRIVE.is_dir():
        return 0, 0
    cat_count = 0
    summary_count = 0
    remapped: dict[str, str] = {}
    month_dirs = sorted(
        d for d in DEST_DRIVE.iterdir()
        if d.is_dir() and re.match(r"^\d{2}年\d{1,2}月文件$", d.name)
    )
    total = len(month_dirs)
    for idx, md in enumerate(month_dirs, 1):
        if on_progress:
            on_progress(idx, total)
        moved = _categorize_in_month(md, remapped=remapped)
        cat_count += moved
        _generate_summary(md)
        summary_count += 1
    if remapped:
        _rewrite_undo_log_destinations(remapped)
    return cat_count, summary_count


def fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}GB"
