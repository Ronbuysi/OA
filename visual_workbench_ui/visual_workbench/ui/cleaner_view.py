# -*- coding: utf-8 -*-
"""工单清洗页：清理 xlsx 单元格内的特殊符号（Windows 非法字符/零宽字符等）。

优化亮点：
- 修复已选文件条「清空」按钮损坏的 QSS 语法，新增「+ 继续添加」按钮与全页拖拽追加支持；
- 变更徽章与表格清理后文本全面适配深/浅色主题；
- 清理冗余重复的 empty_icon.setStyleSheet 调用。
"""
from __future__ import annotations

import os

from PySide6.QtCore import QSize, Qt, QThread, Signal
from PySide6.QtGui import QBrush
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QFileDialog, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QMessageBox, QProgressBar, QPushButton,
    QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..cleaner_core import RULES, clean_workbook, scan_workbook
from .icons import icon as painted_icon, to_qcolor
from .layout import page_body
from . import styles as S
from .styles import cleaner_badge_colors

_RULE_SHORT = {
    "win_illegal": "Windows 非法字符",
    "fullwidth": "全角转半角",
    "zero_width": "零宽/不可见字符",
    "control": "控制字符",
    "variation": "emoji 变体符",
    "ooxml_escape": "OOXML 转义",
    "trim": "首尾空格",
    "emoji": "emoji 表情",
    "dingbat": "对勾 / 叉号类符号",
}


def _hint_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("HintLabel")
    lbl.setWordWrap(True)
    return lbl


def _badge(rule_id: str, count: int) -> QLabel:
    bg, fg = cleaner_badge_colors(rule_id)
    name = _RULE_SHORT.get(rule_id, rule_id)
    lbl = QLabel(f" {name} ×{count} ")
    lbl.setProperty("rule_id", rule_id)
    lbl.setStyleSheet(
        f"background:{bg};color:{fg};font-size:11px;font-weight:600;"
        "border-radius:8px;padding:2px 8px;border:0;"
    )
    return lbl


class CleanerWorker(QThread):
    """后台工作线程，避免界面卡死。"""

    finished_ok = Signal(dict)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, mode: str, files: list[str], rules: set, do_backup: bool = False):
        super().__init__()
        self.mode = mode  # "scan" | "clean"
        self.files = files
        self.rules = rules
        self.do_backup = do_backup

    def run(self):
        all_results = []
        try:
            for i, f in enumerate(self.files):
                self.progress.emit(f"({i + 1}/{len(self.files)}) {os.path.basename(f)}")
                if self.mode == "scan":
                    all_results.append(scan_workbook(f, self.rules))
                else:
                    all_results.append(clean_workbook(f, self.rules, backup=self.do_backup))
            self.finished_ok.emit({"mode": self.mode, "files": all_results})
        except Exception as e:  # noqa: BLE001
            import traceback
            self.failed.emit(f"{e}\n\n{traceback.format_exc()}")


