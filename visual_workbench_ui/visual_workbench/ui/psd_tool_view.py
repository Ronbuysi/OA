# -*- coding: utf-8 -*-
"""PSD 工具页：把 PSD / PSB 的每个最外层（一级）分组各导出一张图。

优化亮点：
- 修复已选文件条「清空」按钮损坏的 QSS 语法，新增「+ 继续添加」按钮与全页拖拽追加支持；
- 接入语义化 QSS 类名（`CardTitle`、`DropZoneTitle`、`HintLabel`、`BorderlessTable`），深浅色切换零残留；
- 表格状态列文字采用主题感知的 `_SUCCESS` / `_DANGER` 配色。
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QThread, Signal
from PySide6.QtGui import QBrush
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton,
    QSpinBox, QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

from ..psd_export import DEFAULT_SUBFOLDER, ExportOptions, analyze, export_psd_groups
from .icons import icon as painted_icon, to_qcolor
from .layout import page_body
from . import styles as S

PSD_SUFFIXES = (".psd", ".psb")
_OPEN_FILTER = "Photoshop 源文件 (*.psd *.psb)"


def _hint_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("HintLabel")
    lbl.setWordWrap(True)
    return lbl


class AnalyzeThread(QThread):
    """只读预演：报出每个文件会导出哪几个分组。"""

    progress = Signal(int, int, str)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, paths: list[str], keep_public: bool):
        super().__init__()
        self.paths = list(paths)
        self.keep_public = keep_public
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            out = []
            total = len(self.paths)
            for i, path in enumerate(self.paths):
                if self._cancel:
                    break
                name = Path(path).name
                self.progress.emit(i, total, f"正在分析：{name}")
                try:
                    info = analyze(path, keep_public=self.keep_public)
                    info["source"] = path
                except Exception as exc:
                    info = {
                        "source": path, "groups": [], "foreground": [],
                        "background": [], "loose_layers": [], "error": str(exc),
                    }
                out.append(info)
            self.finished_ok.emit(out)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class ExportThread(QThread):
    """执行导出，逐文件回传结果。"""

    progress = Signal(int, int, str)
    file_done = Signal(object)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, paths: list[str], fixed_out_dir: Path | None,
                 fmt: str, quality: int, keep_public: bool):
        super().__init__()
        self.paths = list(paths)
        self.fixed_out_dir = fixed_out_dir
        self.fmt = fmt
        self.quality = quality
        self.keep_public = keep_public
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
                self.progress.emit(i, total, f"正在导出：{name}")

                def _on_group(done, group_total, group_name, _i=i, _n=name):
                    self.progress.emit(
                        _i, total,
                        f"{_n} · 第 {done}/{group_total} 个分组：{group_name}",
                    )

                result = export_psd_groups(
                    path,
                    ExportOptions(
                        out_dir=self.fixed_out_dir,
                        fmt=self.fmt,
                        quality=self.quality,
                        keep_public=self.keep_public,
                    ),
                    on_progress=_on_group,
                    should_cancel=lambda: self._cancel,
                )
                results.append(result)
                self.file_done.emit(result)
            self.finished_ok.emit(results)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class _PsdDropZone(QFrame):
    """大拖拽区：点击选择 / 拖拽放入，只看 .psd / .psb。"""

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

        main = QLabel("把 PSD / PSB 拖到这里，或点击选择文件")
        main.setObjectName("DropZoneTitle")
        main.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(main)

        sub = _hint_label("支持多选 · 每个文件最外层的分组会各导出一张图")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(sub)
        self.refresh_theme()

    def refresh_theme(self):
        self.icon_label.setPixmap(painted_icon("layers", S._TEXT_TERTIARY, 28).pixmap(28, 28))
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
            self, "选择 PSD / PSB 文件", "", _OPEN_FILTER
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
            if p.lower().endswith(PSD_SUFFIXES) and os.path.exists(p):
                paths.append(p)
        if paths:
            self.filesAdded.emit(paths)


class PsdToolPage(QWidget):
    """PSD 一级分组批量导出 —— 工作台内嵌页面。"""

    exported = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.analyze_thread: AnalyzeThread | None = None
        self.export_thread: ExportThread | None = None
        self._files: list[str] = []
        self._out_dirs: list[str] = []
        self.setAcceptDrops(True)  # 允许已选文件后继续拖入追加
        self._build_ui()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [
            url.toLocalFile() for url in event.mimeData().urls()
            if url.toLocalFile().lower().endswith(PSD_SUFFIXES)
            and os.path.exists(url.toLocalFile())
        ]
        if paths:
            self.add_files(paths)

    def _build_ui(self):
        root = page_body(self, spacing=14, margins=(32, 28, 32, 20))

        title_row = QHBoxLayout()
        title_row.setSpacing(14)
        title = QLabel("PSD 工具")
        title.setObjectName("PageTitle")
        title_tag = QLabel("PSD EXPORT")
        title_tag.setObjectName("TitleTag")
        title_row.addWidget(title)
        title_row.addWidget(title_tag)
        title_row.addStretch()
        root.addLayout(title_row)

        root.addWidget(_hint_label(
            "把每个最外层分组各导出一张图。图层面板最顶部/最底部连续锁定的"
            "图层或分组会作为公共前景/背景合成进每张图；顶级散层不单独导出；"
            "原 PSD 不会被修改。"
        ))

        self.drop_zone = _PsdDropZone()
        self.drop_zone.filesAdded.connect(self.add_files)
        root.addWidget(self.drop_zone)

        # 已选文件条（含「+ 继续添加」与「清空」）
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

        # ── 主体：左设置 + 右结果表 ──
        mid = QHBoxLayout()
        mid.setSpacing(14)

        settings = QFrame()
        settings.setObjectName("GlassCard")
        settings.setFixedWidth(256)
        sv = QVBoxLayout(settings)
        sv.setContentsMargins(18, 16, 18, 14)
        sv.setSpacing(10)

        head = QLabel("导出设置")
        head.setObjectName("CardTitle")
        sv.addWidget(head)

        fmt_row = QHBoxLayout()
        fmt_row.setSpacing(8)
        fmt_row.addWidget(QLabel("格式"))
        self.fmt_combo = QComboBox()
        self.fmt_combo.addItem("PNG（透明底）", "png")
        self.fmt_combo.addItem("JPG（白底）", "jpg")
        self.fmt_combo.currentIndexChanged.connect(self._on_format_changed)
        fmt_row.addWidget(self.fmt_combo, 1)
        sv.addLayout(fmt_row)

        q_row = QHBoxLayout()
        q_row.setSpacing(8)
        q_row.addWidget(QLabel("质量"))
        self.quality_spin = QSpinBox()
        self.quality_spin.setRange(50, 100)
        self.quality_spin.setValue(92)
        self.quality_spin.setEnabled(False)
        self.quality_spin.setToolTip("仅 JPG 生效")
        q_row.addWidget(self.quality_spin, 1)
        sv.addLayout(q_row)

        sv.addSpacing(4)

        self.same_dir_check = QCheckBox("输出到 PSD 同目录")
        self.same_dir_check.setChecked(True)
        self.same_dir_check.setToolTip(
            f"勾选时：每个 PSD 旁边建一个「{DEFAULT_SUBFOLDER}」文件夹存放结果\n"
            "取消勾选：所有分组图统一输出到下面指定的目录"
        )
        self.same_dir_check.toggled.connect(self._on_same_dir_toggled)
        sv.addWidget(self.same_dir_check)

        self.out_edit = QLineEdit()
        self.out_edit.setPlaceholderText("统一输出目录")
        self.out_edit.setEnabled(False)
        sv.addWidget(self.out_edit)

        self.browse_button = QPushButton("选择目录…")
        self.browse_button.setEnabled(False)
        self.browse_button.clicked.connect(self._choose_dir)
        sv.addWidget(self.browse_button)

        sv.addSpacing(4)

        self.public_check = QCheckBox("锁定层作公共层")
        self.public_check.setChecked(True)
        self.public_check.setToolTip(
            "图层面板最顶/最底连续锁定的图层或分组会合成进每张图，且不单独导出"
        )
        sv.addWidget(self.public_check)
        sv.addStretch()

        self.hint = _hint_label("")
        sv.addWidget(self.hint)
        mid.addWidget(settings)

        # 结果表
        result_card = QFrame()
        result_card.setObjectName("GlassCard")
        pv = QVBoxLayout(result_card)
        pv.setContentsMargins(14, 12, 14, 12)
        pv.setSpacing(8)

        self.result_stack = QStackedWidget()
        empty = QFrame()
        empty_lay = QVBoxLayout(empty)
        empty_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_lay.setSpacing(12)
        icon_frame = QFrame()
        icon_frame.setObjectName("EmptyStateVisual")
        icon_frame.setFixedSize(64, 64)
        icon_lay = QHBoxLayout(icon_frame)
        icon_lay.setContentsMargins(0, 0, 0, 0)
        self._empty_icon = QLabel()
        self._empty_icon.setPixmap(painted_icon("layers", S._ACCENT, 28).pixmap(28, 28))
        self._empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_icon.setStyleSheet("background:transparent;border:0;")
        icon_lay.addWidget(self._empty_icon)
        empty_lay.addWidget(icon_frame, alignment=Qt.AlignmentFlag.AlignHCenter)
        empty_title = QLabel("预览即将导出的分组")
        empty_title.setObjectName("EmptyStateTitle")
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_lay.addWidget(empty_title)
        self.empty_label = QLabel("添加 PSD / PSB 文件后点击“预览分组”，先核对分组和公共层，再导出图片。")
        self.empty_label.setObjectName("EmptyStateDetail")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setWordWrap(True)
        self.empty_label.setMaximumWidth(350)
        empty_lay.addWidget(self.empty_label)

        self.table = QTableWidget(0, 4)
        self.table.setObjectName("BorderlessTable")
        self.table.setHorizontalHeaderLabels(["源文件", "分组", "状态", "输出"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
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

        # ── 底部操作栏 ──
        action_bar = QFrame()
        action_bar.setObjectName("ActionBar")
        ab = QHBoxLayout(action_bar)
        ab.setContentsMargins(18, 12, 18, 12)
        ab.setSpacing(12)
        self.status_label = QLabel("准备就绪 —— 拖入 PSD 开始")
        self.status_label.setObjectName("BodyLabel")
        ab.addWidget(self.status_label, 1)
        self.progress = QProgressBar()
        self.progress.setMaximum(1)
        self.progress.setValue(0)
        self.progress.setMaximumHeight(6)
        self.progress.setTextVisible(False)
        ab.addWidget(self.progress, 1)
        self.progress.hide()

        self.btn_preview = QPushButton("  预览分组")
        self.btn_preview.setIcon(painted_icon("search", S._TEXT_SECONDARY, 16))
        self.btn_preview.setIconSize(QSize(16, 16))
        self.btn_preview.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_preview.setMinimumHeight(40)
        self.btn_preview.setEnabled(False)
        self.btn_preview.clicked.connect(self._start_analyze)
        ab.addWidget(self.btn_preview)

        self.btn_export = QPushButton("  开始导出")
        self.btn_export.setIcon(painted_icon("layers", "#FFFFFF", 16))
        self.btn_export.setIconSize(QSize(16, 16))
        self.btn_export.setObjectName("PrimaryButton")
        self.btn_export.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_export.setMinimumHeight(40)
        self.btn_export.setEnabled(False)
        self.btn_export.clicked.connect(self._start_export)
        ab.addWidget(self.btn_export)

        self.btn_open = QPushButton("打开输出文件夹")
        self.btn_open.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_open.setMinimumHeight(40)
        self.btn_open.setEnabled(False)
        self.btn_open.clicked.connect(self._open_output)
        ab.addWidget(self.btn_open)

        root.addWidget(action_bar)

    def _pick_more_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择要追加的 PSD / PSB 文件", "", _OPEN_FILTER
        )
        if files:
            self.add_files(files)

    def _on_format_changed(self):
        self.quality_spin.setEnabled(self.fmt_combo.currentData() == "jpg")

    def _on_same_dir_toggled(self, checked: bool):
        self.out_edit.setEnabled(not checked)
        self.browse_button.setEnabled(not checked)
        if self._files:
            self._sync_files_bar()

    def _choose_dir(self):
        chosen = QFileDialog.getExistingDirectory(self, "选择统一输出目录")
        if chosen:
            self.out_edit.setText(chosen)
            if self._files:
                self._sync_files_bar()

    def add_files(self, paths: list[str]) -> None:
        if isinstance(paths, str):
            paths = [paths]
        existing = set(self._files)
        added = 0
        for path in paths:
            normalized = os.path.normpath(str(path))
            if not normalized.lower().endswith(PSD_SUFFIXES):
                continue
            if normalized in existing:
                continue
            self._files.append(normalized)
            existing.add(normalized)
            added += 1

        if added:
            self._sync_files_bar()
            self.table.setRowCount(0)
            self.result_stack.setCurrentIndex(0)
            self.result_summary.hide()
            self._set_status(
                f"已选 {len(self._files)} 个文件（新增 {added} 个），点「预览分组」看看会导出哪些分组"
            )

    def _sync_files_bar(self):
        n = len(self._files)
        if hasattr(self, "btn_preview"):
            self.btn_preview.setEnabled(n > 0)
            self.btn_export.setEnabled(n > 0)
        if n == 0:
            self.files_bar.hide()
            self.drop_zone.show()
            self.hint.setText("")
            return
        self.drop_zone.hide()
        self.files_bar.show()
        if n == 1:
            self.files_label.setText(os.path.basename(self._files[0]))
        else:
            head = "、".join(os.path.basename(f) for f in self._files[:3])
            more = f" 等 {n} 个文件" if n > 3 else ""
            self.files_label.setText(f"{head}{more}")
        self.files_label.setToolTip("\n".join(self._files))
        if self.same_dir_check.isChecked():
            dest = f"同目录「{DEFAULT_SUBFOLDER}」"
        else:
            dest = self.out_edit.text().strip() or "统一目录（未指定）"
        self.hint.setText(f"已选 {n} 个文件 · 输出到 {dest}")

    def _clear_files(self):
        self._files.clear()
        self._out_dirs.clear()
        self._sync_files_bar()
        self.table.setRowCount(0)
        self.result_stack.setCurrentIndex(0)
        self.result_summary.hide()
        self.btn_open.setEnabled(False)
        self._set_status("准备就绪 —— 拖入 PSD 开始")

    def _set_status(self, text: str):
        self.status_label.setText(text)

    def _set_busy(self, busy: bool, text: str = ""):
        self.btn_preview.setEnabled(not busy and bool(self._files))
        self.btn_export.setEnabled(not busy and bool(self._files))
        if busy:
            self.btn_export.setText("  处理中…")
        else:
            self.btn_export.setText("  开始导出")
        self.drop_zone.setEnabled(not busy)
        self.progress.setVisible(busy)
        if text:
            self._set_status(text)

    def _fixed_out_dir(self) -> Path | None:
        if self.same_dir_check.isChecked():
            return None
        text = self.out_edit.text().strip()
        return Path(text) if text else None

    def _collect_options(self) -> tuple[str, int, bool] | None:
        if not self.same_dir_check.isChecked() and not self.out_edit.text().strip():
            QMessageBox.information(
                self, "缺少输出目录",
                "已取消「输出到 PSD 同目录」，请先选择一个统一输出目录。",
            )
            return None
        return (
            self.fmt_combo.currentData() or "png",
            self.quality_spin.value(),
            self.public_check.isChecked(),
        )

    def _start_analyze(self):
        if not self._files:
            QMessageBox.information(self, "提示", "请先拖入 PSD / PSB 文件")
            return
        self.table.setRowCount(0)
        self.result_stack.setCurrentIndex(0)
        self._set_busy(True, "分析中…")
        self.progress.setMaximum(len(self._files))
        self.progress.setValue(0)

        self.analyze_thread = AnalyzeThread(
            self._files, self.public_check.isChecked()
        )
        self.analyze_thread.progress.connect(self._on_analyze_progress)
        self.analyze_thread.finished_ok.connect(self._on_analyze_done)
        self.analyze_thread.failed.connect(self._on_thread_failed)
        self.analyze_thread.start()

    def _on_analyze_progress(self, done: int, total: int, text: str):
        self.progress.setValue(done)
        self._set_status(f"分析中… {text}")

    def _on_analyze_done(self, items: list):
        self.analyze_thread = None
        self._set_busy(False)
        self.progress.setValue(self.progress.maximum())
        rows = 0
        empty_files = 0
        for info in items:
            source = os.path.basename(info.get("source", ""))
            if info.get("error"):
                self._append_row(source, "—", "打不开", info["error"])
                rows += 1
                empty_files += 1
                continue
            groups = info.get("groups") or []
            if not groups:
                self._append_row(source, "—", "无分组", "顶层没有可导出的一级分组")
                rows += 1
                empty_files += 1
                continue
            public = (info.get("foreground") or []) + (info.get("background") or [])
            for g in groups:
                self._append_row(source, g, "待导出", "")
                rows += 1
            if public:
                self._append_row(source, f"公共层：{'、'.join(public)}", "不单独导出", "")
        if rows:
            self.result_stack.setCurrentIndex(1)
        self._set_status(
            f"预览完成：{len(items)} 个文件、{rows} 行结果"
            + (f"，{empty_files} 个文件无分组" if empty_files else "")
        )
        self.result_summary.setText(
            f"共 {len(self._files)} 个文件 · 识别到 {rows} 项 · 点「开始导出」执行"
        )
        self.result_summary.show()

    def _start_export(self):
        if not self._files:
            QMessageBox.information(self, "提示", "请先拖入 PSD / PSB 文件")
            return
        options = self._collect_options()
        if options is None:
            return
        fmt, quality, keep_public = options

        self.table.setRowCount(0)
        self.result_stack.setCurrentIndex(0)
        self.result_summary.hide()
        self._out_dirs = []
        self.btn_open.setEnabled(False)
        self._set_busy(True, "导出中…")
        self.progress.setMaximum(len(self._files))
        self.progress.setValue(0)

        self.export_thread = ExportThread(
            self._files, self._fixed_out_dir(), fmt, quality, keep_public
        )
        self.export_thread.progress.connect(self._on_export_progress)
        self.export_thread.file_done.connect(self._on_file_done)
        self.export_thread.finished_ok.connect(self._on_export_done)
        self.export_thread.failed.connect(self._on_thread_failed)
        self.export_thread.start()

    def _on_export_progress(self, done: int, total: int, text: str):
        self.progress.setValue(done)
        self._set_status(f"导出中… {text}")

    def _on_file_done(self, result):
        if result.out_dir:
            self._out_dirs.append(result.out_dir)
        source = os.path.basename(result.source)
        if result.error and result.groups_ok == 0:
            self._append_row(source, "—", "失败", result.error)
            return
        public = result.public_foreground + result.public_background
        if public:
            self._append_row(source, f"公共层：{'、'.join(public)}", "不单独导出", "")
        for item in result.items:
            if item.ok:
                status = "已导出（强制显示）" if item.forced else "已导出"
                self._append_row(source, item.group, status, item.output or "")
            elif item.empty:
                self._append_row(source, item.group, "空分组", "组内无可显示内容，已跳过")
            else:
                self._append_row(source, item.group, "失败", item.message)

    def _on_export_done(self, results: list):
        self.export_thread = None
        self._set_busy(False)
        self.progress.setValue(self.progress.maximum())
        ok_files = sum(1 for r in results if r.groups_ok)
        total_groups = sum(r.groups_ok for r in results)
        empties = sum(r.groups_empty for r in results)
        fails = sum(r.failed for r in results)
        forced = sum(r.forced for r in results)

        parts = [f"完成：{ok_files}/{len(results)} 个文件，共导出 {total_groups} 张图"]
        if forced:
            parts.append(f"{forced} 张走了强制显示")
        if empties:
            parts.append(f"跳过 {empties} 个空分组")
        if fails:
            parts.append(f"{fails} 个失败")
        summary = " · ".join(parts)
        self._set_status(summary)
        self.result_summary.setText(summary + "　（已在素材库排队增量重扫）")
        self.result_summary.show()
        self.btn_open.setEnabled(bool(self._out_dirs))
        if total_groups:
            self.exported.emit()

    def _on_thread_failed(self, message: str):
        self.analyze_thread = None
        self.export_thread = None
        self._set_busy(False)
        self.progress.setValue(0)
        self._set_status(f"出错了：{message}")
        QMessageBox.warning(self, "出错了", message)

    def _append_row(self, source: str, group: str, status: str, output: str):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(source))
        self.table.setItem(row, 1, QTableWidgetItem(group))
        status_item = QTableWidgetItem(status)
        if status in ("失败", "打不开"):
            status_item.setForeground(QBrush(to_qcolor(S._DANGER)))
        elif status.startswith("已导出"):
            status_item.setForeground(QBrush(to_qcolor(S._SUCCESS)))
        self.table.setItem(row, 2, status_item)
        self.table.setItem(row, 3, QTableWidgetItem(output))
        if self.result_stack.currentIndex() != 1:
            self.result_stack.setCurrentIndex(1)

    def _open_output(self):
        if not self._out_dirs:
            return
        targets = []
        for d in self._out_dirs:
            if d and d not in targets:
                targets.append(d)
        try:
            if len(targets) == 1:
                os.startfile(targets[0])  # type: ignore[attr-defined]
            else:
                parents = sorted({os.path.dirname(d) for d in targets})
                os.startfile(parents[0] if len(parents) == 1 else targets[0])  # type: ignore[attr-defined]
        except Exception:
            pass

    def refresh_theme(self):
        """主题切换时刷新拖拽区、图标及结果表状态色。"""
        self.drop_zone.refresh_theme()
        self._empty_icon.setPixmap(painted_icon("layers", S._ACCENT, 28).pixmap(28, 28))
        self.btn_preview.setIcon(painted_icon("search", S._TEXT_SECONDARY, 16))
        self._add_more_btn.setIcon(painted_icon("plus", S._ACCENT, 13))
        for row in range(self.table.rowCount()):
            status_item = self.table.item(row, 2)
            if status_item is None:
                continue
            txt = status_item.text()
            if txt in ("失败", "打不开"):
                status_item.setForeground(QBrush(to_qcolor(S._DANGER)))
            elif txt.startswith("已导出"):
                status_item.setForeground(QBrush(to_qcolor(S._SUCCESS)))
