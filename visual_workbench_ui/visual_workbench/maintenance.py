"""工作台自己的维护动作：预览缓存清理、数据库备份。

界面（设置页）和命令行工具（tools/clean_preview_cache.py）共用这一份逻辑，
避免两边各写一套、改一处漏一处。
"""
from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

# 可用素材里源文件的存活率低于这个值，就认为磁盘/网盘没挂载，一律中止不删
MIN_ALIVE_RATIO = 0.8


def cache_usage(cache_dir: Path) -> tuple[int, int]:
    """返回（缓存文件数, 占用字节）。目录不存在返回 (0, 0)。"""
    if not cache_dir.exists():
        return 0, 0
    count = 0
    total = 0
    for entry in os.scandir(cache_dir):
        try:
            total += entry.stat().st_size
            count += 1
        except OSError:
            continue
    return count, total


def fmt_size(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num_bytes) < 1024 or unit == "GB":
            return f"{num_bytes:.0f} {unit}" if unit in ("B", "KB") else f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} GB"


@dataclass
class CleanupPlan:
    """一次清理的预案。只读计算，不碰任何文件。"""

    cache_files: int = 0
    cache_bytes: int = 0
    dead_rows: list[int] = field(default_factory=list)
    dead_cache: list[str] = field(default_factory=list)
    dead_bytes: int = 0
    available_records: int = 0
    alive_records: int = 0
    blocked_reason: str = ""

    @property
    def blocked(self) -> bool:
        return bool(self.blocked_reason)

    @property
    def freed_bytes(self) -> int:
        return self.dead_bytes


def build_cleanup_plan(db_path: Path, cache_dir: Path) -> CleanupPlan:
    """算出「哪些预览缓存已经没用了」——即源文件已不在磁盘上的那些。

    图片还在的预览一律保留；源文件没变的缓存也保留（它们下次扫描还能命中）。
    计算过程只读，不会删除任何东西。
    """
    plan = CleanupPlan()
    plan.cache_files, plan.cache_bytes = cache_usage(cache_dir)
    if not db_path.exists():
        plan.blocked_reason = "找不到数据库文件"
        return plan

    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, absolute_path, preview_path, is_available FROM files"
        ).fetchall()
        missing_rows = []
        for row in rows:
            alive = os.path.exists(row["absolute_path"])
            if row["is_available"]:
                plan.available_records += 1
                if alive:
                    plan.alive_records += 1
            if not alive:
                missing_rows.append(row)

        if plan.available_records and plan.alive_records / plan.available_records < MIN_ALIVE_RATIO:
            plan.blocked_reason = (
                f"可用素材里只有 {plan.alive_records}/{plan.available_records} 个源文件找得到，"
                "像是磁盘或网盘没挂载。为避免误删，已中止。"
            )
            return plan

        for row in missing_rows:
            plan.dead_rows.append(row["id"])
            preview = row["preview_path"]
            if not preview or not os.path.exists(preview):
                continue
            try:
                plan.dead_bytes += os.path.getsize(preview)
            except OSError:
                continue
            plan.dead_cache.append(preview)
    finally:
        conn.close()
    return plan


def backup_database(db_path: Path) -> Path:
    """整库备份，并校验内容。失败直接抛异常，调用方不要当成成功。

    用 SQLite 官方的 backup API，不是直接复制文件：开了 WAL 之后，还没 checkpoint
    的提交躺在 `-wal` 文件里，直接 copy 主库会漏掉这部分 —— 备份看着成功其实是残缺的。
    """
    backup = Path(str(db_path) + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
    source = sqlite3.connect(str(db_path))
    try:
        target = sqlite3.connect(str(backup))
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()

    # 校验：比"副本和原库字节数相同"更实在 —— 真的打开它做完整性检查 + 核对记录数
    check = sqlite3.connect(str(backup))
    try:
        verdict = check.execute("PRAGMA integrity_check").fetchone()[0]
        if verdict != "ok":
            raise OSError(f"备份校验失败：完整性检查未通过（{verdict}）")
    finally:
        check.close()
    if _count_rows(db_path, "files") != _count_rows(backup, "files"):
        raise OSError("备份校验失败：副本与原库的素材记录数不一致")
    return backup


def _count_rows(db_path: Path, table: str) -> int:
    connection = sqlite3.connect(str(db_path))
    try:
        return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        connection.close()


def apply_cleanup(
    db_path: Path, plan: CleanupPlan, include_db: bool = True, progress=None
) -> tuple[int, int, Path | None]:
    """执行预案，返回（删除的缓存文件数, 删除的记录数, 备份路径）。

    include_db=True 时会先备份数据库再删记录，并 VACUUM 回收空间。
    单个文件删不掉（被占用等）只计数，不中断整体流程。
    """
    removed = 0
    for index, path in enumerate(plan.dead_cache, start=1):
        try:
            os.remove(path)
            removed += 1
        except OSError:
            pass
        if progress and index % 500 == 0:
            progress(index, len(plan.dead_cache))

    backup: Path | None = None
    removed_rows = 0
    if include_db and plan.dead_rows:
        backup = backup_database(db_path)
        conn = sqlite3.connect(str(db_path), timeout=10.0)
        try:
            conn.execute("PRAGMA busy_timeout = 5000")
            dead_params = [(i,) for i in plan.dead_rows]
            conn.executemany("DELETE FROM task_files WHERE file_id = ?", dead_params)
            conn.executemany("DELETE FROM files WHERE id = ?", dead_params)
            conn.commit()
            try:
                conn.execute("VACUUM")
                conn.commit()
            except sqlite3.OperationalError:
                pass
        finally:
            conn.close()
        removed_rows = len(plan.dead_rows)
    return removed, removed_rows, backup
