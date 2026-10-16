from __future__ import annotations

from datetime import date

from PySide6.QtCore import (
    QEasingCurve, QPropertyAnimation, Qt, QTimer, QVariantAnimation,
)
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFrame, QGraphicsOpacityEffect,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
    QVBoxLayout, QWidget,
)

from ..models import Task
from ..reports import week_bounds
from ..repository import Repository
from .layout import page_body
from .styles import (
    _ACCENT, _BORDER_LIGHT, _CARD_BG, _SEGMENTED_BG,
    _TEXT_PRIMARY, _TEXT_SECONDARY, _TEXT_TERTIARY, priority_badge_colors,
)

PRIORITY_LABELS = {"high": "高优先", "medium": "普通", "low": "低优先"}
PRIORITY_CYCLE = ["medium", "high", "low"]

MAX_HISTORY = 100


def _short(text: str, limit: int = 14) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _badge_style(pair: tuple[str, str]) -> str:
    background, foreground = pair
    return (
        f"color:{foreground};background:{background};border-radius:6px;"
        "padding:3px 9px;font-size:11px;font-weight:600;border:0;"
    )


class _HistoryEntry:
    """一条可撤回的操作：只记录「操作前」的任务快照，撤回时整体还原。"""

    __slots__ = ("label", "snapshot")

    def __init__(self, label: str, snapshot: list[Task]):
        self.label = label
        self.snapshot = snapshot


class _Toast(QLabel):
    """页面底部浮出的一句话提示（撤回 / 重做反馈），2.4 秒后自动淡出。"""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedHeight(38)
        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(1.0)
        self.setGraphicsEffect(self._effect)
        self._fade = QPropertyAnimation(self._effect, b"opacity", self)
        self._fade.setDuration(260)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.finished.connect(self._on_fade_done)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fade_out)
        self.hide()

    def restyle(self):
        from .styles import _BORDER_STRONG, _CARD_BG, _TEXT_PRIMARY
        self.setStyleSheet(
            f"QLabel#Toast{{background:{_CARD_BG};color:{_TEXT_PRIMARY};"
            f"border:1px solid {_BORDER_STRONG};border-radius:12px;"
            f"padding:0 18px;font-size:13px;font-weight:500;}}"
        )

    def pop(self, text: str):
        self.setText(text)
        self._fade.stop()
        self._effect.setOpacity(1.0)
        self.restyle()
        self.setFixedWidth(max(180, self.fontMetrics().horizontalAdvance(text) + 52))
        self.reposition()
        self.show()
        self.raise_()
        self._timer.start(2400)

    def reposition(self):
        parent = self.parentWidget()
        if parent is None:
            return
        x = (parent.width() - self.width()) // 2
        y = parent.height() - self.height() - 20
        self.move(max(12, x), max(12, y))

    def _fade_out(self):
        self._fade.setStartValue(self._effect.opacity())
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _on_fade_done(self):
        if self._effect.opacity() <= 0.01:
            self.hide()


class EditableTaskText(QLineEdit):
    """默认只读、外观像普通文字；双击进入编辑，回车/失焦保存。"""

    def __init__(self, task: "Task", page: "TaskPage"):
        super().__init__(task.text)
        self.task = task
        self.page = page
        self.setReadOnly(True)
        from .styles import _TEXT_PRIMARY, _TEXT_TERTIARY
        base_style = "border:none;background:transparent;padding:0;margin:0;"
        if task.is_completed:
            self.setStyleSheet(base_style + f"color:{_TEXT_TERTIARY};")
            font = self.font()
            font.setStrikeOut(True)
            self.setFont(font)
        else:
            self.setStyleSheet(base_style + f"font-weight:500;color:{_TEXT_PRIMARY};")
        self.setToolTip("双击修改任务内容")
        self.editingFinished.connect(self._finish)

    def mouseDoubleClickEvent(self, event):
        self.setReadOnly(False)
        self.setFocus()
        self.selectAll()
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        if not self.isReadOnly() and event.key() == Qt.Key.Key_Escape:
            self.setReadOnly(True)
            self.setText(self.task.text)
            self.clearFocus()
            event.accept()
            return
        super().keyPressEvent(event)

    def _finish(self):
        if self.isReadOnly():
            return
        self.setReadOnly(True)
        new_text = self.text().strip()
        if new_text and new_text != self.task.text and self.task.id is not None:
            self.page.edit_text(self.task.id, new_text)
        else:
            self.setText(self.task.text)


