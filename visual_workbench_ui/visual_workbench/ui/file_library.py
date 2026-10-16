from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (
    QEvent, QMimeData, QObject, QRunnable, QSize, Qt, QThread, QThreadPool, QTimer, QUrl, Signal, Slot,
)
from PySide6.QtGui import QCursor, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QDialog, QFileDialog, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMenu, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from ..config import user_known_folders
from ..models import FileRecord
from ..preview import build_preview, open_file, reveal_file
from ..repository import Repository
from ..scanner import scan_root
from .empty_state import EmptyStatePanel
from .icons import icon as painted_icon, to_qcolor
from .layout import page_body
from .styles import (
    _ACCENT, _ACCENT_LIGHT, _BORDER, _BORDER_LIGHT, _CARD_BG,
    _TEXT_PRIMARY, _TEXT_SECONDARY, _TEXT_TERTIARY, duplicate_badge_colors,
)

# Custom role for absolute path (used by drag)
_PATH_ROLE = Qt.ItemDataRole.UserRole + 1

# File type badge —— 低饱和浅底 + 深色高对比文字，色值从主题 token 取
LABELS = {
    "psd":       ("PSD",    "_BADGE_PSD_BG",       "_BADGE_PSD_FG"),
    "source":    ("源文件", "_BADGE_PSD_BG",       "_BADGE_PSD_FG"),
    "image":     ("图片",   "_BADGE_IMAGE_BG",     "_BADGE_IMAGE_FG"),
    "workorder": ("工单",   "_BADGE_WORKORDER_BG", "_BADGE_WORKORDER_FG"),
    "pdf":       ("PDF",    "_BADGE_PDF_BG",       "_BADGE_PDF_FG"),
    "other":     ("其他",   "_BADGE_OTHER_BG",     "_BADGE_OTHER_FG"),
}


def _badge_colors(category: str) -> tuple[str, str]:
    """返回（前景色, 背景色）。每次渲染时现取，切主题后自动跟着变。"""
    from . import styles
    _text, bg_name, fg_name = LABELS.get(category, LABELS["other"])
    return getattr(styles, fg_name), getattr(styles, bg_name)


# 文件库默认浏览整个 G 盘
DEFAULT_ROOT = "G:/"

# Preview pixmap cache
_PIXMAP_CACHE: dict[str, QPixmap] = {}
_PIXMAP_CACHE_MAX = 500


def _load_pixmap(path: str) -> QPixmap | None:
    cached = _PIXMAP_CACHE.get(path)
    if cached is not None:
        return cached
    if len(_PIXMAP_CACHE) >= _PIXMAP_CACHE_MAX:
        _PIXMAP_CACHE.clear()
    if Path(path).exists():
        pixmap = QPixmap(path)
        if not pixmap.isNull():
            _PIXMAP_CACHE[path] = pixmap
            return pixmap
    return None


def _blocked_root_name(path: Path) -> str | None:
    resolved = path.expanduser().resolve()
    if resolved.parent == resolved:
        return "磁盘根目录"
    home = Path.home().resolve()
    known = user_known_folders()
    candidates = [
        (home, "用户主目录"),
        (home / "Desktop", "桌面"),
        (home / "Documents", "文档"),
        (home / "Downloads", "下载"),
        (home / "Pictures", "图片"),
        (known.get("Desktop"), "桌面"),
        (known.get("Documents"), "文档"),
        (known.get("Downloads"), "下载"),
        (known.get("Pictures"), "图片"),
    ]
    for candidate, name in candidates:
        if candidate is not None:
            try:
                if resolved == candidate.resolve():
                    return name
            except OSError:
                continue
    return None


class WorkerSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int)


def _emit_signal(signal, payload) -> None:
    """安全 emit：程序关闭时后台线程可能还在跑，吞掉已销毁对象的 RuntimeError。"""
    try:
        signal.emit(payload)
    except RuntimeError:
        pass


class ScanWorker(QRunnable):
    def __init__(self, root: str, repository: Repository):
        super().__init__()
        self.root = root
        self.repository = repository
        self.signals = WorkerSignals()

    @Slot()
    def run(self):
        QThread.currentThread().setPriority(QThread.Priority.LowPriority)
        try:
            ids = scan_root(self.root, self.repository, self.signals.progress.emit)
            _emit_signal(self.signals.finished, ids)
        except Exception as exc:
            _emit_signal(self.signals.failed, str(exc))


class PreviewWorker(QRunnable):
    def __init__(self, file: FileRecord, repository: Repository, epoch: int):
        super().__init__()
        self.file = file
        self.repository = repository
        self.epoch = epoch
        self.signals = WorkerSignals()

    @Slot()
    def run(self):
        try:
            result = build_preview(self.file.absolute_path, self.file.category)
            if self.file.id is not None:
                self.repository.update_preview(self.file.id, result.path, result.status)
            _emit_signal(self.signals.finished, (self.file.id, result, self.epoch))
        except Exception as exc:
            _emit_signal(self.signals.failed, str(exc))


class _SkeletonPlaceholder(QWidget):
    """Static skeleton loading placeholder — no animation for performance."""

    def __init__(self, parent=None):
        super().__init__(parent)

    def paintEvent(self, event):
        from .styles import _SKELETON_BG
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(to_qcolor(_SKELETON_BG))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(0, 0, self.width(), self.height(), 12, 12)
        painter.end()


class _ElidedFileLabel(QLabel):
    """超长无空格文件名自动尾部省略（...），避免撑破卡片或硬裁切。"""

    def __init__(self, text: str = "", font_px: int = 14, parent=None):
        super().__init__(text, parent)
        self._full_text = text
        self._font_px = font_px
        font = self.font()
        font.setPixelSize(font_px)
        font.setWeight(font.Weight.DemiBold)
        self.setFont(font)
        self.setFixedHeight(20)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def setText(self, text: str):  # noqa: N802
        self._full_text = text
        super().setText(text)
        self.update()

    def paintEvent(self, event):
        from .styles import _TEXT_PRIMARY
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setFont(self.font())
        painter.setPen(QPen(to_qcolor(_TEXT_PRIMARY)))
        elided = painter.fontMetrics().elidedText(
            self._full_text, Qt.TextElideMode.ElideRight, max(20, self.width())
        )
        painter.drawText(
            self.rect(),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            elided,
        )
        painter.end()


