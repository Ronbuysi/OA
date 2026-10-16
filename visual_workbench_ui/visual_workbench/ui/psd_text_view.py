# -*- coding: utf-8 -*-
"""PSD 文字替换页：按「精确整串」批量改 PSD 里文字图层的内容。

优化亮点：
- 修复底部操作栏进度条未隐藏导致平时常驻一条空灰线的 Bug；
- 修复已选文件条「清空」按钮损坏的 QSS 语法，新增「+ 继续添加」按钮与全页拖拽追加支持；
- 接入语义化 QSS 类名（`SubSectionTitle`、`DropZoneTitle`、`HintLabel`、`BorderlessTable`），深浅色切换零残留。
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QThread, Signal
from PySide6.QtGui import QBrush
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QFileDialog, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QMessageBox, QProgressBar, QPushButton,
    QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..config import app_data_dir
from ..psd_text import (
    ReplaceRule, TextScanResult, photoshop_exe_path, photoshop_is_running,
    replace_text_in_psd, scan_text_layers, set_photoshop_exe,
)
from .empty_state import EmptyStatePanel
from .icons import icon as painted_icon, to_qcolor
from .layout import page_body
from . import styles as S

PSD_SUFFIXES = (".psd", ".psb")
_OPEN_FILTER = "Photoshop 源文件 (*.psd *.psb)"


def script_dir() -> Path:
    """生成的 jsx / 日志放这里（固定、好找，不污染素材盘）。"""
    path = app_data_dir() / "psd_text"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _hint(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("HintLabel")
    lbl.setWordWrap(True)
    return lbl


class ScanThread(QThread):
    progress = Signal(int, int, str)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, paths: list[str]):
        super().__init__()
        self.paths = list(paths)
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            out = []
            for i, p in enumerate(self.paths):
                if self._cancel:
                    break
                self.progress.emit(i, len(self.paths), f"正在扫描：{Path(p).name}")
                out.append(scan_text_layers(p))
            self.finished_ok.emit(out)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class ReplaceThread(QThread):
    """逐文件执行替换。优先走 PowerShell COM / CLI 自动通道，失败再提示手动运行。"""

    stage = Signal(str)
    need_manual = Signal(str, str)
    file_index = Signal(int, int, str)
    file_done = Signal(object)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, paths: list[str], rules: list[ReplaceRule], backup: bool):
        super().__init__()
        self.paths = list(paths)
        self.rules = list(rules)
        self.backup = backup
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            results = []
            total = len(self.paths)
            for i, path in enumerate(self.paths):
                if self._cancel:
                    break
                name = Path(path).name
                self.file_index.emit(i, total, name)
                result = replace_text_in_psd(
                    path, self.rules,
                    work_dir=script_dir(),
                    backup=self.backup,
                    save=True,
                    try_auto=True,
                    auto_probe_timeout=12,
                    wait_timeout=3600,
                    on_stage=lambda t, _n=name: self.stage.emit(f"{_n} · {t}"),
                    on_need_manual=lambda jsx, log, _i=i, _t=total, _n=name:
                        self.need_manual.emit(jsx, log),
                    should_cancel=lambda: self._cancel,
                )
                results.append(result)
                self.file_done.emit(result)
            self.finished_ok.emit(results)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class _DropZone(QFrame):
    filesAdded = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(104)
        self._active = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 18, 24, 18)
        lay.setSpacing(6)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.icon_label = QLabel()
        self.icon_label.setStyleSheet("background:transparent;border:0;")
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.icon_label)

        main = QLabel("把 PSD / PSB 拖到这里，或点击选择文件")
        main.setObjectName("DropZoneTitle")
        main.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(main)

        sub = _hint("先扫描列出全部文字内容，再从清单里点选要换的那一条")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(sub)
        self.refresh_theme()

    def refresh_theme(self):
        self.icon_label.setPixmap(painted_icon("file", S._TEXT_TERTIARY, 24).pixmap(24, 24))
        self.update()

    def paintEvent(self, event):
        from PySide6.QtGui import QPainter, QPainterPath, QPen

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._active:
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
            self, "选择 PSD / PSB 文件", "", _OPEN_FILTER
        )
        if files:
            self.filesAdded.emit(files)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._active = True
            self.update()

    def dragLeaveEvent(self, event):
        self._active = False
        self.update()

    def dropEvent(self, event):
        self._active = False
        self.update()
        paths = [u.toLocalFile() for u in event.mimeData().urls()]
        paths = [p for p in paths if p.lower().endswith(PSD_SUFFIXES) and os.path.exists(p)]
        if paths:
            self.filesAdded.emit(paths)


class PsdTextPage(QWidget):
    """PSD 文字批量替换 —— 工作台内嵌页面。"""

    exported = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scan_thread: ScanThread | None = None
        self.replace_thread: ReplaceThread | None = None
        self._files: list[str] = []
        self._scans: list[TextScanResult] = []
        self.setAcceptDrops(True)  # 允许已选文件后继续拖入追加
        self._build_ui()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [
            u.toLocalFile() for u in event.mimeData().urls()
            if u.toLocalFile().lower().endswith(PSD_SUFFIXES)
            and os.path.exists(u.toLocalFile())
        ]
        if paths:
            self.add_files(paths)

    def _build_ui(self):
        root = page_body(self, spacing=12, margins=(32, 28, 32, 20))

        title_row = QHBoxLayout()
        title_row.setSpacing(14)
        title = QLabel("PSD 文字替换")
        title.setObjectName("PageTitle")
        tag = QLabel("TEXT REPLACE")
        tag.setObjectName("TitleTag")
        title_row.addWidget(title)
        title_row.addWidget(tag)
        title_row.addStretch()
        root.addLayout(title_row)

        self.intro = _hint(
            "按「精确整串」替换文字图层内容：只动文本图层、只换完全匹配的那一串，"
            "不做正则也不做关键词模糊匹配。执行在 Photoshop 内完成，工作台负责"
            "扫描、备份、生成脚本和改后校验。"
        )
        root.addWidget(self.intro)

        self.drop_zone = _DropZone()
        self.drop_zone.filesAdded.connect(self.add_files)
        root.addWidget(self.drop_zone)

        self.files_bar = QFrame()
        self.files_bar.setObjectName("FilesBar")
        fb = QHBoxLayout(self.files_bar)
        fb.setContentsMargins(14, 8, 14, 8)
        fb.setSpacing(10)
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
        clear_btn.clicked.connect(self._clear)

        fb.addWidget(self.files_label, 1)
        fb.addWidget(add_more_btn)
        fb.addWidget(clear_btn)
        self.files_bar.hide()
        root.addWidget(self.files_bar)

        # ── 左：扫描到的文字清单 / 右：替换规则 ──
        mid = QHBoxLayout()
        mid.setSpacing(12)

        left = QFrame()
        left.setObjectName("GlassCard")
        lv = QVBoxLayout(left)
        lv.setContentsMargins(14, 12, 14, 12)
        lv.setSpacing(8)
        lv_head = QHBoxLayout()
        lv_head.addWidget(self._section_label("① PSD 里的文字（去重后）"))
        lv_head.addStretch()
        self.add_rule_btn = QPushButton("→ 加入规则")
        self.add_rule_btn.setFixedHeight(28)
        self.add_rule_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_rule_btn.setEnabled(False)
        self.add_rule_btn.setToolTip("把选中的那一条原样填进右侧的「查找内容」")
        self.add_rule_btn.clicked.connect(self._add_rule_from_scan)
        lv_head.addWidget(self.add_rule_btn)
        lv.addLayout(lv_head)

        self.scan_table = QTableWidget(0, 2)
        self.scan_table.setHorizontalHeaderLabels(["内容", "出现次数"])
        sh = self.scan_table.horizontalHeader()
        sh.setSectionResizeMode(0, QHeaderView.Stretch)
        sh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._prep_table(self.scan_table)
        self.scan_table.itemSelectionChanged.connect(
            lambda: self.add_rule_btn.setEnabled(bool(self.scan_table.selectedItems()))
        )
        self.scan_table.itemDoubleClicked.connect(lambda _item: self._add_rule_from_scan())
        self.scan_stack = QStackedWidget()
        self.scan_empty = EmptyStatePanel("search")
        self.scan_empty.action_button.clicked.connect(self._on_scan_empty_action)
        self.scan_stack.addWidget(self.scan_empty)
        self.scan_stack.addWidget(self.scan_table)
        lv.addWidget(self.scan_stack, 1)
        self.scan_hint = _hint("还没有扫描 —— 加上 PSD 后点「扫描文字」（双击行也可直接加入规则）")
        lv.addWidget(self.scan_hint)
        mid.addWidget(left, 1)

        right = QFrame()
        right.setObjectName("GlassCard")
        rv = QVBoxLayout(right)
        rv.setContentsMargins(14, 12, 14, 12)
        rv.setSpacing(8)
        rv_head = QHBoxLayout()
        rv_head.addWidget(self._section_label("② 替换规则（查找 → 替换为）"))
        rv_head.addStretch()
        del_btn = QPushButton("删除选中")
        del_btn.setFixedHeight(28)
        del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        del_btn.clicked.connect(self._remove_rule)
        rv_head.addWidget(del_btn)
        rv.addLayout(rv_head)

        self.rule_table = QTableWidget(0, 2)
        self.rule_table.setHorizontalHeaderLabels(["查找内容（精确整串）", "替换为"])
        rh = self.rule_table.horizontalHeader()
        rh.setSectionResizeMode(0, QHeaderView.Stretch)
        rh.setSectionResizeMode(1, QHeaderView.Stretch)
        self._prep_table(self.rule_table)
        self.rule_table.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
        )
        self.rule_stack = QStackedWidget()
        self.rule_empty = EmptyStatePanel("layers")
        self.rule_empty.action_button.clicked.connect(self._on_rule_empty_action)
        self.rule_stack.addWidget(self.rule_empty)
        self.rule_stack.addWidget(self.rule_table)
        rv.addWidget(self.rule_stack, 1)
        rv.addWidget(_hint("「查找内容」列建议从左侧点选加入，避免手打出错；「替换为」留空 = 删除这段文字"))
        mid.addWidget(right, 1)

        root.addLayout(mid, stretch=1)

        # ── 手动运行提示卡（需要时才显示）──
        self.manual_card = QFrame()
        self.manual_card.setObjectName("GlassCard")
        mc = QVBoxLayout(self.manual_card)
        mc.setContentsMargins(16, 12, 16, 12)
        mc.setSpacing(6)
        mc.addWidget(self._section_label("③ 现在请在 Photoshop 里运行脚本"))
        self.manual_path = QLabel()
        self.manual_path.setObjectName("BodyLabel")
        self.manual_path.setWordWrap(True)
        mc.addWidget(self.manual_path)
        mc.addWidget(_hint(
            "Photoshop →「文件 > 脚本 > 浏览…」→ 选中上面这个 .jsx 文件。"
            "跑完工作台会自动读到结果并校验，不用回来点任何按钮。"
        ))
        row = QHBoxLayout()
        row.setSpacing(8)
        copy_btn = QPushButton("复制脚本路径")
        copy_btn.setFixedHeight(28)
        copy_btn.clicked.connect(self._copy_manual_path)
        row.addWidget(copy_btn)
        open_btn = QPushButton("打开脚本文件夹")
        open_btn.setFixedHeight(28)
        open_btn.clicked.connect(self._open_script_dir)
        row.addWidget(open_btn)
        row.addStretch()
        mc.addLayout(row)
        self.manual_card.hide()
        root.addWidget(self.manual_card)

        # ── 底部操作栏 ──
        bar = QFrame()
        bar.setObjectName("ActionBar")
        ab = QHBoxLayout(bar)
        ab.setContentsMargins(18, 12, 18, 12)
        ab.setSpacing(12)

        self.backup_check = QCheckBox("替换前备份原文件")
        self.backup_check.setChecked(True)
        self.backup_check.setToolTip("在 PSD 同目录生成 <文件名>.bak_日期时间")
        ab.addWidget(self.backup_check)
        ab.addSpacing(8)

        self.status = QLabel("准备就绪 —— 拖入 PSD 开始")
        self.status.setObjectName("BodyLabel")
        ab.addWidget(self.status, 1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setMaximumHeight(6)
        self.progress.setTextVisible(False)
        ab.addWidget(self.progress, 1)
        self.progress.hide()  # 空闲时隐藏，不再常驻占掉半截状态栏

        self.btn_scan = QPushButton("  扫描文字")
        self.btn_scan.setIcon(painted_icon("search", S._TEXT_SECONDARY, 16))
        self.btn_scan.setIconSize(QSize(16, 16))
        self.btn_scan.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_scan.setMinimumHeight(40)
        self.btn_scan.setEnabled(False)
        self.btn_scan.clicked.connect(self._start_scan)
        ab.addWidget(self.btn_scan)

        self.btn_run = QPushButton("  开始替换")
        self.btn_run.setObjectName("PrimaryButton")
        self.btn_run.setIcon(painted_icon("layers", "#FFFFFF", 16))
        self.btn_run.setIconSize(QSize(16, 16))
        self.btn_run.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_run.setMinimumHeight(40)
        self.btn_run.clicked.connect(self._start_replace)
        ab.addWidget(self.btn_run)

        self.btn_cancel = QPushButton("停止等待")
        self.btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cancel.setMinimumHeight(40)
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.setToolTip("只停止工作台的等待，Photoshop 里正在跑的脚本不会中断")
        self.btn_cancel.clicked.connect(self._cancel)
        ab.addWidget(self.btn_cancel)

        root.addWidget(bar)
        self._refresh_ps_hint()
        self._update_step_states()

    def _on_scan_empty_action(self):
        if self._files:
            self._start_scan()
        else:
            self._pick_more_files()

    def _on_rule_empty_action(self):
        if not self._files:
            self._pick_more_files()
        elif self.scan_table.rowCount() == 0:
            self._start_scan()
        else:
            self.scan_table.setFocus()

    def _update_step_states(self):
        has_files = bool(self._files)
        has_scan = self.scan_table.rowCount() > 0
        has_rules = self.rule_table.rowCount() > 0
        self.scan_stack.setCurrentWidget(self.scan_table if has_scan else self.scan_empty)
        self.rule_stack.setCurrentWidget(self.rule_table if has_rules else self.rule_empty)
        self.scan_empty.set_content(
            "读取 PSD 中的文字" if has_files else "添加 PSD 开始",
            "点击扫描文字，工作台会列出所有文字图层的内容与出现次数。"
            if has_files else "选择 PSD / PSB 文件，先确认里面有哪些文字需要修改。",
            "扫描文字" if has_files else "选择 PSD 文件",
        )
        self.rule_empty.set_content(
            "建立替换规则",
            "从左侧选择一条文字，加入规则后填写替换内容。",
            "选择左侧文字" if has_scan else ("扫描文字" if has_files else "选择 PSD 文件"),
        )
        self.btn_run.setEnabled(has_files and has_rules)

    def _pick_more_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择要追加的 PSD / PSB 文件", "", _OPEN_FILTER
        )
        if files:
            self.add_files(files)

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("SubSectionTitle")
        return lbl

    @staticmethod
    def _prep_table(table: QTableWidget):
        table.setObjectName("BorderlessTable")
        table.verticalHeader().hide()
        table.setShowGrid(False)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setWordWrap(False)

    def _refresh_ps_hint(self):
        exe = photoshop_exe_path()
        if exe is None:
            self.status.setText("提示：未在默认路径找到 Photoshop（点「开始替换」时可手动指定 Photoshop.exe 或走手动脚本模式）")
        elif not photoshop_is_running():
            self.status.setText("准备就绪 · Photoshop 未运行（首次递交会自动启动它，会慢一点）")

    def add_files(self, paths) -> None:
        if isinstance(paths, str):
            paths = [paths]
        existing = set(self._files)
        added = 0
        for p in paths:
            n = os.path.normpath(str(p))
            if n.lower().endswith(PSD_SUFFIXES) and n not in existing:
                self._files.append(n)
                existing.add(n)
                added += 1
        if added:
            self._sync_files_bar()
            self.scan_table.setRowCount(0)
            self.rule_table.setRowCount(0)
            self._scans = []
            self.status.setText(f"已选 {len(self._files)} 个文件（新增 {added} 个），点「扫描文字」列出文字内容")
            self._update_step_states()

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
        else:
            self.files_label.setText(
                "、".join(os.path.basename(f) for f in self._files[:3])
                + (f" 等 {n} 个" if n > 3 else "")
            )
        self.files_label.setToolTip("\n".join(self._files))

    def _clear(self):
        self._files.clear()
        self._scans = []
        self._sync_files_bar()
        self.scan_table.setRowCount(0)
        self.rule_table.setRowCount(0)
        self.manual_card.hide()
        self.progress.hide()
        self.scan_hint.setText("还没有扫描 —— 加上 PSD 后点「扫描文字」")
        self.status.setText("准备就绪 —— 拖入 PSD 开始")
        self._update_step_states()

    def _start_scan(self):
        if not self._files:
            QMessageBox.information(self, "提示", "请先拖入 PSD / PSB 文件")
            return
        self.btn_scan.setEnabled(False)
        self.progress.show()
        self.progress.setRange(0, len(self._files))
        self.progress.setValue(0)
        self.scan_thread = ScanThread(self._files)
        self.scan_thread.progress.connect(
            lambda d, t, s: (self.progress.setValue(d), self.status.setText(s))
        )
        self.scan_thread.finished_ok.connect(self._on_scan_done)
        self.scan_thread.failed.connect(self._on_failed)
        self.scan_thread.start()

    def _on_scan_done(self, scans: list):
        self.scan_thread = None
        self.btn_scan.setEnabled(True)
        self.progress.setValue(self.progress.maximum())
        self.progress.hide()
        self._scans = scans

        rows = 0
        merged: dict[str, int] = {}
        for s in scans:
            if s.error:
                continue
            for content, count in s.unique_contents():
                merged[content] = merged.get(content, 0) + count
        self.scan_table.setRowCount(0)
        muted_brush = QBrush(to_qcolor(S._TEXT_TERTIARY))
        for content, count in sorted(merged.items(), key=lambda kv: (-kv[1], kv[0])):
            r = self.scan_table.rowCount()
            self.scan_table.insertRow(r)
            item = QTableWidgetItem(content)
            item.setData(Qt.ItemDataRole.UserRole, content)
            self.scan_table.setItem(r, 0, item)
            cnt = QTableWidgetItem(str(count))
            cnt.setForeground(muted_brush)
            self.scan_table.setItem(r, 1, cnt)
            rows += 1

        texts = sum(s.text_count for s in scans)
        errs = [s for s in scans if s.error]
        self.scan_hint.setText(
            f"共 {len(scans)} 个文件 · {texts} 个文字图层 · {rows} 种不同写法"
            + (f" · {len(errs)} 个文件打不开" if errs else "")
        )
        self.status.setText(f"扫描完成：{rows} 种写法，从左侧选一条（或双击）加入规则")
        self._update_step_states()

    def _add_rule_from_scan(self):
        items = self.scan_table.selectedItems()
        if not items:
            return
        row = items[0].row()
        content = self.scan_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if content is None:
            content = self.scan_table.item(row, 0).text()
        for r in range(self.rule_table.rowCount()):
            if self.rule_table.item(r, 0) and self.rule_table.item(r, 0).text() == content:
                self.status.setText("这条已经在规则里了")
                return
        r = self.rule_table.rowCount()
        self.rule_table.insertRow(r)
        find_item = QTableWidgetItem(content)
        find_item.setFlags(find_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.rule_table.setItem(r, 0, find_item)
        self.rule_table.setItem(r, 1, QTableWidgetItem(""))
        self._update_step_states()
        self.rule_table.editItem(self.rule_table.item(r, 1))
        self.status.setText("已加入规则，现在填「替换为」")

    def _remove_rule(self):
        rows = sorted({i.row() for i in self.rule_table.selectedItems()}, reverse=True)
        for r in rows:
            self.rule_table.removeRow(r)
        self._update_step_states()

    def _collect_rules(self) -> list[ReplaceRule]:
        rules = []
        for r in range(self.rule_table.rowCount()):
            old_item = self.rule_table.item(r, 0)
            new_item = self.rule_table.item(r, 1)
            old = old_item.text() if old_item else ""
            new = new_item.text() if new_item else ""
            if old:
                rules.append(ReplaceRule(old=old, new=new))
        return rules

    def _start_replace(self):
        if not self._files:
            QMessageBox.information(self, "提示", "请先拖入 PSD / PSB 文件")
            return
        if photoshop_exe_path() is None and os.name == "nt":
            reply = QMessageBox.question(
                self,
                "定位 Photoshop.exe",
                "未在默认安装目录找到 Photoshop.exe。\n\n"
                "是否手动定位你电脑上的 Photoshop.exe？（选「否」则直接生成 .jsx 脚本供手动运行）",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if reply == QMessageBox.StandardButton.Yes:
                exe_file, _ = QFileDialog.getOpenFileName(
                    self, "选择 Photoshop.exe", "C:\\", "可执行文件 (Photoshop.exe *.exe)"
                )
                if exe_file:
                    set_photoshop_exe(exe_file)
                    win = self.window()
                    repo = getattr(win, "repository", None)
                    if repo is not None:
                        try:
                            repo.set_setting("photoshop_exe", exe_file)
                        except Exception:
                            pass
        rules = self._collect_rules()
        if not rules:
            QMessageBox.information(
                self, "还没有规则",
                "请先从左侧「PSD 里的文字」选一条，点「→ 加入规则」，再填「替换为」。",
            )
            return
        hits = 0
        for scan in self._scans:
            for info in scan.layers:
                if any(r.old in info.content for r in rules):
                    hits += 1
        if self._scans:
            ret = QMessageBox.question(
                self, "确认替换",
                f"将对 {len(self._files)} 个 PSD 执行 {len(rules)} 条规则。\n\n"
                f"预计命中 {hits} 个文字图层"
                + ("（备份已勾选）" if self.backup_check.isChecked() else "（未勾选备份）")
                + "\n\n继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if ret != QMessageBox.StandardButton.Yes:
                return

        self.manual_card.hide()
        self._set_busy(True)
        self.progress.setRange(0, len(self._files))
        self.progress.setValue(0)
        self.replace_thread = ReplaceThread(
            self._files, rules, self.backup_check.isChecked()
        )
        self.replace_thread.stage.connect(self.status.setText)
        self.replace_thread.file_index.connect(
            lambda i, t, n: (
                self.progress.setValue(i),
                self.status.setText(f"[{i + 1}/{t}] {n}"),
            )
        )
        self.replace_thread.need_manual.connect(self._on_need_manual)
        self.replace_thread.file_done.connect(self._on_file_done)
        self.replace_thread.finished_ok.connect(self._on_replace_done)
        self.replace_thread.failed.connect(self._on_failed)
        self.replace_thread.start()

    def _on_need_manual(self, jsx: str, log: str):
        self._manual_jsx = jsx
        self.manual_path.setText(jsx)
        self.manual_card.show()
        self.status.setText("等待你在 Photoshop 里运行脚本…（工作台正在监听结果）")

    def _copy_manual_path(self):
        path = getattr(self, "_manual_jsx", "")
        if path:
            QApplication.clipboard().setText(path)
            self.status.setText("脚本路径已复制")

    def _open_script_dir(self):
        try:
            os.startfile(str(script_dir()))  # type: ignore[attr-defined]
        except Exception:
            subprocess.Popen(["explorer.exe", str(script_dir())])

    def _on_file_done(self, result):
        if result.error:
            self.status.setText(f"✕ {Path(result.source).name}：{result.error}")
        else:
            bits = [f"{Path(result.source).name}：替换 {result.changed} 处"]
            if result.still_old:
                bits.append(f"残留旧文案 {result.still_old}")
            if result.verify_ok is False:
                bits.append(f"校验未通过（{result.verify_note}）")
            elif result.verify_ok:
                bits.append("校验通过")
            self.status.setText(" · ".join(bits))

    def _on_replace_done(self, results: list):
        self.replace_thread = None
        self._set_busy(False)
        self.manual_card.hide()
        self.progress.setValue(self.progress.maximum())

        ok = [r for r in results if r.error is None and r.changed > 0]
        changed = sum(r.changed for r in results)

        lines = [f"完成：{len(ok)}/{len(results)} 个文件，共替换 {changed} 处"]
        if any(r.verify_ok for r in results):
            lines.append("独立校验通过")
        self.status.setText(" · ".join(lines))

        detail = []
        for r in results:
            detail.append(f"■ {Path(r.source).name}")
            detail.append(f"    替换 {r.changed} 处 · 文字图层 {r.text_total} 个")
            if r.backup:
                detail.append(f"    备份：{Path(r.backup).name}")
            if r.verify_ok is not None:
                detail.append(f"    校验：{'通过' if r.verify_ok else '未通过'} —— {r.verify_note}")
            if r.errors:
                detail.append(f"    图层内错误 {len(r.errors)} 条")
            if r.error:
                detail.append(f"    错误：{r.error}")
        QMessageBox.information(self, "替换结果", "\n".join(lines + [""] + detail))
        if changed:
            self.exported.emit()

    def _set_busy(self, busy: bool):
        self.btn_scan.setEnabled(not busy and bool(self._files))
        self.btn_run.setEnabled(not busy and bool(self._files) and self.rule_table.rowCount() > 0)
        self.btn_cancel.setEnabled(busy)
        self.drop_zone.setEnabled(not busy)
        self.progress.setVisible(busy)
        self.add_rule_btn.setEnabled(not busy and bool(self.scan_table.selectedItems()))

    def _cancel(self):
        if self.replace_thread is not None:
            self.replace_thread.cancel()
            self.status.setText("已停止等待（Photoshop 里正在跑的脚本不会中断）")
            self.btn_cancel.setEnabled(False)

    def _on_failed(self, message: str):
        self.scan_thread = None
        self.replace_thread = None
        self._set_busy(False)
        self.progress.setValue(0)
        self.status.setText(f"出错了：{message}")
        QMessageBox.warning(self, "出错了", message)

    def refresh_theme(self):
        self.scan_empty.refresh_theme()
        self.rule_empty.refresh_theme()
        self.drop_zone.refresh_theme()
        self.btn_scan.setIcon(painted_icon("search", S._TEXT_SECONDARY, 16))
        self._add_more_btn.setIcon(painted_icon("plus", S._ACCENT, 13))
        muted_brush = QBrush(to_qcolor(S._TEXT_TERTIARY))
        for r in range(self.scan_table.rowCount()):
            cnt = self.scan_table.item(r, 1)
            if cnt is not None:
                cnt.setForeground(muted_brush)
