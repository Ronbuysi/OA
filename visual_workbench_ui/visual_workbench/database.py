from __future__ import annotations

import sqlite3
import threading
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS files (
  id INTEGER PRIMARY KEY,
  absolute_path TEXT NOT NULL UNIQUE,
  relative_path TEXT NOT NULL,
  file_name TEXT NOT NULL,
  extension TEXT NOT NULL,
  category TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  modified_at REAL NOT NULL,
  preview_path TEXT,
  preview_status TEXT NOT NULL DEFAULT 'pending',
  last_scanned_at REAL NOT NULL,
  is_available INTEGER NOT NULL DEFAULT 1,
  content_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_files_category ON files(category);
CREATE INDEX IF NOT EXISTS idx_files_modified ON files(modified_at DESC);
CREATE INDEX IF NOT EXISTS idx_files_available ON files(is_available);
CREATE INDEX IF NOT EXISTS idx_files_category_available ON files(category, is_available);
-- 注意：content_hash 的索引不能写在这里。老库的 files 表还没这一列，
-- executescript 会先于 _migrate() 执行，建索引会直接报 no such column。
-- 它统一在 _migrate() 里加完列之后再建。
CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY,
  text TEXT NOT NULL,
  created_at TEXT NOT NULL,
  completed_at TEXT,
  is_completed INTEGER NOT NULL DEFAULT 0,
  priority TEXT NOT NULL DEFAULT 'medium',
  due_date TEXT
);
CREATE TABLE IF NOT EXISTS task_files (
  task_id INTEGER NOT NULL REFERENCES tasks(id),
  file_id INTEGER NOT NULL REFERENCES files(id),
  PRIMARY KEY(task_id, file_id)
);
CREATE TABLE IF NOT EXISTS monthly_evaluations (
  id INTEGER PRIMARY KEY,
  month TEXT NOT NULL UNIQUE,
  summary_text TEXT NOT NULL DEFAULT '',
  evaluation TEXT NOT NULL DEFAULT '',
  score REAL,
  weight REAL
);
CREATE TABLE IF NOT EXISTS weekly_reports (
  week_start TEXT PRIMARY KEY,
  content TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS todos (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  todo_date TEXT NOT NULL,
  remind_time TEXT,
  group_id INTEGER,
  is_done INTEGER NOT NULL DEFAULT 0,
  reminded INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_todos_date ON todos(todo_date);
CREATE INDEX IF NOT EXISTS idx_todos_group ON todos(group_id);
"""


# 每个库路径在本进程里只建一次表。（原来每次 connect() 都跑一遍全量建表脚本
# —— 8 个 CREATE TABLE + 6 个索引 + 2 次 PRAGMA table_info，实测 1.24 ms/次，
# 而 Repository 有 51 个调用点，等于每次读写都白付一遍"建库"的钱。）
_INITIALIZED: set[str] = set()
_INIT_LOCK = threading.Lock()


def connect(path: Path) -> sqlite3.Connection:
    """打开连接。建表/迁移只在首次做一次，之后只负责连接和 PRAGMA。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    # WAL：读写不再互斥。扫描在后台线程批量写库，界面线程同时读预览/列表，
    # 默认的 rollback journal 下会直接抛 "database is locked"。
    connection.execute("PRAGMA journal_mode = WAL")
    # 拿不到写锁时最多等 5 秒再失败，而不是立刻报错
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA synchronous = NORMAL")

    key = str(path)
    with _INIT_LOCK:
        # 持锁执行：避免两个线程同时判定"首次"后，另一个抢在建表完成前就去读写
        if key not in _INITIALIZED:
            connection.executescript(SCHEMA)
            _migrate(connection)
            connection.commit()
            _INITIALIZED.add(key)
    return connection


def _migrate(connection: sqlite3.Connection) -> None:
    """老库补充新列；CREATE TABLE IF NOT EXISTS 不会自动加列。"""
    task_columns = {row[1] for row in connection.execute("PRAGMA table_info(tasks)")}
    if "priority" not in task_columns:
        connection.execute("ALTER TABLE tasks ADD COLUMN priority TEXT NOT NULL DEFAULT 'medium'")
    if "due_date" not in task_columns:
        connection.execute("ALTER TABLE tasks ADD COLUMN due_date TEXT")

    file_columns = {row[1] for row in connection.execute("PRAGMA table_info(files)")}
    if "content_hash" not in file_columns:
        connection.execute("ALTER TABLE files ADD COLUMN content_hash TEXT")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_files_content_hash ON files(content_hash)")