class FileCard(QFrame):
    """Modern minimal file card — supports elided filenames and theme-aware badges."""

    def __init__(self, file: FileRecord, large: bool = False, dup_count: int = 0, parent=None):
        super().__init__(parent)
        self.file = file
        self.large = large
        self.dup_count = dup_count
        self.setObjectName("Card")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._name_label: _ElidedFileLabel | None = None
        self._time_label: QLabel | None = None
        self._badge_label: QLabel | None = None
        self._dup_badge: QLabel | None = None

        from .styles import _SURFACE_2, _TEXT_TERTIARY

        if large:
            self.setMinimumSize(280, 296)
            self.setMaximumWidth(460)
            layout = QVBoxLayout(self)
            layout.setContentsMargins(12, 12, 12, 12)
            layout.setSpacing(8)
            thumb_h = 188
            font_px = 15
        else:
            min_h = 246 if dup_count > 1 else 216
            self.setMinimumSize(210, min_h)
            self.setMaximumWidth(320)
            layout = QVBoxLayout(self)
            layout.setContentsMargins(12, 12, 12, 12)
            layout.setSpacing(7)
            thumb_h = 118
            font_px = 14

        # Thumbnail
        self.preview = QLabel()
        self.preview.setFixedHeight(thumb_h)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setStyleSheet(f"background: {_SURFACE_2}; border-radius: 12px; border: 0;")
        layout.addWidget(self.preview)

        # Badges row（把分类胶囊与重复胶囊并排放一行，既省纵向空间又不会把文件名挤出卡片底部）
        badge_row = QHBoxLayout()
        badge_row.setContentsMargins(0, 0, 0, 0)
        badge_row.setSpacing(6)

        badge_text = LABELS.get(file.category, LABELS["other"])[0]
        fg, bg = _badge_colors(file.category)
        badge = QLabel(badge_text)
        badge.setStyleSheet(
            f"color: {fg}; background: {bg}; border-radius: 6px; "
            "padding: 2px 9px; font-size: 11px; font-weight: 600; border: 0;"
        )
        self._badge_label = badge
        badge_row.addWidget(badge)

        if dup_count > 1:
            dup_bg, dup_fg = duplicate_badge_colors()
            dup_badge = QLabel(f"重复 ×{dup_count}")
            dup_badge.setStyleSheet(
                f"color: {dup_fg}; background: {dup_bg}; border-radius: 999px;"
                "padding: 2px 10px; font-size: 11px; font-weight: 600; border: 0;"
            )
            dup_badge.setToolTip("内容相同的副本数量（同组留一份即可）")
            self._dup_badge = dup_badge
            badge_row.addWidget(dup_badge)

        badge_row.addStretch()
        layout.addLayout(badge_row)

        # File name (elided)
        name = _ElidedFileLabel(file.file_name, font_px=font_px)
        name.setToolTip(file.absolute_path)
        self._name_label = name
        layout.addWidget(name)

        # 更新时间
        try:
            mt = datetime.fromtimestamp(file.modified_at)
            time_text = f"{mt.year % 100}年{mt.month}月更新"
        except (OSError, ValueError, OverflowError):
            time_text = ""
        time_label = QLabel(time_text)
        time_label.setObjectName("Caption")
        time_label.setToolTip(f"{file.relative_path}\n{time_text}")
        time_label.setStyleSheet(
            f"color: {_TEXT_TERTIARY}; font-size: 11px; font-weight: 400; background: transparent; border: 0;"
        )
        self._time_label = time_label
        time_label.setMaximumHeight(16)
        layout.addWidget(time_label)

        if file.preview_path and Path(file.preview_path).exists():
            self.set_preview(file.preview_path)
        else:
            self._show_placeholder()

    def _show_placeholder(self):
        from .styles import _SURFACE_2, _TEXT_TERTIARY
        if self.file.preview_status == "pending":
            skeleton = _SkeletonPlaceholder()
            skeleton.setMinimumHeight(100)
            self.preview.hide()
            layout = self.layout()
            layout.insertWidget(0, skeleton)
            self._skeleton = skeleton
            return
        if self.file.category == "psd":
            text = "PSD"
        else:
            text = self.file.extension.upper().lstrip(".") or "FILE"
        self.preview.setText(text)
        self.preview.setStyleSheet(
            f"background: {_SURFACE_2}; border-radius: 12px; color: {_TEXT_TERTIARY};"
            " font-size: 17px; font-weight: 600; border: 0;"
        )

    def set_preview(self, preview_path: str | None):
        if hasattr(self, "_skeleton"):
            self._skeleton.hide()
            self._skeleton.deleteLater()
            del self._skeleton
            self.preview.show()

        pixmap = _load_pixmap(preview_path) if preview_path else None
        if pixmap is not None:
            w, h = (280, 188) if self.large else (196, 118)
            self.preview.setPixmap(
                pixmap.scaled(
                    w, h,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            self.preview.setStyleSheet("background: transparent; border-radius: 12px; border: 0;")
        else:
            self._show_placeholder()

    def update_theme(self):
        from .styles import _SURFACE_2, _TEXT_TERTIARY
        if self._name_label:
            self._name_label.update()
        if self._time_label:
            self._time_label.setStyleSheet(
                f"color: {_TEXT_TERTIARY}; font-size: 11px; font-weight: 400; background: transparent; border: 0;"
            )
        if self._badge_label:
            fg, bg = _badge_colors(self.file.category)
            self._badge_label.setStyleSheet(
                f"color: {fg}; background: {bg}; border-radius: 6px; "
                "padding: 2px 9px; font-size: 11px; font-weight: 600; border: 0;"
            )
        if self._dup_badge:
            dup_bg, dup_fg = duplicate_badge_colors()
            self._dup_badge.setStyleSheet(
                f"color: {dup_fg}; background: {dup_bg}; border-radius: 999px;"
                "padding: 2px 10px; font-size: 11px; font-weight: 600; border: 0;"
            )
        if hasattr(self, "preview") and self.preview and self.preview.pixmap().isNull():
            self.preview.setStyleSheet(f"background: {_SURFACE_2}; border-radius: 12px; border: 0;")


class DragFileList(QListWidget):
    def mimeData(self, items):
        mime = QMimeData()
        urls = []
        for item in items:
            path = item.data(_PATH_ROLE)
            if path:
                urls.append(QUrl.fromLocalFile(path))
        if urls:
            mime.setUrls(urls)
        return mime


_PREVIEW_STAGE_BG = "#101013"
_PREVIEW_STAGE_RADIUS = 12


class _QuickPreviewDialog(QDialog):
    """预览窗：空格或 Esc 关闭，手感对齐 QuickLook。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        from .styles import _BORDER, _CARD_BG
        self.setStyleSheet(
            f"QDialog {{ background: {_CARD_BG}; "
            f"border: 1px solid {_BORDER}; border-radius: 16px; }}"
        )

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Escape):
            self.accept()
            return
        super().keyPressEvent(event)


class FileLibraryPage(QWidget):
    scanCompleted = Signal()

    def __init__(self, repository: Repository, thread_pool, parent=None):
        super().__init__(parent)
        self.repository = repository
        self.thread_pool = thread_pool
        self._preview_pool = QThreadPool(self)
        self._preview_pool.setMaxThreadCount(4)
        self._dispatched_rows: set[int] = set()
        self.current_filter = "all"
        self.product_folder: str | None = None
        self.current_files: list[FileRecord] = []
        self._row_by_id: dict[int, int] = {}
        self._refresh_epoch = 0
        self._scan_running = False
        self._preview_dialog: QDialog | None = None
        self._dup_counts: dict[str, int] = {}
        self._dup_sizes: dict[str, int] = {}
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self.refresh_files)

        # 滚动进度气泡自动淡出计时器（滚动停止 1.2s 后自动隐藏，不遮挡右下角卡片）
        self._pos_hint_timer = QTimer(self)
        self._pos_hint_timer.setSingleShot(True)
        self._pos_hint_timer.setInterval(1200)
        self._pos_hint_timer.timeout.connect(self._hide_pos_hint)

        self._build_ui()
        self._load_root()
        self._search_shortcut = QShortcut(QKeySequence("Ctrl+K"), self)
        self._search_shortcut.activated.connect(self._focus_search)
        self._select_all_shortcut = QShortcut(QKeySequence("Ctrl+A"), self)
        self._select_all_shortcut.activated.connect(self._select_all)
        QApplication.instance().installEventFilter(self)

    def _hide_pos_hint(self):
        if hasattr(self, "pos_hint") and self.pos_hint is not None:
            self.pos_hint.hide()

    def _focus_search(self):
        self.search.setFocus()
        self.search.selectAll()

    def _refresh_path_icon(self):
        from .styles import _TEXT_TERTIARY
        self._path_icon.setPixmap(painted_icon("folder", _TEXT_TERTIARY, 14).pixmap(14, 14))

    def _select_all(self):
        if not self.isVisible():
            return
        if isinstance(QApplication.focusWidget(), QLineEdit):
            return
        self.file_list.selectAll()

    def _selected_paths(self) -> list[str]:
        return [
            path for path in (
                item.data(_PATH_ROLE) for item in self.file_list.selectedItems()
            ) if path
        ]

    def _on_selection_changed(self):
        count = len(self.file_list.selectedItems())
        if count > 1:
            self.status.setText(
                f"已选中 {count} 个素材 · 右键批量复制路径 · 可直接拖到聊天窗口发送（Ctrl+A 全选）"
            )

    def refresh_theme(self):
        """刷新主题相关的样式（保持与全局 QSS 对齐，不再用内联样式破坏搜索框和筛选按钮）。"""
        self.empty_state.refresh_theme()
        from .styles import _TEXT_TERTIARY
        if hasattr(self, "_search_icon_action"):
            self._search_icon_action.setIcon(painted_icon("search", _TEXT_TERTIARY, 16))
        if hasattr(self, "_path_icon"):
            self._refresh_path_icon()
        if hasattr(self, "search"):
            self.search.style().unpolish(self.search)
            self.search.style().polish(self.search)
        if hasattr(self, "filter_buttons"):
            for btn in self.filter_buttons.values():
                btn.style().unpolish(btn)
                btn.style().polish(btn)
        # 刷新已实例化的卡片样式，无需重新清空查库
        for row in range(self.file_list.count()):
            item = self.file_list.item(row)
            if item is not None:
                card = self.file_list.itemWidget(item)
                if isinstance(card, FileCard):
                    card.update_theme()

    def _preview_target(self) -> str | None:
        if self._preview_dialog is not None and self._preview_dialog.isVisible():
            return None
        path = getattr(self, "_last_clicked_path", None)
        if path and Path(path).exists():
            return path
        item = self.file_list.currentItem()
        if item is None:
            viewport = self.file_list.viewport()
            pos = viewport.mapFromGlobal(QCursor.pos())
            if viewport.rect().contains(pos):
                item = self.file_list.itemAt(pos)
        if item is not None:
            path = item.data(_PATH_ROLE) or item.data(Qt.ItemDataRole.UserRole)
            if isinstance(path, str) and Path(path).exists():
                return path
        return None

    def eventFilter(self, obj, event):
        try:
            if (
                event.type() == QEvent.Type.KeyPress
                and event.key() == Qt.Key.Key_Space
                and not (
                    event.modifiers() & (
                        Qt.KeyboardModifier.ControlModifier
                        | Qt.KeyboardModifier.AltModifier
                        | Qt.KeyboardModifier.MetaModifier
                    )
                )
                and self.isVisible()
                and not isinstance(obj, QLineEdit)
                and not isinstance(QApplication.focusWidget(), QLineEdit)
            ):
                path = self._preview_target()
                if path:
                    self._invoke_quicklook(path)
                    return True
        except Exception:
            pass
        return False

    def _invoke_quicklook(self, path: str):
        if self._try_quicklook(path):
            return
        self._fallback_preview(path)

    def _try_quicklook(self, path: str) -> bool:
        try:
            import ctypes
            import ctypes.wintypes as wintypes
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateFileW.restype = wintypes.HANDLE
            kernel32.CreateFileW.argtypes = [
                wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
            ]
            kernel32.WriteFile.argtypes = [
                wintypes.HANDLE, wintypes.LPCVOID, wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
            ]
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel32.CreateFileW(
                r"\\.\pipe\QuickLook.ClientApi",
                0x40000000,
                0, None,
                3,
                0, None,
            )
            if handle == wintypes.HANDLE(-1).value:
                return False
            data = path.encode("utf-16-le") + b"\x00\x00"
            written = wintypes.DWORD()
            kernel32.WriteFile(handle, data, len(data), ctypes.byref(written), None)
            kernel32.CloseHandle(handle)
            return True
        except Exception:
            return False

    @staticmethod
    def _load_pixmap(path: str | None, box: tuple[int, int], upscale: bool = True) -> QPixmap | None:
        if not path or not Path(path).exists():
            return None
        pixmap = QPixmap(path)
        if pixmap.isNull():
            return None
        if not upscale and pixmap.width() <= box[0] and pixmap.height() <= box[1]:
            return pixmap
        return pixmap.scaled(
            box[0], box[1],
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    def _preview_pixmap(self, file_record: FileRecord, box: tuple[int, int]) -> QPixmap | None:
        if file_record.category == "image" and file_record.extension.lower() != ".svg":
            pixmap = self._load_pixmap(file_record.absolute_path, box)
            if pixmap is not None:
                return pixmap
        return self._load_pixmap(file_record.preview_path, box, upscale=False)

    def _fallback_preview(self, path: str):
        file_record = next((f for f in self.current_files if f.absolute_path == path), None)
        if file_record is None:
            return

        dlg = _QuickPreviewDialog(self)
        dlg.setWindowTitle(file_record.file_name)
        dlg.setMinimumSize(640, 520)
        dlg.resize(900, 660)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(20, 20, 20, 16)
        lay.setSpacing(12)

        img_label = QLabel()
        img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        img_label.setMinimumHeight(420)
        img_label.setStyleSheet(
            f"background: {_PREVIEW_STAGE_BG};"
            f"border-radius: {_PREVIEW_STAGE_RADIUS}px;"
            "color: #6B6B78; font-size: 32px; font-weight: 500;"
        )
        pixmap = self._preview_pixmap(file_record, (860, 520))
        if pixmap is not None:
            img_label.setPixmap(pixmap)
        else:
            img_label.setText(file_record.extension.upper().lstrip(".") or "FILE")
        lay.addWidget(img_label, 1)

        info = QLabel(
            f"{file_record.file_name}  ·  {file_record.size_bytes / 1024 / 1024:.1f} MB"
            "  ·  空格 / Esc 关闭"
        )
        info.setObjectName("Caption")
        lay.addWidget(info)

        self._preview_dialog = dlg
        try:
            dlg.exec()
        finally:
            self._preview_dialog = None

    def _build_ui(self):
        root = page_body(self, spacing=0, margins=(32, 28, 32, 24))

        # ── Header: title + subtitle + action buttons ──
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        title_block = QVBoxLayout()
        title_block.setSpacing(6)

        title_row = QHBoxLayout()
        title_row.setSpacing(14)
        title = QLabel("资产库")
        title.setObjectName("PageTitle")
        title_row.addWidget(title)
        title_tag = QLabel("ASSET LIBRARY")
        title_tag.setObjectName("TitleTag")
        title_row.addWidget(title_tag)
        title_row.addStretch()
        title_block.addLayout(title_row)

        subtitle = QLabel("你的视觉资产空间 · 双击打开 · 空格预览 · Ctrl/Shift 多选 · 拖拽卡片即可发送文件")
        subtitle.setObjectName("PageSubtitle")
        title_block.addWidget(subtitle)

        header.addLayout(title_block)
        header.addStretch()

        self.change_product_button = QPushButton("更换产品库")
        self.change_product_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.change_product_button.setToolTip("手动指定产品库文件夹（在文件库内选择）")
        self.change_product_button.clicked.connect(self._choose_product_folder)
        header.addWidget(self.change_product_button)

        self.change_button = QPushButton("更改文件夹")
        self.change_button.setObjectName("PrimaryButton")
        self.change_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.change_button.clicked.connect(self.choose_root)
        header.addWidget(self.change_button)
        root.addLayout(header)

        root.addSpacing(18)

        # ── Location pill ──
        location_row = QHBoxLayout()
        location_row.setContentsMargins(0, 0, 0, 0)
        path_chip = QFrame()
        path_chip.setObjectName("PathChip")
        chip_layout = QHBoxLayout(path_chip)
        chip_layout.setContentsMargins(10, 0, 12, 0)
        chip_layout.setSpacing(8)
        self._path_icon = QLabel()
        self._path_icon.setStyleSheet("background: transparent;")
        chip_layout.addWidget(self._path_icon)
        self.location = QLabel("尚未选择文件库")
        self.location.setObjectName("PathText")
        chip_layout.addWidget(self.location)
        self._path_chip = path_chip
        self._refresh_path_icon()
        path_chip.setMaximumWidth(640)
        path_chip.setMinimumHeight(30)
        location_row.addWidget(path_chip)
        location_row.addStretch()
        root.addLayout(location_row)

        root.addSpacing(14)

        # ── Search bar ──
        search_row = QHBoxLayout()
        search_row.setSpacing(12)

        self.search = QLineEdit()
        self.search.setObjectName("SearchField")
        self.search.setPlaceholderText("搜索文件名、文件夹或扩展名")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(300)
        self.search.setMinimumHeight(40)
        self._search_icon_action = self.search.addAction(
            painted_icon("search", _TEXT_TERTIARY, 16),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        self.search.textChanged.connect(self._schedule_refresh)
        search_row.addWidget(self.search, 1)

        kbd_hint = QLabel("Ctrl K")
        kbd_hint.setObjectName("Kbd")
        search_row.addWidget(kbd_hint)

        status_block = QHBoxLayout()
        status_block.setSpacing(12)

        self.status = QLabel("")
        self.status.setObjectName("Caption")
        self.status.setWordWrap(True)

        self.scan_button = QPushButton("重新扫描")
        self.scan_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.scan_button.clicked.connect(self.start_scan)
        status_block.addWidget(self.scan_button)

        search_row.addLayout(status_block)
        root.addLayout(search_row)
        root.addWidget(self.status)

        root.addSpacing(14)

        # ── Segmented filter tabs ──
        filter_row = QHBoxLayout()
        filter_row.setContentsMargins(0, 0, 0, 0)
        segmented = QFrame()
        segmented.setObjectName("Segmented")
        segmented_layout = QHBoxLayout(segmented)
        segmented_layout.setContentsMargins(5, 5, 5, 5)
        segmented_layout.setSpacing(4)

        self.filter_buttons: dict[str, QPushButton] = {}
        filter_specs = [
            ("all",        "全部",   "全部素材（图片+源文件）"),
            ("psd",        "PSD",    "PSD 源文件"),
            ("source",     "源文件", "AI / CDR / EPS 等矢量与工程源文件"),
            ("products",   "产品库", "产品库文件夹（劲牌产品PNG）"),
            ("image",      "图片",   "输出图片"),
            ("recent",     "最近",   "最近 30 天修改的文件"),
            ("duplicates", "重复",   "内容相同的重复素材（按内容签名比对，需先扫描一次）"),
        ]
        for key, label, tip in filter_specs:
            button = QPushButton(label)
            button.setObjectName("FilterButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(tip)
            button.clicked.connect(lambda _checked, value=key: self.set_filter(value))
            self.filter_buttons[key] = button
            segmented_layout.addWidget(button)

        filter_row.addWidget(segmented)
        filter_row.addStretch()
        root.addLayout(filter_row)

        root.addSpacing(16)

        # ── File card grid ──
        self.file_list = DragFileList()
        self.file_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.file_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.file_list.setUniformItemSizes(True)
        self.file_list.setSpacing(20)
        self.file_list.setIconSize(QSize(1, 1))
        self.file_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.file_list.setDragEnabled(True)
        self.file_list.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.file_list.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.file_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.file_list.customContextMenuRequested.connect(self._show_context_menu)
        self.file_list.verticalScrollBar().valueChanged.connect(lambda _v: self._render_visible(show_hint=True))
        self._cols = 1
        self._mat_first: int | None = None
        self._mat_last = 0
        self._pending_previews = 0
        self.file_list.itemDoubleClicked.connect(self._open_item)
        self.file_list.itemClicked.connect(self._item_clicked)
        self.file_list.itemSelectionChanged.connect(self._on_selection_changed)
        root.addWidget(self.file_list, 1)
        self.empty_state = EmptyStatePanel("folder")
        self.empty_state.action_button.clicked.connect(self._on_empty_action)
        self.empty_state.hide()
        root.addWidget(self.empty_state, 1)

        self.filter_buttons["all"].setChecked(True)
        self._update_grid()

    # ── Grid system ──
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_grid()
        QTimer.singleShot(50, self._render_visible)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._update_grid)
        QTimer.singleShot(200, self._render_visible)

    def _update_grid(self):
        spacing = 20
        width = max(250, self.file_list.viewport().width())
        if self.current_filter == "products":
            cell = 340 + spacing
            height = 316
        else:
            cell = 260 + spacing
            height = 236
        columns = max(2, (width + spacing) // cell)
        self._cols = columns
        card_width = (width - (columns - 1) * spacing) // columns
        self.file_list.setGridSize(QSize(card_width, height))
        self.file_list.setSpacing(spacing)
        QTimer.singleShot(0, self._render_visible)

    # ── Data loading ──
    def _load_root(self):
        self.repository.migrate_library_root(DEFAULT_ROOT)
        root = self.repository.get_root_directory()
        if root and Path(root).exists():
            self.location.setText(str(root))
            self.start_scan()
        elif root:
            self.location.setText(f"{root}（当前未连接，点击右上角「更改文件夹」重新指定）")
            self.status.setText("当前文件库路径不可访问，请插上移动硬盘或点「更改文件夹」选择新位置")
        else:
            self.location.setText("尚未选择文件库（点击右上角「更改文件夹」指定你的设计素材目录）")
            self.status.setText("首次使用请点击右上角「更改文件夹」选择你的设计素材目录")
        self._update_empty_state()

    def _on_empty_action(self):
        if self.search.text().strip():
            self.search.clear()
        elif self.current_filter != "all":
            self.set_filter("all")
        else:
            self.choose_root()

    def _update_empty_state(self):
        self.status.setVisible(
            bool(self.current_files) or self._scan_running
            or self.status.text().startswith("扫描失败")
        )
        if self.current_files:
            self.file_list.show()
            self.empty_state.hide()
            return
        root = self.repository.get_root_directory()
        if self._scan_running:
            title = "正在建立素材索引"
            detail = "扫描完成后，图片与设计源文件会出现在这里。"
            action = ""
        elif not root:
            title = "连接你的设计素材"
            detail = "选择存放 PSD、图片和设计源文件的目录，工作台会为你建立可搜索的素材库。"
            action = "选择素材目录"
        elif not Path(root).exists():
            title = "素材目录暂时不可访问"
            detail = "检查移动硬盘或网络盘是否已连接，也可以重新指定目录。"
            action = "更改目录"
        elif self.search.text().strip():
            title = "没有匹配的素材"
            detail = "试试简化关键词，或清除搜索后浏览全部素材。"
            action = "清除搜索"
        elif self.current_filter != "all":
            title = "当前分类还没有素材"
            detail = "切换到全部素材，或选择其他包含设计文件的目录。"
            action = "查看全部"
        else:
            title = "目录中还没有可展示的素材"
            detail = "确认选择了正确目录；新增图片或设计源文件后，可以重新扫描。"
            action = "更改目录"
        self.empty_state.set_content(title, detail, action)
        self.empty_state.action_button.setVisible(bool(action))
        self.file_list.hide()
        self.empty_state.show()

    def _detect_product_folder(self):
        root = self.repository.get_root_directory()
        self.product_folder = self.repository.find_product_folder(root) if root else None

    def _choose_product_folder(self):
        from .styles import _ACCENT_LIGHT, _BORDER, _CARD_BG, _TEXT_PRIMARY, _TEXT_TERTIARY
        root = self.repository.get_root_directory()
        if not root:
            QMessageBox.information(self, "提示", "先选择文件库文件夹")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("更换产品库文件夹")
        dialog.setMinimumWidth(420)
        # 修复原先 `{_ACCENT}14` 被 Qt 解析为 #AARRGGBB 亮绿色的 Bug，改用标准 rgba token
        dialog.setStyleSheet(f"""
            QDialog {{ background: {_CARD_BG}; border-radius: 16px; }}
            QLabel {{ color: {_TEXT_PRIMARY}; font-size: 14px; background: transparent; }}
            QListWidget {{
                background: {_CARD_BG}; border: 1px solid {_BORDER};
                border-radius: 12px; padding: 4px;
            }}
            QListWidget::item {{ border-radius: 8px; padding: 8px 12px; }}
            QListWidget::item:selected {{ background: {_ACCENT_LIGHT}; color: {_TEXT_PRIMARY}; }}
            QPushButton {{ border-radius: 10px; padding: 8px 16px; font-size: 13px; }}
        """)
        layout = QVBoxLayout(dialog)
        layout.setSpacing(12)
        tip = QLabel("选择要作为产品库的文件夹（手动指定优先于自动识别）：")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        folder_list = QListWidget()
        current = self.product_folder
        root_path = Path(root)
        try:
            subdirs = sorted(
                (entry.name for entry in os.scandir(root_path) if entry.is_dir()),
                key=lambda name: name.lower(),
            )
        except OSError:
            subdirs = []
        for name in subdirs:
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setToolTip(str(root_path / name))
            folder_list.addItem(item)
            if current and not os.path.isabs(current) and current == name:
                folder_list.setCurrentItem(item)
        layout.addWidget(folder_list)

        if current:
            hint = QLabel(f"当前产品库：{current}")
            hint.setObjectName("Muted")
            hint.setStyleSheet(f"color: {_TEXT_TERTIARY}; font-size: 12px;")
            layout.addWidget(hint)

        buttons = QHBoxLayout()
        auto_button = QPushButton("自动识别")
        auto_button.setToolTip("清除手动指定，恢复为自动识别名字含「产品」的文件夹")
        auto_button.clicked.connect(lambda: self._apply_product_folder(None, dialog))
        buttons.addWidget(auto_button)
        browse_other = QPushButton("浏览其他目录…")
        browse_other.setToolTip("直接选择电脑上任意位置的产品图文件夹")
        browse_other.clicked.connect(lambda: self._browse_external_product_folder(dialog))
        buttons.addWidget(browse_other)
        buttons.addStretch()
        cancel_button = QPushButton("取消")
        cancel_button.clicked.connect(dialog.reject)
        buttons.addWidget(cancel_button)
        ok_button = QPushButton("确定")
        ok_button.setObjectName("PrimaryButton")
        ok_button.clicked.connect(
            lambda: self._apply_product_folder(
                folder_list.currentItem().data(Qt.ItemDataRole.UserRole) if folder_list.currentItem() else None,
                dialog,
            )
        )
        buttons.addWidget(ok_button)
        layout.addLayout(buttons)
        dialog.exec()

    def _browse_external_product_folder(self, dialog):
        selected = QFileDialog.getExistingDirectory(self, "选择产品库文件夹")
        if selected:
            self._apply_product_folder(selected, dialog)

    def _apply_product_folder(self, folder_ref, dialog):
        self.repository.set_product_folder(folder_ref)
        if folder_ref and os.path.isabs(str(folder_ref)):
            self.repository.set_setting("product_library_root", str(folder_ref))
        dialog.accept()
        self._detect_product_folder()
        if self.current_filter == "products":
            self.refresh_files()
        else:
            self.set_filter("products")

    def choose_root(self):
        selected = QFileDialog.getExistingDirectory(self, "选择文件库根目录")
        if not selected:
            return
        blocked = _blocked_root_name(Path(selected))
        if blocked:
            QMessageBox.warning(
                self,
                "不能选择这个位置",
                f"「{blocked}」里的文件太多太杂，直接扫描会把程序卡死。\n\n"
                "请新建一个专门的文件库文件夹（例如 E:\\电商视觉工作台），"
                "只把 PSD源文件、输出图片、工单文件放进去，再选择它。",
            )
            return
        self.repository.set_root_directory(selected)
        self.location.setText(str(selected))
        self._detect_product_folder()
        win = self.window()
        if hasattr(win, "refresh_drive_widget"):
            win.refresh_drive_widget()
        self.start_scan()

    def start_scan(self):
        root = self.repository.get_root_directory()
        if not root or not Path(root).exists():
            self.choose_root()
            return
        self._scan_running = True
        self._update_empty_state()
        self.scan_button.setEnabled(False)
        self.status.setText("扫描中…")
        worker = ScanWorker(root, self.repository)
        worker.signals.progress.connect(lambda current, total: self.status.setText(f"扫描 {current}/{total}"))
        worker.signals.finished.connect(self.scan_finished)
        worker.signals.failed.connect(self.scan_failed)
        self.thread_pool.start(worker)

    def scan_finished(self, _ids):
        self._scan_running = False
        self.scan_button.setEnabled(True)
        self.status.setText("扫描完成")
        self.repository.clear_screenshot_entries()
        self._detect_product_folder()
        self.refresh_files()
        self.scanCompleted.emit()

    def scan_failed(self, message: str):
        self._scan_running = False
        self.scan_button.setEnabled(True)
        self.status.setText(f"扫描失败：{message}")
        self._update_empty_state()

    def set_filter(self, key: str):
        if key == "products" and not self.product_folder:
            QMessageBox.information(
                self,
                "没有产品库",
                "未找到产品库文件夹。\n\n请在文件库根目录下建一个名字含「产品」的文件夹（如：劲牌产品PNG），\n点「重新扫描」后这里就会出现产品库。",
            )
            self.filter_buttons["all"].setChecked(True)
            return
        self.current_filter = key
        for name, button in self.filter_buttons.items():
            button.setChecked(name == key)
        self.refresh_files()

    def _schedule_refresh(self):
        self._search_timer.start()

    def refresh_files(self):
        epoch = self._refresh_epoch + 1
        self._refresh_epoch = epoch
        self._preview_pool.clear()
        self._pending_previews = 0
        self._dispatched_rows.clear()
        text = self.search.text()
        self._dup_counts = {}
        self._dup_sizes = {}
        if self.current_filter == "duplicates":
            matched = self.repository.find_duplicate_files()
            if text:
                needle = text.lower()
                matched = [
                    f for f in matched
                    if needle in f.file_name.lower()
                    or needle in f.relative_path.lower()
                    or needle in f.extension.lower()
                ]
            for file in matched:
                if file.content_hash:
                    hash_key = file.content_hash
                    self._dup_counts[hash_key] = self._dup_counts.get(hash_key, 0) + 1
                    self._dup_sizes[hash_key] = file.size_bytes
        elif self.current_filter == "products":
            matched = self.repository.list_files_in_folder(self.product_folder, text)
        elif text:
            matched = self.repository.search_files(text, self.current_filter)
        else:
            matched = self.repository.list_files(self.current_filter)

        self.current_files = matched
        self.file_list.clear()
        self._mat_first = None
        self._mat_last = 0
        self._row_by_id = {
            file.id: row for row, file in enumerate(self.current_files) if file.id is not None
        }
        hint_size = QSize(300, 304) if self.current_filter == "products" else QSize(230, 224)
        self.file_list.setUpdatesEnabled(False)
        for file in self.current_files:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, file.id)
            item.setData(_PATH_ROLE, file.absolute_path)
            item.setSizeHint(hint_size)
            self.file_list.addItem(item)
        self.file_list.setUpdatesEnabled(True)

        total = len(self.current_files)
        if total == 0:
            self._hide_pos_hint()
        if self.current_filter == "duplicates":
            if total == 0:
                self.status.setText("没有发现重复素材（若刚升级，先点一次「重新扫描」生成内容签名）")
            else:
                groups, redundant = self.repository.count_duplicate_groups()
                redundant_bytes = sum(
                    (count - 1) * self._dup_sizes.get(hash_key, 0)
                    for hash_key, count in self._dup_counts.items() if count > 1
                )
                self.status.setText(
                    f"发现 {groups} 组重复 · 当前显示 {total} 个 · "
                    f"每组留一份可省约 {redundant_bytes / 1024 / 1024:.0f} MB"
                    f"（共 {redundant} 个多余副本）"
                )
        elif self.current_filter == "products":
            if total == 0:
                self.status.setText("产品库是空的，把产品图放进文件夹后点「重新扫描」")
            else:
                self.status.setText(f"产品库 · {total} 个产品")
        elif total == 0:
            self.status.setText("文件库是空的，把设计文件放进文件夹后点「重新扫描」")
        else:
            self.status.setText(f"共 {total} 个文件")
        self._update_grid()
        self._update_empty_state()
        QTimer.singleShot(0, self._render_visible)
        QTimer.singleShot(200, self._render_visible)

    def _render_visible(self, show_hint: bool = False):
        """根据滚动条位置估算当前可见行，渲染附近卡片（上下各8行缓冲）。"""
        if not self.current_files:
            self._hide_pos_hint()
            return
        total = len(self.current_files)
        sb = self.file_list.verticalScrollBar()
        vp_h = max(self.file_list.viewport().height(), 200)
        cols = max(1, self._cols)
        total_rows = (total + cols - 1) // cols
        if sb.maximum() <= 0:
            first_row = 0
        else:
            fraction = sb.value() / max(sb.maximum(), 1)
            first_row = int(fraction * total_rows)
        rows_visible = max(1, vp_h // 256)
        buffer = 8
        first_row = max(0, first_row - buffer)
        last_row = min(total_rows, first_row + rows_visible + buffer * 2)
        first = first_row * cols
        last = min(total, last_row * cols)
        self._materialize_range(first, last)

        # 只有真正发生滚动且内容超过一屏时才浮出进度提示，并在 1.2s 后自动隐藏
        if show_hint and sb.maximum() > 0:
            pct = int(sb.value() / sb.maximum() * 100)
            if not hasattr(self, "pos_hint") or self.pos_hint is None:
                vp = self.file_list.viewport()
                self.pos_hint = QLabel(vp)
                self.pos_hint.setStyleSheet(
                    "background: rgba(15,23,42,0.78); color: #FFFFFF;"
                    "border-radius: 12px; padding: 4px 12px; font-size: 12px; font-weight: 600;"
                )
                self.pos_hint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self.pos_hint.setText(f"{pct}% · 共 {total} 个")
            self.pos_hint.adjustSize()
            vp = self.file_list.viewport()
            self.pos_hint.move(
                vp.width() - self.pos_hint.width() - 16,
                vp.height() - self.pos_hint.height() - 16,
            )
            self.pos_hint.raise_()
            self.pos_hint.show()
            self._pos_hint_timer.start()

    def _materialize_range(self, first: int, last: int):
        total = len(self.current_files)
        self.file_list.setUpdatesEnabled(False)
        try:
            for row in range(first, last):
                item = self.file_list.item(row)
                if item is None:
                    continue
                if self.file_list.itemWidget(item) is None:
                    self._materialize_row(row)
            if self._mat_first is not None:
                for row in range(self._mat_first, min(self._mat_last, total)):
                    if first <= row < last:
                        continue
                    item = self.file_list.item(row)
                    if item is None:
                        continue
                    w = self.file_list.itemWidget(item)
                    if w is not None:
                        self.file_list.removeItemWidget(item)
                        w.deleteLater()
        finally:
            self.file_list.setUpdatesEnabled(True)
        self._mat_first, self._mat_last = first, last

    def _materialize_row(self, row: int):
        file = self.current_files[row]
        item = self.file_list.item(row)
        if item is None:
            return
        if (
            file.preview_status == "ready"
            and file.preview_path
            and not Path(file.preview_path).exists()
            and file.id is not None
        ):
            self.repository.update_preview(file.id, None, "pending")
            refreshed = self.repository.get_file(file.id)
            if refreshed:
                file = refreshed
                self.current_files[row] = file
        card = FileCard(
            file,
            large=(self.current_filter == "products"),
            dup_count=self._dup_counts.get(file.content_hash or "", 0),
        )
        self.file_list.setItemWidget(item, card)
        if file.preview_status == "pending" and file.id is not None:
            self._try_dispatch_preview(row, file)

    def _try_dispatch_preview(self, row: int, file: FileRecord) -> bool:
        if row in self._dispatched_rows or self._pending_previews >= 24 or file.id is None:
            return False
        self._dispatched_rows.add(row)
        self._pending_previews += 1
        epoch = self._refresh_epoch
        worker = PreviewWorker(file, self.repository, epoch)
        worker.signals.finished.connect(self.preview_finished)
        worker.signals.failed.connect(lambda _err, e=epoch: self._on_preview_failed(e))
        self._preview_pool.start(worker)
        return True

    def _on_preview_failed(self, epoch: int):
        if epoch != self._refresh_epoch:
            return
        self._pending_previews = max(0, self._pending_previews - 1)
        self._pump_pending_previews()

    def _pump_pending_previews(self):
        """当并发槽位空出时，为当前可见区尚未派发的 pending 卡片补派发预览任务。"""
        if self._mat_first is None or self._pending_previews >= 24:
            return
        total = len(self.current_files)
        for row in range(self._mat_first, min(self._mat_last, total)):
            if self._pending_previews >= 24:
                break
            if row in self._dispatched_rows:
                continue
            file = self.current_files[row]
            if file.preview_status == "pending" and file.id is not None:
                self._try_dispatch_preview(row, file)

    def preview_finished(self, payload):
        file_id, result, epoch = payload
        if epoch != self._refresh_epoch:
            return
        self._pending_previews = max(0, self._pending_previews - 1)
        if file_id:
            row = self._row_by_id.get(file_id)
            if row is not None and 0 <= row < len(self.current_files):
                updated = replace(
                    self.current_files[row],
                    preview_path=result.path,
                    preview_status=result.status,
                )
                self.current_files[row] = updated
                item = self.file_list.item(row)
                if item is not None:
                    card = self.file_list.itemWidget(item)
                    if isinstance(card, FileCard):
                        card.file = updated
                        card.set_preview(result.path)
        self._pump_pending_previews()

    # ── Card interactions ──
    def _open_item(self, item: QListWidgetItem):
        file = self._file_for_item(item)
        if file:
            open_file(file.absolute_path)

    def _item_clicked(self, item: QListWidgetItem):
        file = self._file_for_item(item)
        if file:
            label = LABELS.get(file.category, LABELS["other"])[0]
            self.status.setText(f"{label} · {file.file_name} · {file.size_bytes / 1024 / 1024:.1f} MB")
            self._last_clicked_path = file.absolute_path

    def _file_for_item(self, item: QListWidgetItem) -> FileRecord | None:
        """优先从内存列表中 O(1) 取记录，避免每次单击卡片都查一次 SQLite。"""
        file_id = item.data(Qt.ItemDataRole.UserRole)
        if file_id is None:
            return None
        row = self._row_by_id.get(file_id)
        if row is not None and 0 <= row < len(self.current_files):
            return self.current_files[row]
        return self.repository.get_file(file_id)

    def _show_context_menu(self, position):
        item = self.file_list.itemAt(position)
        if item is None:
            return
        if not item.isSelected():
            self.file_list.clearSelection()
            item.setSelected(True)
        paths = self._selected_paths()
        if not paths:
            return
        # 直接使用全局 QSS 的 QMenu 样式，修复原先 `{_ACCENT}14` 导致的荧光绿悬停背景
        menu = QMenu(self)
        actions: dict[object, str] = {}
        single = len(paths) == 1
        if single:
            actions[menu.addAction("打开文件")] = "open"
            actions[menu.addAction("在资源管理器中定位")] = "reveal"
            menu.addSeparator()
        actions[menu.addAction(
            "复制完整路径" if single else f"复制 {len(paths)} 个完整路径"
        )] = "copy"
        actions[menu.addAction(
            "复制文件名" if single else f"复制 {len(paths)} 个文件名"
        )] = "copy_name"
        psd_paths = [p for p in paths if Path(p).suffix.lower() in (".psd", ".psb")]
        if psd_paths:
            menu.addSeparator()
            actions[menu.addAction(
                "用 PSD 工具导出分组…" if len(psd_paths) == 1
                else f"用 PSD 工具导出分组（{len(psd_paths)} 个）…"
            )] = "export_groups"
        chosen = menu.exec(self.file_list.mapToGlobal(position))
        action = actions.get(chosen)
        if action == "open":
            open_file(paths[0])
        elif action == "reveal":
            reveal_file(paths[0])
        elif action == "copy":
            QApplication.clipboard().setText("\n".join(paths))
            self.status.setText(f"已复制 {len(paths)} 个完整路径")
        elif action == "copy_name":
            QApplication.clipboard().setText("\n".join(Path(p).name for p in paths))
            self.status.setText(f"已复制 {len(paths)} 个文件名")
        elif action == "export_groups":
            self._open_psd_tool(psd_paths)

    def _open_psd_tool(self, paths: list[str]) -> None:
        window = self.window()
        if hasattr(window, "open_psd_tool"):
            window.open_psd_tool(paths)
            self.status.setText(f"已把 {len(paths)} 个 PSD 送到「PSD 工具」页")
        else:
            QMessageBox.information(
                self, "打不开 PSD 工具",
                "没找到「PSD 工具」页，请从左侧导航手动进入后再拖入文件。",
            )