class _DropZone(QFrame):
    """大拖拽区：点击选择 / 拖拽放入。"""

    filesAdded = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(116)
        self._drag_active = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 18, 24, 18)
        lay.setSpacing(8)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.icon_label = QLabel()
        self.icon_label.setStyleSheet("background:transparent;border:0;")
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.icon_label)

        main = QLabel("把运营工单 xlsx 拖到这里，或点击选择文件")
        main.setObjectName("DropZoneTitle")
        main.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(main)

        self.sub = _hint_label("支持多选 · 可一次拖入多个文件")
        self.sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.sub)
        self.refresh_theme()

    def refresh_theme(self):
        self.icon_label.setPixmap(painted_icon("file", S._TEXT_TERTIARY, 28).pixmap(28, 28))
        self.update()

    def paintEvent(self, event):
        from PySide6.QtGui import QPainter, QPainterPath, QPen

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._drag_active:
            painter.setBrush(to_qcolor(S._ACCENT_LIGHT))
            pen = QPen(to_qcolor(S._ACCENT), 1.6, Qt.PenStyle.DashLine)
        else:
            painter.setBrush(to_qcolor(S._SURFACE_2))
            pen = QPen(to_qcolor(S._BORDER_STRONG), 1.2, Qt.PenStyle.DashLine)
        pen.setDashPattern([5, 4])
        painter.setPen(pen)
        path = QPainterPath()
        path.addRoundedRect(0.8, 0.8, self.width() - 1.6, self.height() - 1.6, 16, 16)
        painter.drawPath(path)
        painter.end()

    def mousePressEvent(self, event):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择要清理的 xlsx 文件", "", "Excel 文件 (*.xlsx *.xlsm)"
        )
        if files:
            self.filesAdded.emit(files)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._drag_active = True
            self.update()

    def dragLeaveEvent(self, event):
        self._drag_active = False
        self.update()

    def dropEvent(self, event):
        self._drag_active = False
        self.update()
        paths = []
        for url in event.mimeData().urls():
            p = url.toLocalFile()
            if p.lower().endswith((".xlsx", ".xlsm")) and os.path.exists(p):
                paths.append(p)
        if paths:
            self.filesAdded.emit(paths)


