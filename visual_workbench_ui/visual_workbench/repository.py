from __future__ import annotations

import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Iterator, Optional

from .database import connect
from .models import PRIORITY_ORDER, FileRecord, MonthlyEvaluation, Task, Todo


def _task_sort_key(task: Task) -> tuple:
    """进行中在前：按优先级、截止日期（空值排后）、创建时间倒序；已完成按完成时间倒序。"""
    if task.is_completed:
        return (1, -(task.completed_at or task.created_at).toordinal(), -(task.id or 0))
    due = task.due_date.toordinal() if task.due_date else 999999
    return (0, PRIORITY_ORDER.get(task.priority, 1), due, -task.created_at.toordinal(), -(task.id or 0))


class Repository:
    def __init__(self, database: Path):
        self.database = Path(database)
        self._local = threading.local()
        # 首次连接即建表/迁移（之后 connect() 会跳过，见 database.py）
        with self._connection():
            pass

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """取连接：每个线程复用同一个，用完不关（只提交/回滚事务）。

        为什么不每次新建再关闭：
        - 原来直接返回 connect()，调用方写 `with self._connection() as db:`。
          但 `with sqlite3.Connection` 只是**事务**上下文，**不会 close** ——
          51 个调用点每个泄漏一个连接和文件句柄，只能等 GC 兜底。
        - 也不能"每次用完就 close"：开了 WAL 之后，关闭**最后一个**连接会触发
          checkpoint（fsync）。实测 WAL 下 connect+close 要 49 ms，是裸开关的 96 倍。
          每次调用都关 = 每次都做一次 checkpoint，反而比泄漏时还慢。

        所以按线程缓存：线程池的线程数是固定的，连接数就有界（不泄漏），
        又不用反复开关（不触发 checkpoint）。
        """
        db = getattr(self._local, "db", None)
        if db is None:
            db = connect(self.database)
            self._local.db = db
        try:
            with db:  # 事务：正常退出 commit，抛异常则 rollback
                yield db
        except sqlite3.Error:
            # 连接本身坏了（磁盘拔了、库文件被换掉）就丢掉，下次重新建
            self._discard()
            raise

    def _discard(self) -> None:
        """丢弃当前线程的连接（出错恢复用；不主动调用）。"""
        db = getattr(self._local, "db", None)
        if db is not None:
            try:
                db.close()
            except sqlite3.Error:
                pass
            self._local.db = None

    def close(self) -> None:
        """显式收尾时调用（关掉当前线程的连接）。下一个线程会自己再开一个。"""
        self._discard()

    def set_root_directory(self, path: Path | str) -> None:
        with self._connection() as db:
            db.execute(
                "INSERT INTO settings(key, value) VALUES('root_directory', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(Path(path).resolve()),),
            )

    def get_root_directory(self) -> Optional[str]:
        with self._connection() as db:
            row = db.execute("SELECT value FROM settings WHERE key='root_directory'").fetchone()
        return row["value"] if row else None

    def set_archive_dest(self, path: Optional[str]) -> None:
        """归档目标位置（设置页可改）。传 None 或空串表示恢复默认。"""
        with self._connection() as db:
            db.execute(
                "INSERT INTO settings(key, value) VALUES('archive_dest', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                ("" if path is None else str(path).strip(),),
            )

    def get_archive_dest(self) -> Optional[str]:
        with self._connection() as db:
            row = db.execute("SELECT value FROM settings WHERE key='archive_dest'").fetchone()
        value = (row["value"] if row else "") or ""
        return value.strip() or None

    def set_window_backdrop(self, enabled: bool) -> None:
        """窗口磨砂背景开关（设置页可改）。"""
        with self._connection() as db:
            db.execute(
                "INSERT INTO settings(key, value) VALUES('window_backdrop', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                ("1" if enabled else "0",),
            )

    def get_window_backdrop(self) -> bool:
        with self._connection() as db:
            row = db.execute("SELECT value FROM settings WHERE key='window_backdrop'").fetchone()
        return bool(row and row["value"] == "1")

    def set_setting(self, key: str, value: str) -> None:
        """通用设置写入（产品库来源、视图密度这类界面偏好用）。"""
        with self._connection() as db:
            db.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )

    def get_setting(self, key: str, default: str = "") -> str:
        with self._connection() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        value = (row["value"] if row else "") or ""
        return value or default

    def migrate_library_root(self, root: Path | str) -> bool:
        """一次性迁移：仅当本机確實存在默认设计盘（或已有旧库需要切到有效 root）时自动设置，
        在他人无 G 盘的电脑上不写入不存在的盘符，避免首次启动显示失效路径或误扫整盘。"""
        with self._connection() as db:
            row = db.execute(
                "SELECT value FROM settings WHERE key='library_root_migrated'"
            ).fetchone()
            if row and row["value"] == "1":
                return False
            db.execute(
                "INSERT INTO settings(key, value) VALUES('library_root_migrated', '1') "
                "ON CONFLICT(key) DO UPDATE SET value = '1'",
            )
            target = Path(root)
            # 只有目标路径存在，且（不是盘符根目录，或盘内确有作者的标志性目录）时才自动挂载
            try:
                resolved_path = target.resolve()
                is_drive_root = resolved_path.parent == resolved_path
                has_author_marker = (resolved_path / "劲牌产品PNG").exists()
                if not resolved_path.exists() or (is_drive_root and not has_author_marker):
                    return False
                resolved = str(resolved_path)
            except OSError:
                return False
            db.execute(
                "INSERT INTO settings(key, value) VALUES('root_directory', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (resolved,),
            )
            sep_prefix = resolved if resolved.endswith(("\\", "/")) else resolved + os.sep
            db.execute(
                "UPDATE files SET is_available=0 WHERE absolute_path NOT LIKE ?",
                (sep_prefix + "%",),
            )
        return True

    def upsert_file(self, file: FileRecord) -> int:
        """单个文件入库。

        逻辑与批量版完全一致，直接复用 —— 这两处以前各有一份几乎相同的 SQL，
        加字段时很容易只改一处（内容签名就踩过这个坑）。
        """
        ids = self.upsert_files([file])
        return ids[0] if ids else 0

    def get_file_snapshot(self) -> dict[str, tuple[int | None, int, float, bool, str | None]]:
        """全量文件快照：{absolute_path: (id, size_bytes, modified_at, is_available, content_hash)}。
        扫描器用它做增量比对：未变化的文件直接跳过，不重复写库。"""
        with self._connection() as db:
            rows = db.execute(
                "SELECT absolute_path, id, size_bytes, modified_at, is_available, content_hash "
                "FROM files"
            ).fetchall()
        return {
            row["absolute_path"]: (
                row["id"], row["size_bytes"], row["modified_at"],
                bool(row["is_available"]), row["content_hash"],
            )
            for row in rows
        }

    def update_hashes(self, pairs: list[tuple[int, str]]) -> None:
        """回填内容签名（老库升级后，未变化的文件也要补算一次）。"""
        if not pairs:
            return
        with self._connection() as db:
            db.executemany("UPDATE files SET content_hash=? WHERE id=?", [(h, i) for i, h in pairs])

    def find_duplicate_files(self, limit: int | None = None) -> list[FileRecord]:
        """返回「内容签名相同」的素材，按签名聚在一起（同组内按文件名排序）。

        签名是「头尾 256KB + 体积」的哈希，不是全文件哈希：同一组内基本就是
        同一个文件的多份副本，但严格意义上是「疑似重复」，需要人工过一眼。
        """
        query = """
            SELECT * FROM files
            WHERE is_available = 1 AND content_hash IS NOT NULL AND content_hash IN (
                SELECT content_hash FROM files
                WHERE is_available = 1 AND content_hash IS NOT NULL
                GROUP BY content_hash HAVING COUNT(*) > 1
            )
            ORDER BY content_hash, file_name COLLATE NOCASE
        """
        params: list[object] = []
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        with self._connection() as db:
            rows = db.execute(query, params).fetchall()
        return [self._file_from_row(row) for row in rows]

    def count_duplicate_groups(self) -> tuple[int, int]:
        """返回（重复组数, 多余副本数）。多余副本 = 总数 - 组数，即理论上可省下的文件数。"""
        with self._connection() as db:
            row = db.execute(
                """SELECT COUNT(*) AS groups, COALESCE(SUM(n), 0) AS total FROM (
                       SELECT COUNT(*) AS n FROM files
                       WHERE is_available = 1 AND content_hash IS NOT NULL
                       GROUP BY content_hash HAVING COUNT(*) > 1
                   )"""
            ).fetchone()
        groups = int(row["groups"] or 0)
        return groups, max(0, int(row["total"] or 0) - groups)

    def upsert_files(self, files: Iterable[FileRecord]) -> list[int]:
        """批量 upsert（新增/修改的文件）：单个连接 + 单事务，避免几千个文件几千次开连接。"""
        ids: list[int] = []
        with self._connection() as db:
            for file in files:
                existing = db.execute(
                    "SELECT id, preview_path, preview_status, size_bytes, modified_at FROM files WHERE absolute_path=?",
                    (file.absolute_path,),
                ).fetchone()
                preview_path = file.preview_path
                preview_status = file.preview_status
                if existing and preview_path is None:
                    if (
                        existing["size_bytes"] != file.size_bytes
                        or existing["modified_at"] != file.modified_at
                    ):
                        preview_path = None
                        preview_status = "pending"
                    else:
                        preview_path = existing["preview_path"]
                        preview_status = existing["preview_status"]
                db.execute(
                    """INSERT INTO files(
                        absolute_path, relative_path, file_name, extension, category,
                        size_bytes, modified_at, preview_path, preview_status,
                        last_scanned_at, is_available, content_hash
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(absolute_path) DO UPDATE SET
                        relative_path=excluded.relative_path,
                        file_name=excluded.file_name,
                        extension=excluded.extension,
                        category=excluded.category,
                        size_bytes=excluded.size_bytes,
                        modified_at=excluded.modified_at,
                        preview_path=excluded.preview_path,
                        preview_status=excluded.preview_status,
                        last_scanned_at=excluded.last_scanned_at,
                        is_available=excluded.is_available,
                        content_hash=excluded.content_hash""",
                    (
                        file.absolute_path,
                        file.relative_path,
                        file.file_name,
                        file.extension,
                        file.category,
                        file.size_bytes,
                        file.modified_at,
                        preview_path,
                        preview_status,
                        file.last_scanned_at,
                        int(file.is_available),
                        file.content_hash,
                    ),
                )
                row = db.execute("SELECT id FROM files WHERE absolute_path=?", (file.absolute_path,)).fetchone()
                ids.append(int(row["id"]))
        return ids

    def touch_seen_paths(self, paths: list[str], scan_time: float) -> None:
        """本次扫描中「未变化、被跳过」的文件：只刷新 last_scanned_at，
        否则会被 mark_unavailable_not_seen 误判为已删除。分批（SQLite 参数上限 999）。"""
        if not paths:
            return
        with self._connection() as db:
            for index in range(0, len(paths), 500):
                batch = paths[index : index + 500]
                placeholders = ",".join("?" * len(batch))
                db.execute(
                    f"UPDATE files SET last_scanned_at=? WHERE absolute_path IN ({placeholders})",
                    [scan_time, *batch],
                )

    def update_preview(self, file_id: int, preview_path: Optional[str], status: str) -> None:
        with self._connection() as db:
            db.execute(
                "UPDATE files SET preview_path=?, preview_status=? WHERE id=?",
                (preview_path, status, file_id),
            )

    @staticmethod
    def _file_from_row(row: sqlite3.Row) -> FileRecord:
        return FileRecord(
            id=row["id"],
            absolute_path=row["absolute_path"],
            relative_path=row["relative_path"],
            file_name=row["file_name"],
            extension=row["extension"],
            category=row["category"],
            size_bytes=row["size_bytes"],
            modified_at=row["modified_at"],
            preview_path=row["preview_path"],
            preview_status=row["preview_status"],
            last_scanned_at=row["last_scanned_at"],
            is_available=bool(row["is_available"]),
            content_hash=row["content_hash"] if "content_hash" in row.keys() else None,
        )

    def get_file(self, file_id: int) -> Optional[FileRecord]:
        with self._connection() as db:
            row = db.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
        return self._file_from_row(row) if row else None

    def list_files(self, category: Optional[str] = None, limit: Optional[int] = None) -> list[FileRecord]:
        query = "SELECT * FROM files WHERE is_available=1"
        params: list[object] = []
        if category and category != "all":
            if category == "recent":
                query += " AND modified_at >= ?"
                params.append(time.time() - 30 * 86400)
            elif category == "unclassified":
                query += " AND category='other'"
            else:
                query += " AND category=?"
                params.append(category)
        query += " ORDER BY modified_at DESC, file_name COLLATE NOCASE"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        with self._connection() as db:
            rows = db.execute(query, params).fetchall()
        return [self._file_from_row(row) for row in rows]

    def search_files(
        self, text: str, category: Optional[str] = None, limit: Optional[int] = None
    ) -> list[FileRecord]:
        text = text.strip()
        if not text:
            return self.list_files(category, limit=limit)
        query = """SELECT * FROM files WHERE is_available=1 AND
            (file_name LIKE ? OR relative_path LIKE ? OR extension LIKE ?)"""
        like = f"%{text}%"
        params: list[object] = [like, like, like]
        if category and category != "all":
            if category == "recent":
                query += " AND modified_at >= ?"
                params.append(time.time() - 30 * 86400)
            else:
                query += " AND category=?"
                params.append("other" if category == "unclassified" else category)
        query += " ORDER BY modified_at DESC, file_name COLLATE NOCASE"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        with self._connection() as db:
            rows = db.execute(query, params).fetchall()
        return [self._file_from_row(row) for row in rows]

    def mark_unavailable_not_seen(self, root: Path, scan_time: float) -> None:
        resolved = str(root.resolve())
        prefix = resolved if resolved.endswith(("\\", "/")) else resolved + os.sep
        with self._connection() as db:
            db.execute(
                "UPDATE files SET is_available=0 WHERE absolute_path LIKE ? AND last_scanned_at < ?",
                (prefix + "%", scan_time),
            )

    def clear_screenshot_entries(self) -> None:
        """把文件名含'屏幕截图/screenshot/截图'的记录标记为不可用，不再显示在文件库。"""
        with self._connection() as db:
            db.execute(
                "UPDATE files SET is_available=0 WHERE file_name LIKE '%屏幕截图%' "
                "OR file_name LIKE '%screenshot%' OR file_name LIKE '%截图%'"
            )

    def create_task(
        self,
        text: str,
        created_at: Optional[date] = None,
        priority: str = "medium",
        due_date: Optional[date] = None,
    ) -> int:
        created_at = created_at or date.today()
        if priority not in ("high", "medium", "low"):
            priority = "medium"
        with self._connection() as db:
            cursor = db.execute(
                "INSERT INTO tasks(text, created_at, priority, due_date) VALUES(?, ?, ?, ?)",
                (text.strip(), created_at.isoformat(), priority, due_date.isoformat() if due_date else None),
            )
        return int(cursor.lastrowid)

    def delete_task(self, task_id: int) -> None:
        with self._connection() as db:
            db.execute("DELETE FROM task_files WHERE task_id=?", (task_id,))
            db.execute("DELETE FROM tasks WHERE id=?", (task_id,))

    def update_task_text(self, task_id: int, text: str) -> None:
        text = text.strip()
        if not text:
            return
        with self._connection() as db:
            db.execute("UPDATE tasks SET text=? WHERE id=?", (text, task_id))

    def update_task_priority(self, task_id: int, priority: str) -> None:
        if priority not in ("high", "medium", "low"):
            return
        with self._connection() as db:
            db.execute("UPDATE tasks SET priority=? WHERE id=?", (priority, task_id))

    def restore_task_snapshot(self, tasks: Iterable[Task]) -> None:
        """把任务表整体还原成给定快照（撤回 / 重做用）。

        按 id 对齐：快照里没有的删掉、多出来的插回去（保留原 id）、其余就地更新。
        只动 tasks 表，因此 task_files 关联不会丢。"""
        items = [t for t in tasks if t.id is not None]
        with self._connection() as db:
            existing = {row["id"] for row in db.execute("SELECT id FROM tasks")}
            wanted = {t.id for t in items}
            for stale_id in existing - wanted:
                db.execute("DELETE FROM task_files WHERE task_id=?", (stale_id,))
                db.execute("DELETE FROM tasks WHERE id=?", (stale_id,))
            for task in items:
                values = (
                    task.text.strip(),
                    task.created_at.isoformat(),
                    task.completed_at.isoformat() if task.completed_at else None,
                    int(task.is_completed),
                    task.priority if task.priority in ("high", "medium", "low") else "medium",
                    task.due_date.isoformat() if task.due_date else None,
                )
                if task.id in existing:
                    db.execute(
                        "UPDATE tasks SET text=?, created_at=?, completed_at=?, is_completed=?, "
                        "priority=?, due_date=? WHERE id=?",
                        (*values, task.id),
                    )
                else:
                    db.execute(
                        "INSERT INTO tasks(id, text, created_at, completed_at, is_completed, "
                        "priority, due_date) VALUES(?, ?, ?, ?, ?, ?, ?)",
                        (task.id, *values),
                    )

    @staticmethod
    def _task_from_row(row: sqlite3.Row) -> Task:
        keys = row.keys()
        return Task(
            id=row["id"],
            text=row["text"],
            created_at=date.fromisoformat(row["created_at"]),
            completed_at=date.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
            is_completed=bool(row["is_completed"]),
            priority=row["priority"] if "priority" in keys else "medium",
            due_date=date.fromisoformat(row["due_date"]) if "due_date" in keys and row["due_date"] else None,
        )

    def list_tasks(self, include_completed: bool = True) -> list[Task]:
        query = "SELECT * FROM tasks"
        if not include_completed:
            query += " WHERE is_completed=0"
        with self._connection() as db:
            rows = db.execute(query).fetchall()
        tasks = [self._task_from_row(row) for row in rows]
        return sorted(tasks, key=_task_sort_key)

    def count_tasks_created_between(self, start: date, end: date) -> int:
        with self._connection() as db:
            row = db.execute(
                "SELECT COUNT(*) AS n FROM tasks WHERE created_at BETWEEN ? AND ?",
                (start.isoformat(), end.isoformat()),
            ).fetchone()
        return int(row["n"])

    def completed_counts_by_week(self, anchor: date, weeks: int = 12) -> list[tuple[date, int]]:
        """最近 weeks 周（含 anchor 所在那周，周一起算）每周完成任务数，时间从早到晚。

        一次查询取回区间内所有完成时间，再在内存里分桶 —— 比按周发 12 次查询快。
        """
        anchor_start = anchor - timedelta(days=anchor.weekday())
        starts = [anchor_start - timedelta(days=7 * offset)
                  for offset in range(weeks - 1, -1, -1)]
        counts = {start: 0 for start in starts}
        with self._connection() as db:
            rows = db.execute(
                "SELECT completed_at FROM tasks WHERE is_completed = 1 "
                "AND completed_at IS NOT NULL AND completed_at >= ? AND completed_at <= ?",
                (starts[0].isoformat(), (starts[-1] + timedelta(days=6)).isoformat()),
            ).fetchall()
        for row in rows:
            try:
                day = date.fromisoformat(row["completed_at"])
            except (TypeError, ValueError):
                continue
            week_start = day - timedelta(days=day.weekday())
            if week_start in counts:
                counts[week_start] += 1
        return [(start, counts[start]) for start in starts]

    def get_dashboard_stats(self, now=None) -> dict[str, int]:
        """首页概览统计：一次性查询素材总量、PSD数、图片数、本月出图数与今日完成任务数。"""
        from datetime import datetime as _dt
        now = now or _dt.now()
        month_start = _dt(now.year, now.month, 1).timestamp()
        with self._connection() as db:
            total = db.execute("SELECT COUNT(*) n FROM files WHERE is_available=1").fetchone()["n"]
            psd = db.execute(
                "SELECT COUNT(*) n FROM files WHERE is_available=1 AND category='psd'"
            ).fetchone()["n"]
            image = db.execute(
                "SELECT COUNT(*) n FROM files WHERE is_available=1 AND category='image'"
            ).fetchone()["n"]
            month_images = db.execute(
                "SELECT COUNT(*) n FROM files WHERE is_available=1 AND category='image' AND modified_at >= ?",
                (month_start,),
            ).fetchone()["n"]
            month_all = db.execute(
                "SELECT COUNT(*) n FROM files WHERE is_available=1 AND modified_at >= ?",
                (month_start,),
            ).fetchone()["n"]
            done_today = db.execute(
                "SELECT COUNT(*) n FROM tasks WHERE is_completed=1 AND completed_at=?",
                (date.today().isoformat(),),
            ).fetchone()["n"]
        return {
            "total": int(total),
            "psd": int(psd),
            "image": int(image),
            "month_images": int(month_images),
            "month_all": int(month_all),
            "done_today": int(done_today),
        }

    def count_files_by_category(self) -> dict[str, int]:
        with self._connection() as db:
            rows = db.execute(
                "SELECT category, COUNT(*) AS n FROM files WHERE is_available=1 GROUP BY category"
            ).fetchall()
        return {row["category"]: int(row["n"]) for row in rows}

    def complete_task(self, task_id: int, completed_at: Optional[date] = None) -> None:
        completed_at = completed_at or date.today()
        with self._connection() as db:
            db.execute(
                "UPDATE tasks SET is_completed=1, completed_at=? WHERE id=?",
                (completed_at.isoformat(), task_id),
            )

    def reopen_task(self, task_id: int) -> None:
        with self._connection() as db:
            db.execute("UPDATE tasks SET is_completed=0, completed_at=NULL WHERE id=?", (task_id,))

    def get_completed_tasks(self, start: date, end: date) -> list[Task]:
        with self._connection() as db:
            rows = db.execute(
                "SELECT * FROM tasks WHERE is_completed=1 AND completed_at BETWEEN ? AND "
                "? ORDER BY completed_at, id",
                (start.isoformat(), end.isoformat()),
            ).fetchall()
        return [self._task_from_row(row) for row in rows]

    def save_monthly_evaluation(self, item: MonthlyEvaluation) -> None:
        with self._connection() as db:
            db.execute(
                """INSERT INTO monthly_evaluations(month, summary_text, evaluation, score, weight)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(month) DO UPDATE SET
                    summary_text=excluded.summary_text,
                    evaluation=CASE WHEN excluded.evaluation != '' THEN excluded.evaluation ELSE monthly_evaluations.evaluation END,
                    score=COALESCE(excluded.score, monthly_evaluations.score),
                    weight=COALESCE(excluded.weight, monthly_evaluations.weight)""",
                (item.month, item.summary_text, item.evaluation, item.score, item.weight),
            )

    def get_monthly_evaluation(self, month: str) -> MonthlyEvaluation:
        with self._connection() as db:
            row = db.execute("SELECT * FROM monthly_evaluations WHERE month=?", (month,)).fetchone()
        if not row:
            return MonthlyEvaluation(month=month)
        return MonthlyEvaluation(
            id=row["id"], month=row["month"], summary_text=row["summary_text"],
            evaluation=row["evaluation"], score=row["score"], weight=row["weight"],
        )

    def save_weekly_report(self, week_start: date, content: str) -> None:
        """保存某周（以周一起始）的周清文本，重复保存覆盖。"""
        with self._connection() as db:
            db.execute(
                """INSERT INTO weekly_reports(week_start, content, updated_at)
                VALUES(?, ?, ?)
                ON CONFLICT(week_start) DO UPDATE SET
                    content=excluded.content,
                    updated_at=excluded.updated_at""",
                (week_start.isoformat(), content, date.today().isoformat()),
            )

    def get_weekly_report(self, week_start: date) -> Optional[str]:
        """读取某周已保存的周清文本，未保存返回 None。"""
        with self._connection() as db:
            row = db.execute(
                "SELECT content FROM weekly_reports WHERE week_start=?", (week_start.isoformat(),)
            ).fetchone()
        return row["content"] if row else None

    def attach_file(self, task_id: int, file_id: int) -> None:
        with self._connection() as db:
            db.execute("INSERT OR IGNORE INTO task_files(task_id, file_id) VALUES(?, ?)", (task_id, file_id))

    # ---------- 待办 / 日程 ----------

    @staticmethod
    def _todo_from_row(row: sqlite3.Row) -> Todo:
        return Todo(
            id=row["id"],
            title=row["title"],
            todo_date=date.fromisoformat(row["todo_date"]),
            remind_time=row["remind_time"],
            group_id=row["group_id"],
            is_done=bool(row["is_done"]),
            reminded=bool(row["reminded"]),
        )

    def create_todos(
        self,
        title: str,
        start_date: date,
        days: int = 1,
        remind_time: Optional[str] = None,
    ) -> int:
        """批量创建待办：从 start_date 起连续 days 天，每天一条；返回 group_id 便于整组删除。"""
        title = title.strip()
        if not title:
            return 0
        days = max(1, min(days, 90))
        with self._connection() as db:
            cursor = db.execute("INSERT INTO todos(title, todo_date, remind_time) VALUES('', '', NULL)")
            group_id = int(cursor.lastrowid)
            for offset in range(days):
                db.execute(
                    "INSERT INTO todos(title, todo_date, remind_time, group_id) VALUES(?, ?, ?, ?)",
                    (title, (start_date + timedelta(days=offset)).isoformat(), remind_time, group_id),
                )
            db.execute("DELETE FROM todos WHERE id=?", (group_id,))  # 移除占位行
        return group_id

    def list_todos(self, todo_date: date) -> list[Todo]:
        """某一天的待办，未完成在前、按提醒时间排序。"""
        with self._connection() as db:
            rows = db.execute(
                "SELECT * FROM todos WHERE todo_date=? ORDER BY is_done ASC, remind_time IS NULL, remind_time, id",
                (todo_date.isoformat(),),
            ).fetchall()
        return [self._todo_from_row(row) for row in rows]

    def list_todo_dates_in_month(self, year: int, month: int) -> set[str]:
        """当月有待办的日期集合（供日历标记圆点）。"""
        prefix = f"{year:04d}-{month:02d}-"
        with self._connection() as db:
            rows = db.execute(
                "SELECT DISTINCT todo_date FROM todos WHERE todo_date LIKE ?", (prefix + "%",)
            ).fetchall()
        return {row["todo_date"] for row in rows}

    def count_todos_in_month(self, year: int, month: int) -> int:
        prefix = f"{year:04d}-{month:02d}-"
        with self._connection() as db:
            row = db.execute(
                "SELECT COUNT(*) AS n FROM todos WHERE todo_date LIKE ?", (prefix + "%",)
            ).fetchone()
        return int(row["n"])

    def set_todo_done(self, todo_id: int, done: bool) -> None:
        with self._connection() as db:
            db.execute("UPDATE todos SET is_done=?, reminded=0 WHERE id=?", (int(done), todo_id))

    def delete_todo(self, todo_id: int) -> None:
        with self._connection() as db:
            db.execute("DELETE FROM todos WHERE id=?", (todo_id,))

    def delete_todo_group(self, group_id: int) -> None:
        with self._connection() as db:
            db.execute("DELETE FROM todos WHERE group_id=?", (group_id,))

    def todo_group_size(self, group_id: int) -> int:
        with self._connection() as db:
            row = db.execute("SELECT COUNT(*) AS n FROM todos WHERE group_id=?", (group_id,)).fetchone()
        return int(row["n"])

    def get_due_todos(self, todo_date: date, now_time: str) -> list[Todo]:
        """当天到期且未提醒、有提醒时间且时间已到的待办（供提醒器调用）。"""
        with self._connection() as db:
            rows = db.execute(
                "SELECT * FROM todos WHERE todo_date=? AND is_done=0 AND reminded=0 "
                "AND remind_time IS NOT NULL AND remind_time <= ? ORDER BY remind_time",
                (todo_date.isoformat(), now_time),
            ).fetchall()
        return [self._todo_from_row(row) for row in rows]

    def mark_todo_reminded(self, todo_id: int) -> None:
        with self._connection() as db:
            db.execute("UPDATE todos SET reminded=1 WHERE id=?", (todo_id,))

    # ---------- 产品库 ----------

    def set_product_folder(self, folder_ref: Optional[str]) -> None:
        """手动指定产品库文件夹：传入根目录下的子文件夹名、绝对路径，或 None 恢复自动识别。"""
        key = "product_folder"
        with self._connection() as db:
            db.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, folder_ref or ""),
            )

    def get_product_folder_setting(self) -> Optional[str]:
        """读取手动指定的产品库文件夹（空值/未设置返回 None = 自动识别）。"""
        with self._connection() as db:
            row = db.execute("SELECT value FROM settings WHERE key='product_folder'").fetchone()
        value = row["value"] if row else ""
        return value or None

    def find_product_folder(self, root: Path | str) -> Optional[str]:
        """返回产品库文件夹引用：手动指定的优先（根目录下子文件夹名或绝对路径）；
        未指定则自动识别根目录第一层名字含「产品」的文件夹。找不到返回 None。"""
        manual = self.get_product_folder_setting()
        if manual:
            if os.path.isabs(manual):
                return manual  # 文件库之外的任意文件夹（绝对路径）
            return manual  # 根目录下的子文件夹名
        root = Path(root)
        if not root.exists() or not root.is_dir():
            return None
        candidates = [d.name for d in root.iterdir() if d.is_dir() and "产品" in d.name]
        if not candidates:
            return None
        for keyword in ("产品png", "产品图", "产品库", "产品素材"):
            lowered = keyword.lower()
            for name in candidates:
                if lowered in name.lower():
                    return name
        return candidates[0]

    def list_files_in_folder(self, folder_ref: str, keyword: str = "") -> list[FileRecord]:
        """列出产品库文件夹下的全部可用文件，可选关键词过滤。
        folder_ref 可以是根目录下的子文件夹名（按 relative_path 匹配），
        也可以是绝对路径（按 absolute_path 匹配，支持文件库外的文件夹）。"""
        if os.path.isabs(folder_ref):
            column = "absolute_path"
            prefix = folder_ref.rstrip(os.sep) + os.sep
        else:
            column = "relative_path"
            prefix = folder_ref + os.sep
        with self._connection() as db:
            if keyword:
                rows = db.execute(
                    f"SELECT * FROM files WHERE is_available=1 AND {column} LIKE ? "
                    "AND (file_name LIKE ? OR relative_path LIKE ?) ORDER BY modified_at DESC, id",
                    (prefix + "%", f"%{keyword}%", f"%{keyword}%"),
                ).fetchall()
            else:
                rows = db.execute(
                    f"SELECT * FROM files WHERE is_available=1 AND {column} LIKE ? "
                    "ORDER BY modified_at DESC, id",
                    (prefix + "%",),
                ).fetchall()
        return [self._file_from_row(row) for row in rows]