class StatCard(QFrame):
    """统计卡：小标签在上（前面缀一个语义色点），数字在下。"""

    def __init__(self, title: str, color: str = "#2B5CE6", parent=None):
        super().__init__(parent)
        self.setObjectName("Surface")
        self._value = 0
        self._number_style = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 16)
        layout.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(6)
        dot = QLabel()
        dot.setFixedSize(6, 6)
        dot.setStyleSheet(f"background:{color};border-radius:3px;border:0;")
        head.addWidget(dot)
        self.caption = QLabel(title)
        self.caption.setObjectName("Caption")
        head.addWidget(self.caption)
        head.addStretch()

        self.number = QLabel("0")
        self.number.setObjectName("StatValue")
        layout.addLayout(head)
        layout.addWidget(self.number)
        self.refresh_theme()

        self._animation = QVariantAnimation(self)
        self._animation.setDuration(380)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(lambda value: self.number.setText(str(int(value))))

    def refresh_theme(self):
        from .styles import _TEXT_PRIMARY
        self._number_style = (
            f"font-size:26px;font-weight:600;color:{_TEXT_PRIMARY};"
            "background:transparent;border:0;letter-spacing:-0.02em;"
        )
        self.number.setStyleSheet(self._number_style)

    def set_value(self, value: int):
        self._animation.stop()
        self._animation.setStartValue(self._value)
        self._animation.setEndValue(value)
        self._value = value
        self._animation.start()


class TaskRow(QFrame):
    def __init__(self, task: Task, page: "TaskPage"):
        super().__init__()
        self.task = task
        self.setObjectName("Card")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(10)

        checkbox = QCheckBox()
        checkbox.setChecked(task.is_completed)
        checkbox.stateChanged.connect(lambda state: page.toggle(task.id, state))
        layout.addWidget(checkbox)

        color = priority_badge_colors(task.priority)
        badge = QLabel(PRIORITY_LABELS.get(task.priority, "普通"))
        badge.setStyleSheet(_badge_style(color))
        badge.setToolTip("点击切换优先级：普通 → 高 → 低")
        badge.setCursor(Qt.CursorShape.PointingHandCursor)
        badge.mousePressEvent = lambda _event, t=task: page.cycle_priority(t.id)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(badge)

        text = EditableTaskText(task, page)
        layout.addWidget(text, 1)

        date_text = (
            f"完成于 {task.completed_at.isoformat()}"
            if task.completed_at
            else f"记录于 {task.created_at.isoformat()}"
        )
        date_label = QLabel(date_text)
        date_label.setObjectName("Muted")
        layout.addWidget(date_label)

        delete_button = QPushButton("×")
        delete_button.setObjectName("DangerIconButton")
        delete_button.setFixedSize(26, 26)
        delete_button.setCursor(Qt.CursorShape.PointingHandCursor)
        delete_button.setToolTip("删除任务（Ctrl+Z 可撤回）")
        delete_button.clicked.connect(lambda: page.delete(task.id))
        layout.addWidget(delete_button)