class CleanerPage(QWidget):
    """xlsx 特殊符号清理 —— 工作台内嵌页面。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker: CleanerWorker | None = None
        self._files: list[str] = []
        self.setAcceptDrops(True)  # 允许已选文件后继续往页面任意位置拖入新文件
        self._build_ui()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [
            url.toLocalFile() for url in event.mimeData().urls()
            if url.toLocalFile().lower().endswith((".xlsx", ".xlsm"))
            and os.path.exists(url.toLocalFile())
        ]
        if paths:
            self._add_files(paths)

    def _build_ui(self):
        root = page_body(self, spacing=14, margins=(32, 28, 32, 20))

        title_row = QHBoxLayout()
        title_row.setSpacing(14)
        title = QLabel("工单清洗")
        title.setObjectName("PageTitle")
        title_tag = QLabel("XLSX CLEANER")
        title_tag.setObjectName("TitleTag")
        title_row.addWidget(title)
        title_row.addWidget(title_tag)
        title_row.addStretch()
        root.addLayout(title_row)

        self.drop_zone = _DropZone()
        self.drop_zone.filesAdded.connect(self._add_files)
        root.addWidget(self.drop_zone)

        # 已选文件条（含「+ 继续添加」与「清空」按钮，不再锁死追加操作）
        self.files_bar = QFrame()
        self.files_bar.setObjectName("FilesBar")
        files_lay = QHBoxLayout(self.files_bar)
        files_lay.setContentsMargins(14, 8, 14, 8)
        files_lay.setSpacing(10)
        self.files_label = QLabel()
        self.files_label.setObjectName("BodyLabel")

        add_more_btn = QPushButton("  继续添加")
        add_more_btn.setObjectName("GhostAccentButton")
        add_more_btn.setIcon(painted_icon("plus", S._ACCENT, 13))
        add_more_btn.setIconSize(QSize(13, 13))
        add_more_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_more_btn.setFixedHeight(28)
        add_more_btn.clicked.connect(self._pick_more_files)
        self._add_more_btn = add_more_btn

        clear_btn = QPushButton("清空")
        clear_btn.setObjectName("GhostButton")
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.setFixedHeight(28)
        clear_btn.clicked.connect(self._clear_files)

        files_lay.addWidget(self.files_label, 1)
        files_lay.addWidget(add_more_btn)
        files_lay.addWidget(clear_btn)
        self.files_bar.hide()
        root.addWidget(self.files_bar)

        # ── 主体：左规则（窄）+ 右结果表（宽）──
        mid = QHBoxLayout()
        mid.setSpacing(14)

        rule_card = QFrame()
        rule_card.setObjectName("GlassCard")
        rule_card.setFixedWidth(240)
        rv = QVBoxLayout(rule_card)
        rv.setContentsMargins(18, 16, 18, 14)
        rv.setSpacing(8)
        head = QLabel("清理规则")
        head.setObjectName("CardTitle")
        rv.addWidget(head)
        self.rule_checks: dict[str, QCheckBox] = {}
        for rid, name, default, desc, *_ in RULES:
            cb = QCheckBox(name)
            cb.setChecked(default)
            cb.setToolTip(desc)
            cb.setToolTipDuration(8000)
            cb.toggled.connect(self._update_rule_summary)
            self.rule_checks[rid] = cb
            rv.addWidget(cb)
        rv.addSpacing(6)
        self.backup_check = QCheckBox("清理前备份 .bak")
        self.backup_check.setChecked(False)
        self.backup_check.setToolTip("勾选后清理前会自动生成 .bak 备份文件")
        rv.addWidget(self.backup_check)
        rv.addStretch()
        self.rule_summary = _hint_label("")
        rv.addWidget(self.rule_summary)
        mid.addWidget(rule_card)

        result_card = QFrame()
        result_card.setObjectName("GlassCard")
        pv = QVBoxLayout(result_card)
        pv.setContentsMargins(14, 12, 14, 12)
        pv.setSpacing(8)

        self.result_stack = QStackedWidget()
        empty = QFrame()
        empty_lay = QVBoxLayout(empty)
        empty_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_lay.setSpacing(8)
        self._empty_icon = QLabel()
        self._empty_icon.setPixmap(painted_icon("search", S._TEXT_TERTIARY, 28).pixmap(28, 28))
        self._empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_icon.setStyleSheet("background:transparent;border:0;")
        empty_lay.addWidget(self._empty_icon)
        self.empty_label = _hint_label("还没有扫描结果 —— 添加文件后点「扫描预览」看看哪里有问题")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_lay.addWidget(self.empty_label)

        self.table = QTableWidget(0, 4)
        self.table.setObjectName("BorderlessTable")
        self.table.setHorizontalHeaderLabels(["位置", "原始内容", "清理后", "变更"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.result_stack.addWidget(empty)
        self.result_stack.addWidget(self.table)
        pv.addWidget(self.result_stack)

        self.result_summary = _hint_label("")
        self.result_summary.hide()
        pv.addWidget(self.result_summary)
        mid.addWidget(result_card, 1)
        root.addLayout(mid, stretch=1)

        # ── 底部固定操作栏 ──
        action_bar = QFrame()
        action_bar.setObjectName("ActionBar")
        ab = QHBoxLayout(action_bar)
        ab.setContentsMargins(18, 12, 18, 12)
        ab.setSpacing(12)
        self.progress = QProgressBar()
        self.progress.setMaximum(1)
        self.progress.setValue(0)
        self.progress.setMaximumHeight(6)
        self.progress.setTextVisible(False)
        self.status_label = QLabel("准备就绪 —— 拖入工单文件开始")
        self.status_label.setObjectName("BodyLabel")
        ab.addWidget(self.status_label, 1)
        ab.addWidget(self.progress, 1)
        self.progress.hide()

        self.btn_scan = QPushButton("  扫描预览")
        self.btn_scan.setIcon(painted_icon("search", "#FFFFFF", 16))
        self.btn_scan.setIconSize(QSize(16, 16))
        self.btn_scan.setObjectName("PrimaryButton")
        self.btn_scan.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_scan.setMinimumHeight(40)
        self.btn_scan.clicked.connect(self._start_scan)

        self.btn_clean = QPushButton("  一键清理")
        self.btn_clean.setIcon(painted_icon("cleaner", S._TEXT_SECONDARY, 16))
        self.btn_clean.setIconSize(QSize(16, 16))
        self.btn_clean.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clean.setMinimumHeight(40)
        self.btn_clean.setEnabled(False)
        self.btn_clean.clicked.connect(self._start_clean)
        ab.addWidget(self.btn_scan)
        ab.addWidget(self.btn_clean)
        root.addWidget(action_bar)

        self._update_rule_summary()

    def _pick_more_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择要追加的 xlsx 文件", "", "Excel 文件 (*.xlsx *.xlsm)"
        )
        if files:
            self._add_files(files)

    def _add_files(self, paths: list[str]):
        existing = self._get_files()
        added = 0
        for p in paths:
            p = os.path.normpath(p)
            if p not in existing:
                self._files.append(p)
                existing.append(p)
                added += 1
        if added:
            self._sync_files_bar()
            self._set_status(f"已选 {len(self._files)} 个文件（新增 {added} 个），点「扫描预览」开始检查")
            self.table.setRowCount(0)
            self.result_stack.setCurrentIndex(0)

    def _sync_files_bar(self):
        n = len(self._files)
        if n == 0:
            self.files_bar.hide()
            self.drop_zone.show()
            return
        self.drop_zone.hide()
        self.files_bar.show()
        if n == 1:
            self.files_label.setText(os.path.basename(self._files[0]))
            self.files_label.setToolTip(self._files[0])
        else:
            total = "、".join(os.path.basename(f) for f in self._files[:3])
            more = f" 等 {n} 个文件" if n > 3 else ""
            self.files_label.setText(f"{total}{more}")
            self.files_label.setToolTip("\n".join(self._files))

    def _clear_files(self):
        self._files.clear()
        self._sync_files_bar()
        self.table.setRowCount(0)
        self.result_stack.setCurrentIndex(0)
        self.result_summary.hide()
        self.btn_clean.setEnabled(False)
        self._set_status("准备就绪 —— 拖入工单文件开始")

    def _get_files(self) -> list[str]:
        return list(self._files)

    def _get_rules(self) -> set:
        return {rid for rid, cb in self.rule_checks.items() if cb.isChecked()}

    def _update_rule_summary(self):
        enabled = self._get_rules()
        order = [rule[0] for rule in RULES]
        names = [_RULE_SHORT.get(r, r) for r in order if r in enabled]
        self.rule_summary.setText("已启用：" + ("、".join(names) if names else "无"))

    def _set_status(self, text: str):
        self.status_label.setText(text)

    def _set_busy(self, busy: bool, text: str = ""):
        self.btn_scan.setEnabled(not busy)
        self.btn_clean.setEnabled(not busy and self.table.rowCount() > 0)
        self.drop_zone.setEnabled(not busy)
        self.progress.setVisible(busy)
        self._set_status(text or "准备就绪")

    def _start_scan(self):
        files = self._get_files()
        if not files:
            QMessageBox.information(self, "提示", "请先拖入 xlsx 文件")
            return
        self.table.setRowCount(0)
        self.result_stack.setCurrentIndex(0)
        self._run_worker("scan", files)

    def _start_clean(self):
        files = self._get_files()
        if not files:
            return
        if self.table.rowCount() == 0:
            ret = QMessageBox.question(
                self, "确认", "还没有扫描预览。建议先扫描确认要清理的内容。\n是否直接清理？"
            )
            if ret != QMessageBox.Yes:
                return
        do_backup = self.backup_check.isChecked()
        tip = "会先备份为 .bak 文件" if do_backup else "不会生成备份，改动直接写入原文件"
        ret = QMessageBox.question(
            self, "确认清理", f"将对 {len(files)} 个文件执行清理，{tip}。\n继续？"
        )
        if ret != QMessageBox.Yes:
            return
        self._run_worker("clean", files, do_backup)

    def _run_worker(self, mode: str, files: list[str], do_backup: bool = False):
        self._set_busy(True, f"处理中…（0/{len(files)}）")
        self.progress.setMaximum(len(files))
        self.progress.setValue(0)
        self.worker = CleanerWorker(mode, files, self._get_rules(), do_backup)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_done)
        self.worker.failed.connect(self._on_fail)
        self.worker.start()

    def _on_progress(self, text: str):
        self._set_status(f"处理中… {text}")
        self.progress.setValue(self.progress.value() + 1)

    def _on_done(self, payload: dict):
        mode = payload["mode"]
        files = payload["files"]
        if mode == "scan":
            total_hits = 0
            ok_brush = QBrush(to_qcolor(S._SUCCESS))
            for r in files:
                total_hits += len(r["results"])
                for item in r["results"]:
                    row = self.table.rowCount()
                    self.table.insertRow(row)
                    self.table.setItem(row, 0, QTableWidgetItem(
                        f"{os.path.basename(r['file'])} · {item['sheet']}!{item['cell']}"
                    ))
                    self.table.setItem(row, 1, QTableWidgetItem(item["original"]))
                    clean_item = QTableWidgetItem(item["cleaned"])
                    clean_item.setForeground(ok_brush)
                    self.table.setItem(row, 2, clean_item)

                    wrap = QWidget()
                    wrap.setStyleSheet("background: transparent;")
                    wl = QHBoxLayout(wrap)
                    wl.setContentsMargins(0, 0, 0, 0)
                    wl.setSpacing(4)
                    for rid, _name, n in item["changes"][:3]:
                        wl.addWidget(_badge(rid, n))
                    if len(item["changes"]) > 3:
                        more = QLabel(f"+{len(item['changes']) - 3}")
                        more.setObjectName("HintLabel")
                        wl.addWidget(more)
                    wl.addStretch()
                    self.table.setRowHeight(row, 32)
                    self.table.setCellWidget(row, 3, wrap)
            self.result_stack.setCurrentIndex(1 if total_hits else 0)
            if total_hits == 0:
                self.empty_label.setText("扫描完成 —— 文件很干净 ✓ 没有需要清理的内容")
            self.result_summary.show()
            self.result_summary.setText(
                f"共扫描 {len(files)} 个文件，发现 {total_hits} 处需要清理"
            )
            self.btn_clean.setEnabled(total_hits > 0)
            self._set_status(
                f"扫描完成：{total_hits} 处待清理" + ("，确认无误后点「一键清理」" if total_hits else "")
            )
        else:
            total = sum(r["hits"] for r in files)
            backups = [r["backup"] for r in files if r.get("backup")]
            msg = f"清理完成：共替换 {total} 处"
            if backups:
                msg += f"，已生成 {len(backups)} 个 .bak 备份"
            self.result_summary.show()
            self.result_summary.setText(msg + " ✓")
            self._set_status(msg + " ✓")
            QMessageBox.information(self, "完成", msg)
            self.table.setRowCount(0)
            self.result_stack.setCurrentIndex(0)
            self.empty_label.setText("清理完成 ✓ 可以继续拖入下一批文件")
            self.btn_clean.setEnabled(False)
        self._set_busy(False)

    def _on_fail(self, err: str):
        self._set_busy(False, "出错了")
        self.progress.setValue(0)
        QMessageBox.critical(self, "处理失败", err[:2000])

    def refresh_theme(self):
        """主题切换时同步图标、拖拽区、表格清理色与规则徽章。"""
        self.drop_zone.refresh_theme()
        self._empty_icon.setPixmap(painted_icon("search", S._TEXT_TERTIARY, 28).pixmap(28, 28))
        self.btn_clean.setIcon(painted_icon("cleaner", S._TEXT_SECONDARY, 16))
        self._add_more_btn.setIcon(painted_icon("plus", S._ACCENT, 13))
        ok_brush = QBrush(to_qcolor(S._SUCCESS))
        for row in range(self.table.rowCount()):
            clean_item = self.table.item(row, 2)
            if clean_item is not None:
                clean_item.setForeground(ok_brush)
            wrap = self.table.cellWidget(row, 3)
            if wrap is not None:
                for lbl in wrap.findChildren(QLabel):
                    rid = lbl.property("rule_id")
                    if rid:
                        bg, fg = cleaner_badge_colors(str(rid))
                        lbl.setStyleSheet(
                            f"background:{bg};color:{fg};font-size:11px;font-weight:600;"
                            "border-radius:8px;padding:2px 8px;border:0;"
                        )
