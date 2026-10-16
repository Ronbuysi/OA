from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate, Qt, QTime
from PySide6.QtGui import QColor, QTextCharFormat
from PySide6.QtWidgets import (
    QCalendarWidget, QCheckBox, QDateEdit, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton, QSpinBox,
    QTimeEdit, QVBoxLayout, QWidget,
)

from ..models import Todo
from ..repository import Repository
from .layout import page_body
from .styles import remind_badge_colors

_TODO_MARK_BG = QColor(37, 99, 235, 36)


class TodoRow(QFrame):
    """单条待办：时间徽章 + 标题 + 完成勾选 + 删除；批量条目右键可删整组。"""

    def __init__(self, todo: Todo, page, parent=None):
        super().__init__(parent)
        self.todo = todo
        self.setObjectName("Card")
        from .styles import _SEGMENTED_BG, _TEXT_PRIMARY, _TEXT_TERTIARY

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)

        bg, fg = remind_badge_colors(bool(todo.remind_time))
        badge = QLabel(todo.remind_time if todo.remind_time else "无提醒")
        badge.setStyleSheet(
            f"color:{fg};background:{bg};border-radius:6px;padding:3px 9px;"
            f"font-size:11px;font-weight:{600 if todo.remind_time else 500};border:0;"
        )
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(badge)

        checkbox = QCheckBox()
        checkbox.setChecked(todo.is_done)
        checkbox.stateChanged.connect(
            lambda state: page.toggle_done(todo.id, state == Qt.CheckState.Checked.value)
        )
        layout.addWidget(checkbox)

        title = QLabel(todo.title)
        title.setWordWrap(True)
        title.setToolTip(todo.title)
        if todo.is_done:
            title.setStyleSheet(f"color:{_TEXT_TERTIARY};font-size:14px;background:transparent;border:0;")
            font = title.font()
            font.setStrikeOut(True)
            title.setFont(font)
        else:
            title.setStyleSheet(f"color:{_TEXT_PRIMARY};font-weight:500;font-size:14px;background:transparent;border:0;")
        layout.addWidget(title, 1)

        group_size = page.repository.todo_group_size(todo.group_id) if todo.group_id is not None else 0
        if group_size > 1:
            group_badge = QLabel(f"共 {group_size} 天")
            group_badge.setStyleSheet(
                f"color:{_TEXT_TERTIARY};background:{_SEGMENTED_BG};border-radius:8px;"
                "padding:2px 8px;font-size:11px;font-weight:600;border:0;"
            )
            layout.addWidget(group_badge)

        delete_button = QPushButton("×")
        delete_button.setObjectName("DangerIconButton")
        delete_button.setFixedSize(26, 26)
        delete_button.setCursor(Qt.CursorShape.PointingHandCursor)
        delete_button.setToolTip("删除")
        delete_button.clicked.connect(lambda: page.delete_todo(todo))
        layout.addWidget(delete_button)


