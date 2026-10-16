from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable, Optional

from .models import Task

CATEGORY_LABELS = (("psd", "PSD源文件"), ("image", "输出图片"), ("workorder", "工单文件"))


def week_bounds(day: date) -> tuple[date, date]:
    start = day - timedelta(days=day.weekday())
    return start, start + timedelta(days=6)


def _sentence(task: Task) -> str:
    """用户实际周清格式：已完成。8月3日完成XXX设计。"""
    text = task.text.strip().rstrip("。.!！")
    return f"已完成。{task.completed_at.month}月{task.completed_at.day}日完成{text}。"


def generate_weekly_text(
    completed: Iterable[Task],
    open_tasks: Iterable[Task] = (),
    start: Optional[date] = None,
    end: Optional[date] = None,
    created_count: int = 0,
    file_stats: Optional[dict[str, int]] = None,
) -> str:
    done = sorted((t for t in completed if t.completed_at), key=lambda t: (t.completed_at, t.id or 0))
    if not done:
        return "（本周暂无完成任务，去任务页勾选完成的任务后会自动出现在这里）"
    return "\n".join(_sentence(t) for t in done)


def weeks_in_month(year: int, month: int) -> list[tuple[date, date]]:
    """返回某月覆盖的所有周（周一为每周起点，允许跨月），按时间顺序。"""
    first = date(year, month, 1)
    if month == 12:
        last = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    start = first - timedelta(days=first.weekday())
    weeks: list[tuple[date, date]] = []
    while start <= last:
        weeks.append((start, start + timedelta(days=6)))
        start += timedelta(days=7)
    return weeks


def _week_heading(start: date, end: date, index: int) -> str:
    return f"第{index}周（{start.month}月{start.day}日-{end.month}月{end.day}日）"


def generate_monthly_from_weeks(
    month_label: str,
    weeks: Iterable[tuple[date, date, str]],
) -> str:
    """月度总结 = 该月每周已保存的周清文本按周拼合，周与周之间空行。"""
    lines = [f"【月度总结】{month_label}"]
    blank = True
    for index, (start, end, content) in enumerate(weeks, 1):
        lines.append("")
        lines.append(_week_heading(start, end, index))
        content = (content or "").strip()
        lines.append(content if content else "（本周无记录）")
        blank = False
    if blank:
        return f"【月度总结】{month_label}\n（本月暂无周清记录）"
    return "\n".join(lines)
