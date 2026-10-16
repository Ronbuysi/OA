# -*- coding: utf-8 -*-
"""一键归档页面：扫描 -> 预览计划 -> 执行整理 / 撤销。

优化亮点：
- 补齐 `refresh_theme()`，确保顶部 4 张统计卡的数字与路径文本在深/浅色模式切换时实时同步；
- 接入语义化 QSS 类名，消除深色模式下的暗字残留。
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QProgressBar,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import organizer
from ..repository import Repository
from .layout import page_body


class _ScanSignals(QObject):
    progress = Signal(int, int)
    done = Signal(object)  # organizer.ScanResult
    failed = Signal(str)


class _ScanRunnable(QRunnable):
    def __init__(self, signals: _ScanSignals):
        super().__init__()
        self.signals = signals

    def run(self):
        try:
            result = organizer.scan(on_progress=lambda i, t: self.signals.progress.emit(i, t))
            self.signals.done.emit(result)
        except Exception as exc:
            try:
                self.signals.failed.emit(str(exc))
            except RuntimeError:
                pass


class _ApplySignals(QObject):
    progress = Signal(int, int)
    done = Signal(object)  # organizer.ApplyReport
    failed = Signal(str)


class _ApplyRunnable(QRunnable):
    def __init__(self, signals: _ApplySignals):
        super().__init__()
        self.signals = signals

    def run(self):
        try:
            report = organizer.apply_moves(
                on_progress=lambda i, t: self.signals.progress.emit(i, t)
            )
            cat_count, summary_count = 0, 0
            if not report.failed:
                try:
                    cat_count, summary_count = organizer.post_process_month_folders()
                except Exception:
                    pass
            self.signals.done.emit((report, cat_count, summary_count))
        except Exception as exc:
            try:
                self.signals.failed.emit(str(exc))
            except RuntimeError:
                pass


def _stat_card(title: str, value: str, accent: bool = False, compact: bool = False) -> QFrame:
    card = QFrame()
    card.setObjectName("Surface")
    lay = QVBoxLayout(card)
    lay.setContentsMargins(18, 14, 18, 16)
    lay.setSpacing(6)
    label = QLabel(title)
    label.setObjectName("Caption")
    text = QLabel(value)
    lay.addWidget(label)
    lay.addWidget(text)
    card._value_label = text
    card._accent = accent
    card._compact = compact
    _refresh_card_style(card)
    return card


def _refresh_card_style(card: QFrame) -> None:
    from .styles import _ACCENT, _TEXT_PRIMARY, _TEXT_SECONDARY
    if getattr(card, "_compact", False):
        card._value_label.setStyleSheet(
            f"font-size:12px;font-weight:500;color:{_TEXT_SECONDARY};background:transparent;border:0;"
        )
    else:
        color = _ACCENT if getattr(card, "_accent", False) else _TEXT_PRIMARY
        card._value_label.setStyleSheet(
            f"font-size:20px;font-weight:600;color:{color};"
            "background:transparent;border:0;letter-spacing:-0.01em;"
        )


class OrganizerPage(QWidget):
    """一键归档：把散落的设计文件按 G 盘年份文件夹规则归位。"""

    def __init__(self, repository: Repository, thread_pool: QThreadPool, parent=None):
        super().__init__(parent)
        self.repository = repository
        self.thread_pool = thread_pool
        self.plan: organizer.ScanResult | None = None
        self._build_ui()
        cached = organizer.load_plan()
        if cached and cached.records:
            self._show_plan(cached)

    def refresh_theme(self):
        """刷新统计卡文字颜色以跟随新主题。"""
        for card in (self.card_total, self.card_source, self.card_size, self.card_dest):
            _refresh_card_style(card)

    def _build_ui(self):
        root = page_body(self, spacing=16, margins=(32, 28, 32, 24))

        header = QVBoxLayout()
        header.setSpacing(4)
        title_row = QHBoxLayout()
        title_row.setSpacing(12)
        title = QLabel("AI 智能归档")
        title.setObjectName("PageTitle")
        title_tag = QLabel("SMART ARCHIVE")
        title_tag.setObjectName("TitleTag")
        title_row.addWidget(title)
        title_row.addWidget(title_tag)
        title_row.addStretch()
        header.addLayout(title_row)
        subtitle = QLabel(
            "把桌面 / 下载 / 文档 / 图片里散落的设计文件，按你的 G 盘规则归位："
            "年月文件夹 → 分类子文件夹（不改原文件名）"
        )
        subtitle.setObjectName("PageSubtitle")
        header.addWidget(subtitle)
        root.addLayout(header)

        # 统计卡片行
        stats_row = QHBoxLayout()
        stats_row.setSpacing(12)
        self.card_total = _stat_card("待归档文件", "—")
        self.card_size = _stat_card("总体积", "—")
        self.card_source = _stat_card("源文件 (PSD/AI/CDR)", "—", accent=True)
        self.card_dest = _stat_card("归档位置", organizer.describe_dest(), compact=True)
        for c in (self.card_total, self.card_source, self.card_size, self.card_dest):
            stats_row.addWidget(c)
        root.addLayout(stats_row)

        # 计划表格
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["文件名", "类型", "年月", "原位置"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(1, 110)
        self.table.setColumnWidth(2, 90)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        root.addWidget(self.table, 1)

        # 进度条 + 状态
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        root.addWidget(self.progress)

        self.status = QLabel("点击「开始扫描」生成整理计划（只读扫描，不会移动任何文件）")
        self.status.setObjectName("Muted")
        root.addWidget(self.status)

        # 按钮行
        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self.btn_scan = QPushButton("开始扫描")
        self.btn_scan.setObjectName("PrimaryButton")
        self.btn_scan.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_scan.clicked.connect(self.start_scan)
        self.btn_apply = QPushButton("执行整理")
        self.btn_apply.setObjectName("PrimaryButton")
        self.btn_apply.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_apply.setEnabled(False)
        self.btn_apply.clicked.connect(self.confirm_apply)
        self.btn_undo = QPushButton("撤销上次整理")
        self.btn_undo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_undo.clicked.connect(self.confirm_undo)
        self.btn_open = QPushButton("打开归档文件夹")
        self.btn_open.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_open.clicked.connect(self.open_dest)
        for b in (self.btn_scan, self.btn_apply, self.btn_undo, self.btn_open):
            buttons.addWidget(b)
        buttons.addStretch()
        root.addLayout(buttons)

    def start_scan(self):
        self.status.setText("正在扫描…（只读，不会移动文件）")
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.btn_scan.setEnabled(False)
        signals = _ScanSignals()
        signals.progress.connect(self._on_progress)
        signals.done.connect(self._on_scan_done)
        signals.failed.connect(self._on_failed)
        self.thread_pool.start(_ScanRunnable(signals))

    def _on_failed(self, message: str):
        self.progress.setVisible(False)
        self.btn_scan.setEnabled(True)
        self.btn_apply.setEnabled(bool(self.plan and self.plan.records))
        self.status.setText(f"出错了：{message}")
        QMessageBox.warning(self, "操作失败", message)

    def _on_progress(self, current: int, total: int):
        if self.progress.maximum() == 0:
            self.progress.setRange(0, max(total, 1))
        self.progress.setValue(current)
        if total:
            self.status.setText(f"正在处理 {current}/{total}")

    def _on_scan_done(self, result: organizer.ScanResult):
        self.progress.setVisible(False)
        self.btn_scan.setEnabled(True)
        self.plan = result
        self._show_plan(result)

    def _show_plan(self, result: organizer.ScanResult):
        counts = result.count_by_category()
        source_count = sum(counts.get(c, 0) for c in ("源文件", "设计源文件"))
        self.card_total._value_label.setText(str(result.total))
        self.card_size._value_label.setText(organizer.fmt_size(result.total_bytes))
        self.card_source._value_label.setText(str(source_count))
        self.card_dest._value_label.setText(organizer.describe_dest())

        self.table.setRowCount(min(len(result.records), 500))
        for row, rec in enumerate(result.records[:500]):
            name_item = QTableWidgetItem(os.path.basename(rec.src))
            name_item.setToolTip(rec.dst)
            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, QTableWidgetItem(rec.category))
            self.table.setItem(row, 2, QTableWidgetItem(rec.month))
            self.table.setItem(row, 3, QTableWidgetItem(rec.src))

        if result.total == 0:
            self.status.setText("没有发现需要归档的设计文件")
            self.btn_apply.setEnabled(False)
        else:
            self.status.setText(
                f"共 {result.total} 个文件（{organizer.fmt_size(result.total_bytes)}）："
                "散文件按修改时间归入 G 盘「年月文件」分类子夹；你创建的文件夹【整个归档、不拆分】；"
                "不改原文件名，截图单独放。确认无误后点「执行整理」。"
            )
            self.btn_apply.setEnabled(True)

    def _ensure_dest_accessible(self) -> bool:
        """若当前归档目标盘不可访问（如换电脑无 G 盘或未插移动硬盘），引导用户先选一个有效目录。"""
        if organizer.is_dest_accessible():
            return True
        reply = QMessageBox.question(
            self,
            "归档位置不可访问",
            f"当前设定的归档位置「{organizer.DEST_DRIVE}」在本机无法访问（可能未插移动硬盘或盘符不同）。\n\n"
            "是否立即选择一个新的归档目标文件夹？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return False
        selected = QFileDialog.getExistingDirectory(self, "选择归档目标文件夹", str(Path.home()))
        if not selected:
            return False
        self.repository.set_archive_dest(selected)
        organizer.set_dest_drive(selected)
        self.card_dest._value_label.setText(organizer.describe_dest())
        return True

    def confirm_apply(self):
        if not self.plan or not self.plan.records:
            return
        if not self._ensure_dest_accessible():
            return
        # 若用户在扫描后修改了归档目标盘，提示重新扫描一次以更新计划路径
        first_dst = self.plan.records[0].dst
        if not first_dst.lower().startswith(str(organizer.DEST_DRIVE).rstrip("\\/").lower()):
            self.start_scan()
            QMessageBox.information(
                self, "归档位置已变更", "归档目标位置已更新，正在按新位置重新生成归档计划，请扫描完成后再次点「执行整理」。"
            )
            return
        box = QMessageBox(self)
        box.setWindowTitle("确认整理")
        box.setText(
            f"即将把 {self.plan.total} 个文件移入归档目录（年月文件夹 + 分类子文件夹）：\n"
            f"{organizer.DEST_DRIVE}\n\n"
            "· 散文件自动归入分类子夹（设计源文件/图片/文档/压缩包等）\n"
            "· 你创建的文件夹整体保留，不拆分\n"
            "· 移动完成后自动在每个月份文件夹生成「归档汇总.txt」\n"
            "· 只移动、不删除，可一键撤销\n\n确定执行吗？"
        )
        yes = box.addButton("执行", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("再看看", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is not yes:
            return
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.btn_apply.setEnabled(False)
        self.btn_scan.setEnabled(False)
        self.status.setText("正在整理并生成归档汇总…每批 10 个文件并逐一校验")
        signals = _ApplySignals()
        signals.progress.connect(self._on_progress)
        signals.done.connect(self._on_apply_done)
        signals.failed.connect(self._on_failed)
        self.thread_pool.start(_ApplyRunnable(signals))

    def _on_apply_done(self, payload):
        if isinstance(payload, tuple):
            report, cat_count, summary_count = payload
        else:
            report, cat_count, summary_count = payload, 0, 0
        self.progress.setVisible(False)
        self.btn_scan.setEnabled(True)
        if report.failed:
            detail = "\n".join(f"{p} ({err})" for p, err in report.failed[:5])
            more = f"\n… 等共 {len(report.failed)} 个失败" if len(report.failed) > 5 else ""
            self.status.setText(f"整理完成：成功 {report.moved} 个，失败 {len(report.failed)} 个")
            QMessageBox.warning(
                self, "部分失败",
                f"成功移动 {report.moved} 个文件。\n\n失败的文件：\n{detail}{more}",
            )
        else:
            self.status.setText(
                f"整理完成：移动 {report.moved} 个文件 → "
                f"分类 {cat_count} 个条目 → 生成 {summary_count} 个月份归档汇总"
            )

    def confirm_undo(self):
        box = QMessageBox(self)
        box.setWindowTitle("撤销整理")
        box.setText("将把上次「执行整理」移动的所有文件/文件夹放回原位置。\n确定继续吗？")
        yes = box.addButton("撤销", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is not yes:
            return
        restored = organizer.undo_last()
        self.status.setText(f"已回滚 {restored} 项到原位置。")
        self.start_scan()

    def open_dest(self):
        if not self._ensure_dest_accessible():
            return
        path = str(organizer.DEST_DRIVE)
        try:
            os.makedirs(path, exist_ok=True)
            from ..preview import reveal_file
            reveal_file(path)
        except OSError as exc:
            QMessageBox.warning(self, "无法打开归档目录", f"无法访问归档目录 {path}：\n{exc}")