class TodoPage(QWidget):
    def __init__(self, repository: Repository, parent=None):
        super().__init__(parent)
        self.repository = repository
        self.selected_date = date.today()
        self._build_ui()
        self._style_calendar()
        self._refresh()

    def refresh_theme(self):
        """刷新主题相关的样式。"""
        self._style_calendar()
        self._refresh()

    def _style_calendar(self):
        from .icons import to_qcolor
        from .styles import _DANGER
        fmt = QTextCharFormat()
        fmt.setForeground(to_qcolor(_DANGER))
        for day in (Qt.DayOfWeek.Saturday, Qt.DayOfWeek.Sunday):
            self.calendar.setWeekdayTextFormat(day, fmt)

    def _build_ui(self):
        root = page_body(self, spacing=16, margins=(32, 28, 32, 24))

        header = QVBoxLayout()
        header.setSpacing(2)
        title = QLabel("待办 / 日程")
        title.setObjectName("PageTitle")
        header.addWidget(title)
        subtitle = QLabel("日历看日程 · 可一次创建连续多天待办 · 到点弹系统提醒")
        subtitle.setObjectName("PageSubtitle")
        header.addWidget(subtitle)
        root.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(16)

        # ---------- 左：月历 + 本月统计 ----------
        left = QFrame()
        left.setObjectName("Card")
        left.setMinimumWidth(300)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(14, 14, 14, 14)
        left_layout.setSpacing(10)
        self.calendar = QCalendarWidget()
        self.calendar.setGridVisible(False)
        self.calendar.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        self.calendar.setNavigationBarVisible(True)
        self.calendar.clicked.connect(self._on_date_selected)
        self.calendar.currentPageChanged.connect(lambda _y, _m: self._mark_calendar())
        left_layout.addWidget(self.calendar)
        self.month_label = QLabel("")
        self.month_label.setObjectName("Muted")
        left_layout.addWidget(self.month_label)
        left_layout.addStretch()
        body.addWidget(left, 3)

        # ---------- 右：创建区 + 当天列表 ----------
        right = QVBoxLayout()
        right.setSpacing(14)

        create = QFrame()
        create.setObjectName("Card")
        create_layout = QVBoxLayout(create)
        create_layout.setContentsMargins(16, 14, 16, 14)
        create_layout.setSpacing(10)
        create_title = QLabel("新建待办")
        create_title.setObjectName("CardTitle")
        create_layout.addWidget(create_title)

        row1 = QHBoxLayout()
        row1.setSpacing(10)
        row1.addWidget(QLabel("内容"))
        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("例如：周三交劲酒海报初稿")
        self.title_input.returnPressed.connect(self.create_todos)
        row1.addWidget(self.title_input, 1)
        create_layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(10)
        row2.addWidget(QLabel("开始日期"))
        self.start_date = QDateEdit(QDate.currentDate())
        self.start_date.setCalendarPopup(True)
        row2.addWidget(self.start_date)
        row2.addSpacing(12)
        row2.addWidget(QLabel("持续"))
        self.days = QSpinBox()
        self.days.setRange(1, 90)
        self.days.setValue(1)
        self.days.setSuffix(" 天")
        self.days.setToolTip("一次创建连续多天待办（每天一条）")
        row2.addWidget(self.days)
        row2.addSpacing(12)
        row2.addWidget(QLabel("提醒"))
        self.remind_check = QCheckBox()
        self.remind_check.setToolTip("勾选后到点弹系统提醒")
        row2.addWidget(self.remind_check)
        self.remind_time = QTimeEdit(QTime(9, 0))
        self.remind_time.setDisplayFormat("HH:mm")
        row2.addWidget(self.remind_time)
        row2.addStretch()
        create_layout.addLayout(row2)

        bottom_create = QHBoxLayout()
        hint = QLabel("提示：持续天数 > 1 时按天各创建一条，删除时可选择整组删除。")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        bottom_create.addWidget(hint, 1)
        create_button = QPushButton("创建待办")
        create_button.setObjectName("PrimaryButton")
        create_button.setCursor(Qt.CursorShape.PointingHandCursor)
        create_button.clicked.connect(self.create_todos)
        bottom_create.addWidget(create_button)
        create_layout.addLayout(bottom_create)
        right.addWidget(create)

        # 当天待办列表
        self.day_label = QLabel("")
        self.day_label.setObjectName("CardTitle")
        right.addWidget(self.day_label)
        self.todo_list = QListWidget()
        self.todo_list.setSpacing(6)
        self.todo_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.todo_list.customContextMenuRequested.connect(self._show_context_menu)
        right.addWidget(self.todo_list, 1)
        body.addLayout(right, 4)

        root.addLayout(body, 1)

    def _on_date_selected(self, qdate: QDate):
        self.selected_date = qdate.toPython()
        self._refresh_list()

    def _mark_calendar(self):
        year, month = self.calendar.yearShown(), self.calendar.monthShown()
        marked = self.repository.list_todo_dates_in_month(year, month)
        fmt = QTextCharFormat()
        fmt.setBackground(_TODO_MARK_BG)
        plain = QTextCharFormat()
        for day in range(1, 32):
            qdate = QDate(year, month, day)
            if not qdate.isValid():
                continue
            self.calendar.setDateTextFormat(
                qdate, fmt if qdate.toString("yyyy-MM-dd") in marked else plain
            )

    def _refresh(self):
        self._mark_calendar()
        self._refresh_month_label()
        self._refresh_list()

    def _refresh_month_label(self):
        year, month = self.calendar.yearShown(), self.calendar.monthShown()
        count = self.repository.count_todos_in_month(year, month)
        self.month_label.setText(f"{year}年{month}月共 {count} 条待办 · 蓝色日期表示当天有待办")

    def _refresh_list(self):
        todos = self.repository.list_todos(self.selected_date)
        self.day_label.setText(
            f"{self.selected_date.month}月{self.selected_date.day}日 · 待办 {len(todos)} 条"
        )
        self.todo_list.clear()
        for todo in todos:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, todo.id)
            row = TodoRow(todo, self)
            item.setSizeHint(row.sizeHint())
            self.todo_list.addItem(item)
            self.todo_list.setItemWidget(item, row)
        if not todos:
            empty = QListWidgetItem()
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            label = QLabel("这一天没有待办，在上方创建一条吧")
            label.setObjectName("Muted")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setSizeHint(label.sizeHint())
            self.todo_list.addItem(empty)
            self.todo_list.setItemWidget(empty, label)

    def create_todos(self):
        title = self.title_input.text().strip()
        if not title:
            QMessageBox.information(self, "提示", "先输入待办内容")
            self.title_input.setFocus()
            return
        start = self.start_date.date().toPython()
        days = self.days.value()
        remind_time = self.remind_time.time().toString("HH:mm") if self.remind_check.isChecked() else None
        self.repository.create_todos(title, start, days, remind_time)
        self.title_input.clear()
        if days > 1:
            self.selected_date = start
            self.calendar.setSelectedDate(QDate(start.year, start.month, start.day))
        self._refresh()

    def toggle_done(self, todo_id: int, done: bool):
        self.repository.set_todo_done(todo_id, done)
        self._refresh_list()

    def delete_todo(self, todo: Todo):
        group_size = self.repository.todo_group_size(todo.group_id) if todo.group_id is not None else 0
        if todo.group_id is not None and group_size > 1:
            box = QMessageBox(self)
            box.setWindowTitle("删除待办")
            box.setText(
                f"「{todo.title}」是连续 {group_size} 天创建的整批待办，\n"
                "请选择删除范围："
            )
            btn_group = box.addButton(f"删除整组（共 {group_size} 天）", QMessageBox.ButtonRole.DestructiveRole)
            btn_single = box.addButton("仅删当天这条", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            clicked = box.clickedButton()
            if clicked is btn_group:
                self.repository.delete_todo_group(todo.group_id)
            elif clicked is btn_single:
                self.repository.delete_todo(todo.id)
            else:
                return
        else:
            self.repository.delete_todo(todo.id)
        self._refresh()

    def _show_context_menu(self, position):
        item = self.todo_list.itemAt(position)
        if item is None:
            return
        row = self.todo_list.itemWidget(item)
        if not isinstance(row, TodoRow):
            return
        todo = row.todo
        menu = QMenu(self)
        menu.addAction("删除这条（仅当天）", lambda: self._delete_single(todo))
        if todo.group_id is not None and self.repository.todo_group_size(todo.group_id) > 1:
            menu.addAction("删除整组（所有天）", lambda: self._delete_group(todo))
        menu.exec(self.todo_list.mapToGlobal(position))

    def _delete_single(self, todo: Todo):
        self.repository.delete_todo(todo.id)
        self._refresh()

    def _delete_group(self, todo: Todo):
        self.repository.delete_todo_group(todo.group_id)
        self._refresh()
