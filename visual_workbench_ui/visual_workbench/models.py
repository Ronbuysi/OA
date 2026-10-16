from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass(frozen=True)
class FileRecord:
    absolute_path: str
    relative_path: str
    file_name: str
    extension: str
    category: str
    size_bytes: int
    modified_at: float
    preview_path: Optional[str] = None
    preview_status: str = "pending"
    last_scanned_at: float = 0.0
    is_available: bool = True
    content_hash: Optional[str] = None
    id: Optional[int] = None


@dataclass(frozen=True)
class Task:
    text: str
    created_at: date
    completed_at: Optional[date] = None
    is_completed: bool = False
    id: Optional[int] = None
    priority: str = "medium"  # high / medium / low
    due_date: Optional[date] = None


PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


@dataclass(frozen=True)
class Todo:
    title: str
    todo_date: date
    remind_time: Optional[str] = None  # "HH:MM"，无提醒为 None
    group_id: Optional[int] = None     # 批量创建的条目共享组 id（用于整组删除）
    is_done: bool = False
    reminded: bool = False             # 当天提醒是否已发送
    id: Optional[int] = None


@dataclass(frozen=True)
class MonthlyEvaluation:
    month: str
    summary_text: str = ""
    evaluation: str = ""
    score: Optional[float] = None
    weight: Optional[float] = None
    id: Optional[int] = None
