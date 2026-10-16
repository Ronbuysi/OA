from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Callable, Iterator, Optional

from .classifier import RECORDED_CATEGORIES, classify_extension
from .models import FileRecord
from .repository import Repository


# 算内容签名时每个文件最多读多少：头、尾各读这么多字节
SIGNATURE_CHUNK = 256 * 1024


def file_signature(path: Path, size: int) -> Optional[str]:
    """算一个内容签名，用来找重复素材。

    小文件整份读；大文件只读「头部 + 尾部」各 256KB，再把体积拼进哈希。
    这样几万个素材总共只读几百 MB，而不是几十 GB —— 对设计文件来说
    （PSD / AI 开头是结构头、结尾是收尾数据），头尾和体积都一致基本就是同一个文件。

    严格讲这是「疑似重复」的判据，不是密码学意义上的全文件哈希。
    读不了（占用 / 权限 / 0 字节）返回 None，不影响扫描继续。
    """
    if size <= 0:
        return None
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            if size <= SIGNATURE_CHUNK * 2:
                digest.update(handle.read())
            else:
                digest.update(handle.read(SIGNATURE_CHUNK))
                handle.seek(-SIGNATURE_CHUNK, os.SEEK_END)
                digest.update(handle.read(SIGNATURE_CHUNK))
    except OSError:
        return None
    digest.update(str(size).encode("ascii"))
    return digest.hexdigest()


def scan_root(
    root: Path | str,
    repository: Repository,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> list[int]:
    """增量扫描：遍历目录是必须的（用于发现新文件），但只有「新增/修改过」的文件
    才会写库——未变化的文件靠内存快照比对直接跳过，只刷新扫描时间戳。
    耗时从「∝文件总数」降为「∝变化量」，文件再多启动扫描也快。

    内容签名（用于找重复）只在必要时才算：新文件、被改过的文件，
    以及老库里还没算过签名的存量文件（升级后补算一次，之后就不再重复读）。
    """
    root = Path(root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(root)
    scan_time = time.time()
    snapshot = repository.get_file_snapshot()  # {path: (id, size, mtime, available, hash)}
    files = list(_iter_files(root))
    total = len(files)
    changed: list[FileRecord] = []
    seen_paths: list[str] = []  # 未变化文件：只刷新扫描时间戳，避免被误判删除
    pending_hashes: list[tuple[int, str]] = []  # 存量文件补算的签名
    result: list[int] = []
    for index, path in enumerate(files, start=1):
        try:
            stat = path.stat()
        except OSError:
            # 扫描过程中文件被移动/删除或无权访问，跳过而不是中断整个扫描
            if on_progress:
                on_progress(index, total)
            continue
        key = str(path)
        category = classify_extension(path.suffix)
        if category not in RECORDED_CATEGORIES:
            # 文件库只收录 图片/设计源文件，其余不入库
            if on_progress:
                on_progress(index, total)
            continue
        known = snapshot.get(key)
        if (
            known is not None
            and known[1] == stat.st_size
            and known[2] == stat.st_mtime
            and known[3]  # 已可用（未被标记删除）
        ):
            # 未变化：跳过写库，保留预览缓存，仅刷新时间戳
            seen_paths.append(key)
            if known[0] is not None:
                result.append(known[0])
                if known[4] is None:
                    signature = file_signature(path, stat.st_size)
                    if signature:
                        pending_hashes.append((known[0], signature))
        else:
            changed.append(FileRecord(
                absolute_path=key,
                relative_path=str(path.relative_to(root)),
                file_name=path.name,
                extension=path.suffix.lower(),
                category=category,
                size_bytes=stat.st_size,
                modified_at=stat.st_mtime,
                last_scanned_at=scan_time,
                content_hash=file_signature(path, stat.st_size),
            ))
        if on_progress:
            on_progress(index, total)
    result.extend(repository.upsert_files(changed))
    repository.update_hashes(pending_hashes)
    repository.touch_seen_paths(seen_paths, scan_time)
    repository.mark_unavailable_not_seen(root, scan_time)
    return result


def _iter_files(root: Path) -> Iterator[Path]:
    """遍历 root 下的文件；目录在遍历中途消失或无权限时跳过，不抛出异常。"""

    def _on_error(_error: OSError) -> None:
        return None

    for dirpath, dirnames, filenames in os.walk(root, onerror=_on_error):
        dirnames[:] = [
            name for name in dirnames
            if not _skip_name(name) and name.lower() not in _SKIP_DIRS
            and not _SCREENSHOT_DIR_RE.match(name)
        ]
        for name in filenames:
            if _skip_name(name):
                continue
            yield Path(dirpath) / name


_SKIP_DIRS = {
    "__pycache__", ".venv", "venv", "build", "dist", "node_modules",
    # 系统目录（扫整个 G 盘时跳过）
    "$recycle.bin", "system volume information", "$baksd", "$lbak",
    # 截图文件夹不进文件库
    "screenshots", "截图", "屏幕截图", "大屏截图",
}

# 系统截图文件夹（26年截图/25年截图…），不算设计素材，不入库
_SCREENSHOT_DIR_RE = __import__("re").compile(r"^\d{2}年截图$")

# 截图类文件名不入库
_SCREENSHOT_NAME_RE = __import__("re").compile(r"(屏幕截图|screenshot|截图)", __import__("re").IGNORECASE)


def _skip_name(name: str) -> bool:
    return (name.startswith("~$")
            or name.startswith(".")
            or name == "__pycache__"
            or _SCREENSHOT_NAME_RE.search(name))
