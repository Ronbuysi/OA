from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QTextCursor,
)
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QSpinBox, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from ..models import MonthlyEvaluation
from ..reports import (
    generate_monthly_from_weeks,
    generate_weekly_text,
    week_bounds,
    weeks_in_month,
)
from ..repository import Repository
from .layout import page_body
from . import styles as S
from .styles import (
    _ACCENT, _TEXT_TERTIARY,
    to_qcolor as _to_color,
)
from .task_view import StatCard

TREND_WEEKS = 12


class _TrendBarChart(QWidget):
    """零卡顿自绘圆角渐变柱状图（取代笨重的 QtCharts，支持悬停高亮与数值气泡）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data: list[tuple[date, int]] = []
        self._hover_idx: int = -1
        self.setMouseTracking(True)
        self.setMinimumHeight(260)

    def set_data(self, data: list[tuple[date, int]]) -> None:
        self._data = list(data)
        self._hover_idx = -1
        self.update()

    def leaveEvent(self, event):
        if self._hover_idx != -1:
            self._hover_idx = -1
            self.update()
        super().leaveEvent(event)

    def mouseMoveEvent(self, event):
        idx = self._bar_index_at(event.position())
        if idx != self._hover_idx:
            self._hover_idx = idx
            self.update()
        super().mouseMoveEvent(event)

    def _plot_rect(self) -> QRectF:
        return QRectF(44, 24, max(100, self.width() - 64), max(80, self.height() - 62))

    def _bar_index_at(self, pos: QPointF) -> int:
        if not self._data:
            return -1
        plot = self._plot_rect()
        if not plot.contains(pos):
            return -1
        slot_w = plot.width() / len(self._data)
        idx = int((pos.x() - plot.left()) / slot_w)
        return idx if 0 <= idx < len(self._data) else -1

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self._data:
            p.end()
            return

        plot = self._plot_rect()
        counts = [c for _, c in self._data]
        max_val = max(4, max(counts) + 1)
        dark = S.get_theme() == "dark"

        muted = _to_color(S._TEXT_TERTIARY)
        grid_color = QColor(255, 255, 255, 18) if dark else QColor(15, 23, 42, 18)
        f_axis = QFont()
        f_axis.setPixelSize(11)
        p.setFont(f_axis)

        # 1. 水平参考虚线与 Y 轴刻度
        steps = 4
        for step in range(steps + 1):
            ratio = step / steps
            y = plot.bottom() - ratio * plot.height()
            val = int(round(ratio * max_val))
            pen = QPen(grid_color, 1.0, Qt.PenStyle.DashLine if step > 0 else Qt.PenStyle.SolidLine)
            p.setPen(pen)
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.setPen(muted)
            p.drawText(
                QRectF(4, y - 9, plot.left() - 10, 18),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                str(val),
            )

        # 2. 圆角渐变柱与 X 轴日期标签
        n = len(self._data)
        slot_w = plot.width() / n
        bar_w = min(34.0, max(12.0, slot_w * 0.52))
        accent = _to_color(_ACCENT, "#2563EB")

        for i, (start_day, count) in enumerate(self._data):
            cx = plot.left() + (i + 0.5) * slot_w
            hovered = i == self._hover_idx

            # 悬停列背景光带
            if hovered:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(accent.red(), accent.green(), accent.blue(), 22 if dark else 14))
                p.drawRoundedRect(
                    QRectF(plot.left() + i * slot_w + 2, plot.top(), slot_w - 4, plot.height()),
                    8, 8,
                )

            # 柱体
            if count > 0:
                bh = max(6.0, (count / max_val) * plot.height())
                bar_rect = QRectF(cx - bar_w / 2, plot.bottom() - bh, bar_w, bh)
                grad = QLinearGradient(bar_rect.topLeft(), bar_rect.bottomLeft())
                if hovered:
                    grad.setColorAt(0.0, QColor("#60A5FA"))
                    grad.setColorAt(1.0, accent)
                else:
                    grad.setColorAt(0.0, accent)
                    grad.setColorAt(1.0, QColor(accent.red(), accent.green(), accent.blue(), 175))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(grad)
                p.drawRoundedRect(bar_rect, 6, 6)

                # 柱顶数字
                f_val = QFont()
                f_val.setPixelSize(11)
                f_val.setBold(True)
                p.setFont(f_val)
                p.setPen(_to_color(S._TEXT_PRIMARY) if hovered else muted)
                p.drawText(
                    QRectF(cx - slot_w / 2, bar_rect.top() - 18, slot_w, 16),
                    Qt.AlignmentFlag.AlignCenter,
                    str(count),
                )
            else:
                # 0 值微点占位
                dot_rect = QRectF(cx - bar_w * 0.35, plot.bottom() - 3, bar_w * 0.7, 3)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(grid_color)
                p.drawRoundedRect(dot_rect, 1.5, 1.5)

            # X 轴日期
            p.setFont(f_axis)
            p.setPen(_to_color(S._ACCENT) if hovered else muted)
            p.drawText(
                QRectF(cx - slot_w / 2, plot.bottom() + 8, slot_w, 20),
                Qt.AlignmentFlag.AlignCenter,
                f"{start_day.month}/{start_day.day}",
            )

        p.end()


class ReportPage(QWidget):
    def __init__(self, repository: Repository, parent=None):
        super().__init__(parent)
        self.repository = repository
        self._week_offset = 0
        self._loaded_week_start: date | None = None
        self._clean_week_text: str = ""
        self._loaded_month_key: str | None = None
        self._clean_month_text: str = ""

        outer = page_body(self, spacing=16, margins=(32, 28, 32, 24))
        header = QVBoxLayout()
        header.setSpacing(2)
        title = QLabel("数据")
        title.setObjectName("PageTitle")
        header.addWidget(title)
        subtitle = QLabel("产出趋势一眼看 · 每周点「保存周清」存档，月底一键拼成月度总结")
        subtitle.setObjectName("PageSubtitle")
        header.addWidget(subtitle)
        outer.addLayout(header)
        tabs = QTabWidget()
        tabs.addTab(self._build_trend(), "趋势")
        tabs.addTab(self._build_weekly(), "周清")
        tabs.addTab(self._build_monthly(), "月度绩效")
        self.tabs = tabs
        outer.addWidget(tabs, 1)

    def refresh_theme(self):
        """主题切换后重画图表、刷新统计卡。"""
        for card in (
            self.trend_total_card, self.trend_avg_card, self.trend_best_card,
            self.week_done_card, self.week_open_card, self.week_created_card,
            self.month_done_card, self.month_weeks_card,
        ):
            card.refresh_theme()
        self._refresh_trend()

    def refresh_on_show(self):
        """切到报表页时刷新趋势与统计；若当周/当月尚未手动编辑，同步刷新自动生成的正文。"""
        self._refresh_trend()
        self._refresh_week_stats()
        if not self._is_week_dirty():
            self.load_week()
        if not self._is_month_dirty():
            self.load_month()

    # ---------- 趋势 ----------
    def _build_trend(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(16)

        stats = QHBoxLayout()
        stats.setSpacing(16)
        self.trend_total_card = StatCard(f"近 {TREND_WEEKS} 周完成", "#059669")
        self.trend_avg_card = StatCard("平均每周", "#2563EB")
        self.trend_best_card = StatCard("最好的一周", "#2563EB")
        for card in (self.trend_total_card, self.trend_avg_card, self.trend_best_card):
            card.setMinimumWidth(140)
            stats.addWidget(card, 1)
        layout.addLayout(stats)

        chart_card = QFrame()
        chart_card.setObjectName("Surface")
        chart_lay = QVBoxLayout(chart_card)
        chart_lay.setContentsMargins(16, 16, 16, 12)
        chart_lay.setSpacing(0)
        self.trend_view = _TrendBarChart()
        chart_lay.addWidget(self.trend_view, 1)
        layout.addWidget(chart_card, 1)

        self.trend_note = QLabel("")
        self.trend_note.setObjectName("Muted")
        self.trend_note.setWordWrap(True)
        layout.addWidget(self.trend_note)

        self._refresh_trend()
        return page

    def _refresh_trend(self):
        data = self.repository.completed_counts_by_week(date.today(), TREND_WEEKS)
        counts = [count for _start, count in data]
        total = sum(counts)

        self.trend_total_card.set_value(total)
        self.trend_avg_card.set_value(round(total / len(counts)) if counts else 0)
        self.trend_best_card.set_value(max(counts) if counts else 0)
        self.trend_view.set_data(data)

        active_weeks = sum(1 for count in counts if count)
        best = max(data, key=lambda item: item[1]) if data else None
        note = (
            f"按周统计最近 {TREND_WEEKS} 周（每周一起算）的任务完成数 · "
            f"有产出的周 {active_weeks}/{TREND_WEEKS} · 共 {total} 个任务"
        )
        if best and best[1]:
            note += f" · 最好的一周是 {best[0].month}月{best[0].day}日那周（{best[1]} 个）"
        self.trend_note.setText(note)

    # ---------- 周清 ----------
    def _build_weekly(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        stats = QHBoxLayout()
        stats.setSpacing(16)
        self.week_done_card = StatCard("本周完成", "#059669")
        self.week_open_card = StatCard("进行中", "#DC2626")
        self.week_created_card = StatCard("本周新增", "#2563EB")
        for card in (self.week_done_card, self.week_open_card, self.week_created_card):
            card.setMinimumWidth(140)
            stats.addWidget(card, 1)
        layout.addLayout(stats)

        row = QHBoxLayout()
        row.addWidget(QLabel("周清范围"))
        prev_week = QPushButton("‹ 上一周")
        prev_week.setToolTip("切换到上一周")
        prev_week.clicked.connect(lambda: self._shift_week(-1))
        row.addWidget(prev_week)
        self.week_label = QLabel("")
        row.addWidget(self.week_label)
        next_week = QPushButton("下一周 ›")
        next_week.setToolTip("切换到下一周")
        next_week.clicked.connect(lambda: self._shift_week(1))
        row.addWidget(next_week)
        back_today = QPushButton("回到本周")
        back_today.setToolTip("回到当前这一周")
        back_today.clicked.connect(self._back_to_today)
        row.addWidget(back_today)
        self.week_status = QLabel("")
        self.week_status.setObjectName("Muted")
        row.addWidget(self.week_status)
        row.addStretch()
        layout.addLayout(row)

        actions = QHBoxLayout()
        actions.addStretch()
        regenerate = QPushButton("重新生成")
        regenerate.setObjectName("PrimaryButton")
        regenerate.setToolTip("从任务表重新生成本周周清，会覆盖当前编辑内容（未点「保存周清」不会写入存档）")
        regenerate.clicked.connect(lambda: self.regenerate_week(confirm=True))
        actions.addWidget(regenerate)
        self.save_week_button = QPushButton("保存周清")
        self.save_week_button.setObjectName("PrimaryButton")
        self.save_week_button.clicked.connect(self.save_week)
        actions.addWidget(self.save_week_button)
        copy_button = QPushButton("复制")
        copy_button.clicked.connect(self.copy_week)
        actions.addWidget(copy_button)
        export_button = QPushButton("导出 txt")
        export_button.clicked.connect(self.export_week)
        actions.addWidget(export_button)
        layout.addLayout(actions)

        self.week_text = QTextEdit()
        self.week_text.setPlaceholderText("本周周报会生成在这里，可直接修改后点「保存周清」，月底月度页自动汇总")
        layout.addWidget(self.week_text, 1)
        self.load_week()
        return page

    def copy_week(self):
        self.week_text.selectAll()
        self.week_text.copy()
        self.week_text.moveCursor(QTextCursor.MoveOperation.Start)

    def export_week(self):
        start, end = self._week_bounds()
        path, _ = QFileDialog.getSaveFileName(
            self, "导出周报", f"周报_{start.isoformat()}_{end.isoformat()}.txt", "文本文件 (*.txt)"
        )
        if path:
            with open(path, "w", encoding="utf-8-sig") as handle:
                handle.write(self.week_text.toPlainText())

    def _week_bounds(self) -> tuple[date, date]:
        anchor = date.today() + timedelta(days=self._week_offset * 7)
        return week_bounds(anchor)

    def _is_week_dirty(self) -> bool:
        if self._loaded_week_start is None:
            return False
        return self.week_text.toPlainText().strip() != self._clean_week_text.strip()

    def _check_unsaved_week_before_switch(self) -> bool:
        if not self._is_week_dirty() or self._loaded_week_start is None:
            return True
        reply = QMessageBox.question(
            self, "未保存的修改",
            f"当前周清（{self._loaded_week_start.month}月{self._loaded_week_start.day}日起）"
            "有未保存的编辑修改，是否先保存？",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if reply == QMessageBox.StandardButton.Cancel:
            return False
        if reply == QMessageBox.StandardButton.Save:
            self.repository.save_weekly_report(
                self._loaded_week_start, self.week_text.toPlainText()
            )
            self._clean_week_text = self.week_text.toPlainText()
        return True

    def _shift_week(self, delta: int):
        if not self._check_unsaved_week_before_switch():
            return
        self._week_offset += delta
        self.load_week()

    def _back_to_today(self):
        if self._week_offset == 0:
            return
        if not self._check_unsaved_week_before_switch():
            return
        self._week_offset = 0
        self.load_week()

    def _refresh_week_stats(self):
        start, end = self._week_bounds()
        completed = self.repository.get_completed_tasks(start, end)
        open_tasks = self.repository.list_tasks(include_completed=False)
        created_count = self.repository.count_tasks_created_between(start, end)
        self.week_done_card.set_value(len(completed))
        self.week_open_card.set_value(len(open_tasks))
        self.week_created_card.set_value(created_count)

    def load_week(self):
        start, end = self._week_bounds()
        self.week_label.setText(f"{start.year}年{start.month}月{start.day}日 - {end.month}月{end.day}日")
        self._refresh_week_stats()

        saved = self.repository.get_weekly_report(start)
        self._loaded_week_start = start
        if saved is not None:
            self.week_text.setPlainText(saved)
            self._clean_week_text = saved
            self.week_status.setText(f"已保存 {start.month}月{start.day}日 起的周清 · 修改后记得重新保存")
        else:
            self.regenerate_week(confirm=False)
            self._clean_week_text = self.week_text.toPlainText()
            self.week_status.setText("本周周清尚未保存")

    def regenerate_week(self, confirm: bool = False):
        if confirm and self._is_week_dirty():
            reply = QMessageBox.question(
                self, "重新生成周清",
                "重新生成会覆盖当前手动编辑的内容，是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.No:
                return

        start, end = self._week_bounds()
        completed = self.repository.get_completed_tasks(start, end)
        open_tasks = self.repository.list_tasks(include_completed=False)
        created_count = self.repository.count_tasks_created_between(start, end)
        file_stats = self.repository.count_files_by_category()
        generated = generate_weekly_text(
            completed, open_tasks, start, end, created_count, file_stats,
        )
        self.week_text.setPlainText(generated)
        if confirm:
            self.week_status.setText("已重新生成 · 未保存，点「保存周清」写入存档")

    def save_week(self):
        start, _end = self._week_bounds()
        text = self.week_text.toPlainText()
        self.repository.save_weekly_report(start, text)
        self._loaded_week_start = start
        self._clean_week_text = text
        self.save_week_button.setText("已保存 ✓")
        self.week_status.setText(f"已保存 {start.month}月{start.day}日 起的周清")
        QTimer.singleShot(2000, lambda: self.save_week_button.setText("保存周清"))

    def _is_week_saved(self, week_start: date) -> bool:
        return self.repository.get_weekly_report(week_start) is not None

    # ---------- 月度绩效 ----------
    def _build_monthly(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        stats = QHBoxLayout()
        stats.setSpacing(16)
        self.month_done_card = StatCard("本月完成", "#059669")
        self.month_weeks_card = StatCard("本月周数", "#2563EB")
        for card in (self.month_done_card, self.month_weeks_card):
            card.setMinimumWidth(140)
            stats.addWidget(card, 1)
        layout.addLayout(stats)

        row = QHBoxLayout()
        row.addWidget(QLabel("年份"))
        self.year = QSpinBox()
        self.year.setRange(2020, 2035)
        self.year.setValue(date.today().year)
        self.year.valueChanged.connect(self.load_month)
        row.addWidget(self.year)
        row.addWidget(QLabel("月份"))
        self.month = QComboBox()
        for m in range(1, 13):
            self.month.addItem(f"{m:02d}月", m)
        self.month.setCurrentIndex(date.today().month - 1)
        self.month.currentIndexChanged.connect(self.load_month)
        row.addWidget(self.month)
        row.addStretch()
        layout.addLayout(row)

        actions = QHBoxLayout()
        actions.addStretch()
        combine = QPushButton("从周清生成")
        combine.setObjectName("PrimaryButton")
        combine.setToolTip("把当月每周已保存的周清按周拼成月度总结（没保存过的周自动从任务生成）")
        combine.clicked.connect(lambda: self.build_month_from_weeks(confirm=True))
        actions.addWidget(combine)
        self.save_month_button = QPushButton("保存")
        self.save_month_button.setObjectName("PrimaryButton")
        self.save_month_button.clicked.connect(self.save_month)
        actions.addWidget(self.save_month_button)
        copy_button = QPushButton("复制")
        copy_button.clicked.connect(self.copy_month)
        actions.addWidget(copy_button)
        export_button = QPushButton("导出 txt")
        export_button.clicked.connect(self.export_month)
        actions.addWidget(export_button)
        layout.addLayout(actions)

        self.month_text = QTextEdit()
        self.month_text.setPlaceholderText("月度总结 = 当月每周已保存的周清自动拼合，可直接修改后保存")
        layout.addWidget(self.month_text, 1)
        self.load_month()
        return page

    def _month_bounds(self) -> tuple[date, date]:
        year = self.year.value()
        month = self.month.currentData() or date.today().month
        start = date(year, month, 1)
        end = date(year + (month == 12), 1 if month == 12 else month + 1, 1) - timedelta(days=1)
        return start, end

    def _is_month_dirty(self) -> bool:
        if self._loaded_month_key is None:
            return False
        return self.month_text.toPlainText().strip() != self._clean_month_text.strip()

    def build_month_from_weeks(self, confirm: bool = False):
        if confirm and self._is_month_dirty():
            reply = QMessageBox.question(
                self, "从周清生成月度绩效",
                "从周清生成会覆盖当前手动编辑的内容，是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.No:
                return

        year = self.year.value()
        month = self.month.currentData() or date.today().month
        items: list[tuple[date, date, str]] = []
        for start, end in weeks_in_month(year, month):
            content = self.repository.get_weekly_report(start)
            if content is None:
                completed = self.repository.get_completed_tasks(start, end)
                generated = generate_weekly_text(completed, start=start, end=end)
                content = "" if "暂无完成任务" in generated else generated
            items.append((start, end, content))
        self.month_text.setPlainText(generate_monthly_from_weeks(f"{year}年{month}月", items))

    def load_month(self):
        year = self.year.value()
        month = self.month.currentData() or date.today().month
        new_key = f"{year}-{month:02d}"

        if self._loaded_month_key is not None and self._loaded_month_key != new_key and self._is_month_dirty():
            old_key = self._loaded_month_key
            reply = QMessageBox.question(
                self, "未保存的修改",
                f"当前月度绩效（{old_key}）有未保存的编辑修改，是否先保存？",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )
            if reply == QMessageBox.StandardButton.Cancel:
                old_y, old_m = (int(x) for x in old_key.split("-"))
                self.year.blockSignals(True)
                self.month.blockSignals(True)
                self.year.setValue(old_y)
                self.month.setCurrentIndex(old_m - 1)
                self.year.blockSignals(False)
                self.month.blockSignals(False)
                return
            if reply == QMessageBox.StandardButton.Save:
                self.repository.save_monthly_evaluation(MonthlyEvaluation(
                    month=old_key,
                    summary_text=self.month_text.toPlainText(),
                ))

        self._loaded_month_key = new_key
        item = self.repository.get_monthly_evaluation(new_key)
        if item.summary_text:
            self.month_text.setPlainText(item.summary_text)
        else:
            self.build_month_from_weeks(confirm=False)
        self._clean_month_text = self.month_text.toPlainText()

        start, end = self._month_bounds()
        self.month_done_card.set_value(len(self.repository.get_completed_tasks(start, end)))
        self.month_weeks_card.set_value(len(weeks_in_month(year, month)))

    def save_month(self):
        year = self.year.value()
        month = self.month.currentData() or date.today().month
        key = f"{year}-{month:02d}"
        text = self.month_text.toPlainText()
        self.repository.save_monthly_evaluation(MonthlyEvaluation(
            month=key,
            summary_text=text,
        ))
        self._loaded_month_key = key
        self._clean_month_text = text
        self.save_month_button.setText("已保存 ✓")
        QTimer.singleShot(2000, lambda: self.save_month_button.setText("保存"))

    def copy_month(self):
        self.month_text.selectAll()
        self.month_text.copy()
        self.month_text.moveCursor(QTextCursor.MoveOperation.Start)

    def export_month(self):
        year = self.year.value()
        month = self.month.currentData() or date.today().month
        path, _ = QFileDialog.getSaveFileName(
            self, "导出月度总结", f"月度总结_{year}-{month:02d}.txt", "文本文件 (*.txt)"
        )
        if path:
            with open(path, "w", encoding="utf-8-sig") as handle:
                handle.write(self.month_text.toPlainText())