class TaskPage(QWidget):
    def __init__(self, repository: Repository, parent=None):
        super().__init__(parent)
        self.repository = repository
        self._undo_stack: list[_HistoryEntry] = []
        self._redo_stack: list[_HistoryEntry] = []
        self._restoring = False
        layout = page_body(self, spacing=16, margins=(32, 28, 32, 24))

        header = QHBoxLayout()
        header.setSpacing(12)
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title = QLabel("任务")
        title.setObjectName("PageTitle")
        title_col.addWidget(title)
        subtitle = QLabel("记录本周设计工单 · 勾选即完成 · 点徽章切换优先级 · 双击修改内容 · Ctrl+Z 撤回")
        subtitle.setObjectName("PageSubtitle")
        title_col.addWidget(subtitle)
        header.addLayout(title_col)
        header.addStretch()

        self.undo_button = QPushButton("撤回")
        self.undo_button.clicked.connect(self.undo)
        self.redo_button = QPushButton("重做")
        self.redo_button.clicked.connect(self.redo)
        for button in (self.undo_button, self.redo_button):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setMinimumWidth(96)
        header.addWidget(self.undo_button)
        header.addWidget(self.redo_button)
        layout.addLayout(header)

        stats = QHBoxLayout()
        stats.setSpacing(16)
        self.open_card = StatCard("进行中", "#C0392B")
        self.today_card = StatCard("今日完成", "#1D7A4C")
        self.week_card = StatCard("本周完成", "#2B5CE6")
        for card in (self.open_card, self.today_card, self.week_card):
            card.setMinimumWidth(150)
            stats.addWidget(card, 1)
        layout.addLayout(stats)

        add_row = QHBoxLayout()
        add_row.setSpacing(12)
        self.input = QLineEdit()
        self.input.setPlaceholderText("输入任务内容，回车快速新增，例如：劲酒中秋海报主视觉")
        self.input.returnPressed.connect(self.add_task)
        add_row.addWidget(self.input, 1)
        self.priority = QComboBox()
        for key, label in PRIORITY_LABELS.items():
            self.priority.addItem(label, key)
        self.priority.setCurrentIndex(1)
        add_row.addWidget(self.priority)
        add_button = QPushButton("添加")
        add_button.setObjectName("PrimaryButton")
        add_button.setCursor(Qt.CursorShape.PointingHandCursor)
        add_button.clicked.connect(self.add_task)
        add_row.addWidget(add_button)
        layout.addLayout(add_row)

        self.list = QListWidget()
        self.list.setSpacing(6)
        layout.addWidget(self.list, 1)

        self.toast = _Toast(self)
        self._install_shortcuts()
        self.refresh()

    def _install_shortcuts(self):
        self._shortcuts: list[QShortcut] = []
        for sequence, handler in (
            ("Ctrl+Z", self._shortcut_undo),
            ("Ctrl+Y", self._shortcut_redo),
            ("Ctrl+Shift+Z", self._shortcut_redo),
        ):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(handler)
            self._shortcuts.append(shortcut)

    def _shortcut_undo(self):
        if not self.isVisible():
            return
        focused = QApplication.focusWidget()
        if (
            isinstance(focused, QLineEdit)
            and not focused.isReadOnly()
            and focused.text()
            and focused.isUndoAvailable()
        ):
            focused.undo()
            return
        self.undo()

    def _shortcut_redo(self):
        if not self.isVisible():
            return
        self.redo()

    def _snapshot(self) -> list[Task]:
        return self.repository.list_tasks()

    def _record(self, label: str, before: list[Task]) -> None:
        if self._restoring:
            return
        self._undo_stack.append(_HistoryEntry(label, before))
        if len(self._undo_stack) > MAX_HISTORY:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def undo(self):
        if not self._undo_stack:
            self._toast("没有可撤回的操作了")
            return
        entry = self._undo_stack.pop()
        after = self._snapshot()
        self._restoring = True
        try:
            self.repository.restore_task_snapshot(entry.snapshot)
        finally:
            self._restoring = False
        self._redo_stack.append(_HistoryEntry(entry.label, after))
        self.refresh()
        self._toast(f"已撤回：{entry.label}")

    def redo(self):
        if not self._redo_stack:
            self._toast("没有可重做的操作了")
            return
        entry = self._redo_stack.pop()
        before = self._snapshot()
        self._restoring = True
        try:
            self.repository.restore_task_snapshot(entry.snapshot)
        finally:
            self._restoring = False
        self._undo_stack.append(_HistoryEntry(entry.label, before))
        self.refresh()
        self._toast(f"已重做：{entry.label}")

    def _sync_history_buttons(self):
        self.undo_button.setEnabled(bool(self._undo_stack))
        self.redo_button.setEnabled(bool(self._redo_stack))
        self.undo_button.setToolTip(
            f"撤回：{self._undo_stack[-1].label}（Ctrl+Z）" if self._undo_stack
            else "暂无可撤回的操作"
        )
        self.redo_button.setToolTip(
            f"重做：{self._redo_stack[-1].label}（Ctrl+Y）" if self._redo_stack
            else "暂无可重做的操作"
        )

    def _toast(self, text: str):
        self.toast.pop(text)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.toast.reposition()

    def refresh_theme(self):
        for card in (self.open_card, self.today_card, self.week_card):
            card.refresh_theme()
        self.refresh()

    def add_task(self):
        text = self.input.text().strip()
        if not text:
            return
        before = self._snapshot()
        self.repository.create_task(text, priority=self.priority.currentData() or "medium")
        self._record(f"新增「{_short(text)}」", before)
        self.input.clear()
        self.refresh()

    def toggle(self, task_id: int | None, state: int):
        if task_id is None:
            return
        before = self._snapshot()
        task = next((t for t in before if t.id == task_id), None)
        if state == Qt.CheckState.Checked.value:
            self.repository.complete_task(task_id)
            label = f"完成「{_short(task.text)}」" if task else "勾选任务"
        else:
            self.repository.reopen_task(task_id)
            label = f"取消完成「{_short(task.text)}」" if task else "取消勾选任务"
        self._record(label, before)
        self.refresh()

    def delete(self, task_id: int | None):
        if task_id is None:
            return
        before = self._snapshot()
        task = next((t for t in before if t.id == task_id), None)
        self.repository.delete_task(task_id)
        self._record(f"删除「{_short(task.text)}」" if task else "删除任务", before)
        self.refresh()

    def edit_text(self, task_id: int, text: str):
        before = self._snapshot()
        task = next((t for t in before if t.id == task_id), None)
        old_text = task.text if task else text
        self.repository.update_task_text(task_id, text)
        if text.strip() != old_text:
            self._record(f"修改为「{_short(text)}」", before)
        self.refresh()

    def cycle_priority(self, task_id: int | None):
        if task_id is None:
            return
        before = self._snapshot()
        task = next((t for t in before if t.id == task_id), None)
        if task is None:
            return
        next_priority = PRIORITY_CYCLE[(PRIORITY_CYCLE.index(task.priority) + 1) % len(PRIORITY_CYCLE)]
        self.repository.update_task_priority(task_id, next_priority)
        self._record(f"「{_short(task.text)}」改为{PRIORITY_LABELS[next_priority]}", before)
        self.refresh()

    def _add_header(self, text: str):
        from .styles import _TEXT_TERTIARY
        item = QListWidgetItem()
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        label = QLabel(text)
        label.setStyleSheet(f"color:{_TEXT_TERTIARY};font-weight:700;padding:10px 4px 4px 4px;font-size:12px;")
        item.setSizeHint(label.sizeHint())
        self.list.addItem(item)
        self.list.setItemWidget(item, label)

    def _add_hint(self, text: str):
        item = QListWidgetItem()
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        label = QLabel(text)
        label.setObjectName("Muted")
        item.setSizeHint(label.sizeHint())
        self.list.addItem(item)
        self.list.setItemWidget(item, label)

    def refresh(self):
        tasks = self.repository.list_tasks()
        open_tasks = [t for t in tasks if not t.is_completed]
        done_tasks = [t for t in tasks if t.is_completed]

        today = date.today()
        week_start, week_end = week_bounds(today)
        self.open_card.set_value(len(open_tasks))
        self.today_card.set_value(sum(1 for t in done_tasks if t.completed_at == today))
        self.week_card.set_value(
            sum(1 for t in done_tasks if t.completed_at and week_start <= t.completed_at <= week_end)
        )

        self.list.clear()
        if not tasks:
            self._add_hint("暂无任务。在上方输入任务内容，回车添加第一条；勾选即完成，点 × 删除，Ctrl+Z 撤回。")
        if open_tasks:
            self._add_header(f"进行中（{len(open_tasks)}）")
            for task in open_tasks:
                self._add_task_row(task)
        if done_tasks:
            self._add_header(f"已完成（{len(done_tasks)}）")
            for task in done_tasks:
                self._add_task_row(task)

        self._sync_history_buttons()
        self.toast.restyle()

    def _add_task_row(self, task: Task):
        item = QListWidgetItem()
        row = TaskRow(task, self)
        item.setSizeHint(row.sizeHint())
        self.list.addItem(item)
        self.list.setItemWidget(item, row)
