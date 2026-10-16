from __future__ import annotations

import ctypes
import ctypes.wintypes
from datetime import date, datetime
from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve, QPoint, QPropertyAnimation, QRectF, QSize, QThreadPool, Qt, QTimer,
)
from PySide6.QtGui import QColor, QCursor, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QApplication, QFrame, QGraphicsDropShadowEffect, QGraphicsOpacityEffect,
    QHBoxLayout, QLabel, QMainWindow, QProgressBar, QPushButton, QStackedWidget,
    QSystemTrayIcon, QVBoxLayout, QWidget,
)

from ..repository import Repository
from .cleaner_view import CleanerPage
from .file_library import FileLibraryPage
from .home_view import HomePage
from .icons import icon as painted_icon
from .organizer_view import OrganizerPage
from .product_view import ProductPage
from .psd_text_view import PsdTextPage
from .psd_tool_view import PsdToolPage
from .report_view import ReportPage
from .settings_view import SettingsPage
from .splash import StartupSplash
from .styles import (
    APP_STYLE, animate_fade_in, apply_palette, build_app_style, get_theme, set_theme,
)
from .task_view import TaskPage
from .todo_view import TodoPage

# 侧边栏导航：图标名（icons.py 里画的） + 文字（保持索引 0..10 不变）
NAV_ITEMS = (
    ("home", "工作台首页"),
    ("library", "素材资产库"),
    ("bottle", "产品库"),
    ("archive", "AI 智能归档"),
    ("task", "排期与工单"),
    ("calendar", "日程"),
    ("chart", "出图效能"),
    ("cleaner", "工单清洗"),
    ("layers", "PSD 工具"),
    ("text", "PSD 文字替换"),
    ("sliders", "设置"),
)

# 侧边栏分组小标题（在指定索引前插入分组名，让 11 个导航项层次分明）
NAV_GROUP_HEADERS: dict[int, str] = {
    0: "视觉中心 · ASSETS",
    3: "排期效能 · WORKFLOW",
    7: "批量工坊 · STUDIO",
    10: "系统 · SYSTEM",
}

_RES = Path(__file__).resolve().parent / "resources"
_LOGO_PATH = (_RES / "logo.png").as_posix()


class _GlobalToast(QLabel):
    """主窗口级底部轻提示（供各页面调用 window().show_toast(...)）。"""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("GlobalToast")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedHeight(38)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(1.0)
        self.setGraphicsEffect(self._effect)
        self._fade = QPropertyAnimation(self._effect, b"opacity", self)
        self._fade.setDuration(240)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.finished.connect(self._on_fade_done)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fade_out)
        self.hide()

    def restyle(self):
        from .styles import _BORDER_STRONG, _CARD_BG, _TEXT_PRIMARY
        self.setStyleSheet(
            f"QLabel#GlobalToast {{ background: {_CARD_BG}; color: {_TEXT_PRIMARY};"
            f" border: 1px solid {_BORDER_STRONG}; border-radius: 12px;"
            f" padding: 0 18px; font-size: 13px; font-weight: 600; }}"
        )

    def pop(self, text: str):
        self.setText(text)
        self._fade.stop()
        self._effect.setOpacity(1.0)
        self.restyle()
        width = max(200, self.fontMetrics().horizontalAdvance(text) + 48)
        self.setFixedWidth(min(width, max(240, self.parentWidget().width() - 80)))
        self.reposition()
        self.show()
        self.raise_()
        self._timer.start(2400)

    def reposition(self):
        parent = self.parentWidget()
        if parent is None:
            return
        x = (parent.width() - self.width()) // 2
        y = parent.height() - self.height() - 28
        self.move(max(16, x), max(16, y))

    def _fade_out(self):
        self._fade.setStartValue(self._effect.opacity())
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _on_fade_done(self):
        if self._effect.opacity() <= 0.01:
            self.hide()


class _WinButton(QPushButton):
    """自绘窗口控制按钮：最小化 / 最大化（或还原双框） / 关闭。"""

    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        self.kind = kind
        self._hover = False
        self.setFixedSize(44, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("background: transparent; border: 0;")

    def enterEvent(self, event):
        self._hover = True
        self.update()

    def leaveEvent(self, event):
        self._hover = False
        self.update()

    def paintEvent(self, event):
        from .icons import to_qcolor
        from .styles import _DANGER, _SIDEBAR_HOVER, _TEXT_SECONDARY, _TEXT_TERTIARY

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        if self._hover:
            if self.kind == "close":
                painter.setBrush(QColor(_DANGER))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(0, 0, w, h, 8, 8)
                icon_color = QColor("#FFFFFF")
            else:
                painter.setBrush(to_qcolor(_SIDEBAR_HOVER))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(0, 0, w, h, 8, 8)
                icon_color = to_qcolor(_TEXT_SECONDARY)
        else:
            icon_color = to_qcolor(_TEXT_TERTIARY)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        pen = painter.pen()
        pen.setColor(icon_color)
        pen.setWidthF(1.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        cx, cy = w / 2.0, h / 2.0
        if self.kind == "min":
            painter.drawLine(int(cx - 6), int(cy), int(cx + 6), int(cy))
        elif self.kind == "max":
            win = self.window()
            is_max = bool(win and win.isMaximized())
            if is_max:
                painter.drawRoundedRect(QRectF(cx - 6, cy - 3, 9, 8), 1.2, 1.2)
                path = QPainterPath()
                path.moveTo(cx - 3, cy - 3)
                path.lineTo(cx - 3, cy - 5)
                path.lineTo(cx + 6, cy - 5)
                path.lineTo(cx + 6, cy + 3)
                path.lineTo(cx + 3, cy + 3)
                painter.drawPath(path)
            else:
                painter.drawRoundedRect(QRectF(cx - 6, cy - 5, 12, 10), 1.4, 1.4)
        elif self.kind == "close":
            painter.drawLine(int(cx - 5), int(cy - 5), int(cx + 5), int(cy + 5))
            painter.drawLine(int(cx + 5), int(cy - 5), int(cx - 5), int(cy + 5))
        painter.end()


def _make_tray_icon() -> QIcon:
    """使用 Logo 图片作为托盘图标。"""
    logo_path = Path(__file__).resolve().parent / "resources" / "logo.png"
    if logo_path.exists():
        pixmap = QPixmap(str(logo_path))
        if not pixmap.isNull():
            scaled_pixmap = pixmap.scaled(
                32, 32,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            return QIcon(scaled_pixmap)

    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#2563EB"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(4, 4, 24, 24)
    painter.end()
    return QIcon(pixmap)


def _enable_mica(hwnd: int) -> bool:
    """Windows 11 的 Mica 背景材质（DWMWA_SYSTEMBACKDROP_TYPE = 38）。"""
    try:
        value = ctypes.c_int(2)
        result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 38, ctypes.byref(value), ctypes.sizeof(value))
        if result != 0:
            return False
        margins = (ctypes.c_int * 4)(-1, -1, -1, -1)
        ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))
        return True
    except Exception:
        return False


def _enable_acrylic(hwnd: int) -> bool:
    """Win10 1803+ 的 Acrylic 毛玻璃（Mica 不可用时的退路）。"""
    try:
        class ACCENT_POLICY(ctypes.Structure):
            _fields_ = [
                ("AccentState", ctypes.c_int),
                ("AccentFlags", ctypes.c_int),
                ("GradientColor", ctypes.c_uint),
                ("AnimationId", ctypes.c_int),
            ]

        class WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):
            _fields_ = [
                ("Attrib", ctypes.c_int),
                ("pvData", ctypes.c_void_p),
                ("cbData", ctypes.c_size_t),
            ]

        accent = ACCENT_POLICY()
        accent.AccentState = 4
        accent.GradientColor = (110 << 24) | (242 << 16) | (243 << 8) | 245
        data = WINDOWCOMPOSITIONATTRIBDATA()
        data.Attrib = 19
        data.pvData = ctypes.cast(ctypes.byref(accent), ctypes.c_void_p)
        data.cbData = ctypes.sizeof(accent)
        return bool(ctypes.windll.user32.SetWindowCompositionAttribute(
            hwnd, ctypes.byref(data)))
    except Exception:
        return False


def _sync_dwm_theme(hwnd: int, dark: bool) -> None:
    """同步 Windows 11 沉浸式暗色边框与圆角。"""
    try:
        dwm = ctypes.windll.dwmapi
        dark_val = ctypes.c_int(1 if dark else 0)
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark_val), 4)
        pref = ctypes.c_int(2)  # DWMWCP_ROUND
        dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), 4)
    except Exception:
        pass


class MainWindow(QMainWindow):
    def __init__(self, repository: Repository):
        super().__init__()
        self.repository = repository
        self.thread_pool = QThreadPool(self)
        self.thread_pool.setMaxThreadCount(6)
        self._acrylic_done = False
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)

        logo_path = Path(__file__).resolve().parent / "resources" / "logo.png"
        if logo_path.exists():
            icon = QIcon(str(logo_path))
            self.setWindowIcon(icon)
            app = QApplication.instance()
            if app:
                app.setWindowIcon(icon)

        self.setWindowTitle("电商视觉工作台")
        self.resize(1440, 900)
        self.setMinimumSize(1020, 680)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setStyleSheet(APP_STYLE)
        _app = QApplication.instance()
        if _app:
            apply_palette(_app)
        self._build_ui()
        self._toast = _GlobalToast(self)
        self._setup_reminders()
        saved_theme = self.repository.get_setting("theme", "light")
        if saved_theme in ("light", "dark") and saved_theme != get_theme():
            self.apply_theme(saved_theme, persist=False)
        if self.repository.get_window_backdrop():
            self.set_backdrop(True)

    def show_toast(self, text: str) -> None:
        """在主窗口底部弹出轻提示（供各子页面統一调用）。"""
        self._toast.pop(text)

    def reveal_with_fade(self) -> None:
        """启动屏结束时平滑淡入显示主窗口（60fps 窗口级透明度过渡）。"""
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self.activateWindow()
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(220)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._reveal_anim = anim
        anim.start()

    def preview_startup_splash(self) -> StartupSplash:
        """随时预览程序启动动画（供设置页调用）。"""
        splash = StartupSplash()
        self._active_splash = splash
        splash.start()
        return splash

    # ── 窗口磨砂背景（Windows 系统材质） ──
    def set_backdrop(self, enabled: bool) -> bool:
        applied = False
        if enabled:
            try:
                hwnd = int(self.winId())
                applied = _enable_mica(hwnd) or _enable_acrylic(hwnd)
            except Exception:
                applied = False
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, applied)
        central = self.centralWidget()
        if central is not None:
            if applied:
                from .styles import backdrop_tint
                central.setStyleSheet(f"QWidget#Root {{ background: {backdrop_tint()}; }}")
            else:
                central.setStyleSheet("")
        self._backdrop_enabled = applied
        return applied

    def refresh_backdrop_tint(self) -> None:
        if getattr(self, "_backdrop_enabled", False):
            self.set_backdrop(True)

    def _setup_reminders(self):
        self.tray = QSystemTrayIcon(_make_tray_icon(), self)
        self.tray.setToolTip("电商视觉工作台")
        self.tray.show()
        self._remind_timer = QTimer(self)
        self._remind_timer.timeout.connect(self._check_reminders)
        self._remind_timer.start(30_000)

    def _check_reminders(self):
        try:
            today = date.today()
            now = datetime.now().strftime("%H:%M")
            for todo in self.repository.get_due_todos(today, now):
                self.tray.showMessage(
                    "待办提醒",
                    f"{today.month}月{today.day}日 {todo.remind_time} · {todo.title}",
                    QSystemTrayIcon.MessageIcon.Information,
                    8000,
                )
                self.repository.mark_todo_reminded(todo.id)
        except Exception:
            pass

    def _build_ui(self):
        central = QWidget()
        central.setObjectName("Root")
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._make_titlebar())

        body = QWidget()
        outer = QHBoxLayout(body)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ─── Sidebar wrapper — holds the floating dock ───
        sidebar_wrapper = QWidget()
        sidebar_wrapper.setStyleSheet("background: transparent;")
        sidebar_wrapper.setFixedWidth(226)
        self._sidebar_wrapper = sidebar_wrapper
        wrapper_layout = QVBoxLayout(sidebar_wrapper)
        wrapper_layout.setContentsMargins(16, 14, 0, 16)

        # ─── Sidebar — floating dock ───
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setStyleSheet(self._sidebar_stylesheet())
        self._sidebar = sidebar
        _sidebar_shadow = QGraphicsDropShadowEffect(sidebar)
        _sidebar_shadow.setBlurRadius(24)
        _sidebar_shadow.setXOffset(0)
        _sidebar_shadow.setYOffset(6)
        _sidebar_shadow.setColor(QColor(9, 9, 11, 22))
        sidebar.setGraphicsEffect(_sidebar_shadow)
        self._sidebar_shadow = _sidebar_shadow

        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(13, 14, 13, 12)
        side_layout.setSpacing(1)

        # ── 用户胶囊 ──
        from .styles import _ACCENT, _ACCENT_PRESSED
        profile = QFrame()
        profile.setObjectName("ProfileCapsule")
        profile_lay = QHBoxLayout(profile)
        profile_lay.setContentsMargins(10, 8, 10, 8)
        profile_lay.setSpacing(10)
        avatar = QLabel("视")
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setFixedSize(32, 32)
        avatar.setStyleSheet(
            "border-radius:9px;font-weight:700;font-size:13px;color:#FFFFFF;"
            f"background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {_ACCENT},stop:1 {_ACCENT_PRESSED});"
        )
        self._avatar_label = avatar
        profile_lay.addWidget(avatar)
        profile_col = QVBoxLayout()
        profile_col.setSpacing(1)
        profile_name = QLabel("电商视觉工作台")
        profile_name.setObjectName("BrandTitle")
        profile_col.addWidget(profile_name)
        profile_status = QLabel("● 本地运行")
        profile_status.setObjectName("ProfileStatus")
        self._profile_status = profile_status
        profile_col.addWidget(profile_status)
        profile_lay.addLayout(profile_col, 1)
        side_layout.addWidget(profile)
        side_layout.addSpacing(6)

        # Navigation items（带分组小标题，视觉清晰不拥挤）
        self.nav_buttons: list[QPushButton] = []
        for index, (_icon_name, label) in enumerate(NAV_ITEMS):
            if index in NAV_GROUP_HEADERS:
                group_lbl = QLabel(NAV_GROUP_HEADERS[index])
                group_lbl.setObjectName("NavGroupLabel")
                side_layout.addWidget(group_lbl)
            button = QPushButton(f"  {label}")
            button.setObjectName("NavButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setIconSize(QSize(16, 16))
            button.clicked.connect(lambda _checked, i=index: self.switch_page(i))
            side_layout.addWidget(button)
            self.nav_buttons.append(button)
        side_layout.addStretch()

        # ── 磁盘用量组件 ──
        self._drive_widget = self._make_drive_widget()
        side_layout.addWidget(self._drive_widget)
        side_layout.addSpacing(8)

        # Theme toggle button
        self.theme_button = QPushButton("  深色模式")
        self.theme_button.setObjectName("ThemeButton")
        self.theme_button.setCheckable(True)
        self.theme_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.theme_button.setIconSize(QSize(16, 16))
        self.theme_button.clicked.connect(self._toggle_theme)
        side_layout.addWidget(self.theme_button)

        version = QLabel("Studio v1.3 · 数据保存在本机")
        version.setObjectName("SidebarVersion")
        self._sidebar_version = version
        side_layout.addWidget(version, alignment=Qt.AlignmentFlag.AlignHCenter)

        wrapper_layout.addWidget(sidebar)
        outer.addWidget(sidebar_wrapper)

        # ─── Page container ───
        self.pages = QStackedWidget()
        self.home_page = HomePage(self.repository)
        self.file_page = FileLibraryPage(self.repository, self.thread_pool)
        self.product_page = ProductPage(self.repository, self.thread_pool)
        self.organizer_page = OrganizerPage(self.repository, self.thread_pool)
        self.task_page = TaskPage(self.repository)
        self.todo_page = TodoPage(self.repository)
        self.report_page = ReportPage(self.repository)
        self.cleaner_page = CleanerPage()
        self.psd_tool_page = PsdToolPage()
        self.psd_text_page = PsdTextPage()
        self.settings_page = SettingsPage(self.repository, self.thread_pool)
        for page in (
            self.home_page, self.file_page, self.product_page,
            self.organizer_page, self.task_page, self.todo_page,
            self.report_page, self.cleaner_page, self.psd_tool_page,
            self.psd_text_page, self.settings_page,
        ):
            self.pages.addWidget(page)

        self.psd_tool_page.exported.connect(self.file_page.start_scan)
        self.psd_text_page.exported.connect(self.file_page.start_scan)
        self.file_page.scanCompleted.connect(self._on_library_scan_completed)
        self.product_page.productsAdded.connect(lambda _n: self._on_library_scan_completed())
        outer.addWidget(self.pages, 1)
        root.addWidget(body, 1)
        self.setCentralWidget(central)

        self._current_page_index = 0
        self.switch_page(0)
        self._update_theme_button()
        self._update_responsive_chrome()

    def _update_responsive_chrome(self):
        if not hasattr(self, "_sidebar_wrapper"):
            return
        compact_width = self.width() < 1180
        compact_height = self.height() < 760
        self._sidebar_wrapper.setFixedWidth(210 if compact_width else 226)
        self._drive_widget.setVisible(not compact_height)
        self._sidebar_version.setVisible(not compact_height)
        self._profile_status.setVisible(not compact_height)

    def _make_drive_widget(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("DriveWidget")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(12, 9, 12, 10)
        lay.setSpacing(5)

        head = QHBoxLayout()
        head.setSpacing(6)
        self._drive_title = QLabel("设计盘")
        self._drive_title.setObjectName("DriveTitle")
        head.addWidget(self._drive_title)
        head.addStretch()
        self._drive_value = QLabel("—")
        self._drive_value.setObjectName("DriveValue")
        head.addWidget(self._drive_value)
        lay.addLayout(head)

        self._drive_bar = QProgressBar()
        self._drive_bar.setRange(0, 100)
        self._drive_bar.setValue(0)
        self._drive_bar.setTextVisible(False)
        self._drive_bar.setFixedHeight(5)
        lay.addWidget(self._drive_bar)

        hint = QLabel("素材索引随扫描自动更新")
        hint.setObjectName("DriveHint")
        lay.addWidget(hint)
        self.refresh_drive_widget()
        return frame

    def refresh_drive_widget(self) -> None:
        """刷新侧边栏底部的设计盘空间占用显示。"""
        import shutil

        target = Path.home()
        try:
            root = self.repository.get_root_directory()
            if root:
                target = Path(root)
        except Exception:
            pass
        used_pct = None
        label = "设计盘"
        try:
            anchor = target.anchor or "C:\\"
            total, _used, free = shutil.disk_usage(anchor)
            if total:
                used_pct = (total - free) / total * 100
                drive_letter = anchor.rstrip("\\/") or "C:"
                label = f"设计盘 ({drive_letter})"
        except Exception:
            used_pct = None

        if hasattr(self, "_drive_title"):
            self._drive_title.setText(label)
            self._drive_value.setText("—" if used_pct is None else f"{used_pct:.0f}%")
            self._drive_bar.setValue(int(used_pct) if used_pct is not None else 0)

    def _sidebar_stylesheet(self) -> str:
        from .styles import (
            _ACCENT, _ACCENT_HOVER, _ACCENT_PRESSED, _BORDER, _SIDEBAR_BG,
            _SIDEBAR_HOVER, _SURFACE_TOP, _TEXT_PRIMARY, _TEXT_SECONDARY,
        )
        return f"""
            QFrame#Sidebar {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {_SURFACE_TOP}, stop:1 {_SIDEBAR_BG});
                border: 1px solid {_BORDER};
                border-radius: 20px;
            }}
            QPushButton#NavButton {{
                border: 0; border-radius: 10px; padding: 0 12px; margin: 1px 0;
                min-height: 33px; text-align: left; background: transparent;
                color: {_TEXT_SECONDARY}; font-size: 13px; font-weight: 500;
            }}
            QPushButton#NavButton:hover {{
                background: {_SIDEBAR_HOVER}; color: {_TEXT_PRIMARY};
            }}
            QPushButton#NavButton:checked {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {_ACCENT}, stop:1 {_ACCENT_HOVER});
                color: #FFFFFF; font-weight: 600;
            }}
            QPushButton#NavButton:checked:pressed {{ background: {_ACCENT_PRESSED}; }}
            QPushButton#ThemeButton {{
                border: 0; border-radius: 10px; padding: 0 12px; margin: 1px 0;
                min-height: 32px; text-align: left; background: transparent;
                color: {_TEXT_SECONDARY}; font-size: 13px; font-weight: 500;
            }}
            QPushButton#ThemeButton:hover {{
                background: {_SIDEBAR_HOVER}; color: {_TEXT_PRIMARY};
            }}
        """

    def _toggle_theme(self):
        self.apply_theme("dark" if get_theme() == "light" else "light")

    def _on_library_scan_completed(self):
        """资产库扫描或产品添加完成后联动刷新首页与侧边栏磁盘占用。"""
        if hasattr(self, "home_page"):
            self.home_page.refresh()
        self.refresh_drive_widget()

    def apply_theme(self, name: str, persist: bool = True):
        if name not in ("light", "dark"):
            return
        set_theme(name)
        if persist:
            try:
                self.repository.set_setting("theme", name)
            except Exception:
                pass
        self._update_theme_button()
        self._refresh_nav_icons()

        new_style = build_app_style()
        self.setStyleSheet(new_style)
        app = QApplication.instance()
        if app:
            app.setStyleSheet(new_style)
            apply_palette(app)

        if hasattr(self, "_sidebar") and self._sidebar:
            self._sidebar.setStyleSheet(self._sidebar_stylesheet())

        if getattr(self, "_sidebar_shadow", None) is not None:
            self._sidebar_shadow.setColor(
                QColor(0, 0, 0, 120) if name == "dark" else QColor(9, 9, 11, 22)
            )

        if hasattr(self, "_avatar_label"):
            from .styles import _ACCENT, _ACCENT_PRESSED
            self._avatar_label.setStyleSheet(
                "border-radius:9px;font-weight:700;font-size:13px;color:#FFFFFF;"
                f"background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {_ACCENT},stop:1 {_ACCENT_PRESSED});"
            )

        for index in range(self.pages.count()):
            page = self.pages.widget(index)
            if hasattr(page, "refresh_theme"):
                page.refresh_theme()

        for btn in self.nav_buttons:
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        self.theme_button.style().unpolish(self.theme_button)
        self.theme_button.style().polish(self.theme_button)

        if hasattr(self, "_toast"):
            self._toast.restyle()

        self.refresh_backdrop_tint()
        try:
            _sync_dwm_theme(int(self.winId()), name == "dark")
        except Exception:
            pass

    def _update_theme_button(self):
        from .styles import _TEXT_TERTIARY
        if get_theme() == "dark":
            self.theme_button.setText("  浅色模式")
            self.theme_button.setIcon(painted_icon("sun", _TEXT_TERTIARY, 18))
            self.theme_button.setChecked(True)
        else:
            self.theme_button.setText("  深色模式")
            self.theme_button.setIcon(painted_icon("moon", _TEXT_TERTIARY, 18))
            self.theme_button.setChecked(False)

    def _refresh_nav_icons(self):
        from .styles import _NAV_ACTIVE_ICON, _TEXT_SECONDARY
        for index, button in enumerate(self.nav_buttons):
            active = index == getattr(self, "_current_page_index", 0)
            color = _NAV_ACTIVE_ICON if active else _TEXT_SECONDARY
            button.setIcon(painted_icon(NAV_ITEMS[index][0], color, 16))

    # ── 自绘标题栏（无边框窗口一体化） ──
    def _make_titlebar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("TitleBar")
        bar.setFixedHeight(48)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(20, 0, 8, 0)
        lay.setSpacing(10)

        logo = QLabel()
        logo.setStyleSheet("background: transparent;")
        pixmap = QPixmap(_LOGO_PATH)
        if not pixmap.isNull():
            logo.setPixmap(pixmap.scaled(
                18, 18,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        lay.addWidget(logo)

        name = QLabel("电商视觉工作台")
        name.setObjectName("TitleBarName")
        self._titlebar_name = name
        lay.addWidget(name)

        badge = QLabel("STUDIO v1.3")
        badge.setObjectName("TitleBarBadge")
        lay.addWidget(badge)
        lay.addStretch()

        # 标题栏快捷添加产品按钮（在任意页面都可一键入库）
        self.quick_add_prod_btn = QPushButton("＋ 添加产品")
        self.quick_add_prod_btn.setObjectName("GhostAccentButton")
        self.quick_add_prod_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.quick_add_prod_btn.setFixedHeight(26)
        self.quick_add_prod_btn.setToolTip("随时添加新产品图到产品库")
        self.quick_add_prod_btn.clicked.connect(lambda: self.open_add_product(switch_to_page=True))
        lay.addWidget(self.quick_add_prod_btn)

        self.min_button = _WinButton("min")
        self.min_button.setToolTip("最小化")
        self.min_button.clicked.connect(self.showMinimized)
        self.max_button = _WinButton("max")
        self.max_button.setToolTip("最大化 / 还原")
        self.max_button.clicked.connect(self._toggle_max)
        self.close_button = _WinButton("close")
        self.close_button.setToolTip("关闭")
        self.close_button.clicked.connect(self.close)
        for b in (self.min_button, self.max_button, self.close_button):
            lay.addWidget(b)

        self._titlebar = bar
        self._drag_pos: QPoint | None = None
        self._max_drag_origin: QPoint | None = None
        self._max_drag_ratio: float = 0.5
        return bar

    def _toggle_max(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        if hasattr(self, "max_button"):
            self.max_button.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_responsive_chrome()
        if hasattr(self, "_toast"):
            self._toast.reposition()
        if hasattr(self, "max_button"):
            self.max_button.update()

    def _in_drag_zone(self, widget) -> bool:
        return (
            widget is self._titlebar
            or (
                widget is not None
                and widget.parent() is self._titlebar
                and not isinstance(widget, QPushButton)
            )
        )

    def mousePressEvent(self, event):
        """标题栏区域拖拽移动窗口（支持最大化状态下往下拖拽自动还原跟手移动）。"""
        if event.button() == Qt.MouseButton.LeftButton:
            child = self.childAt(event.position().toPoint())
            if self._in_drag_zone(child):
                if self.isMaximized():
                    self._max_drag_origin = event.globalPosition().toPoint()
                    self._max_drag_ratio = event.position().x() / max(1, self.width())
                else:
                    self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                return
        self._drag_pos = None
        self._max_drag_origin = None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            if self._max_drag_origin is not None:
                delta = event.globalPosition().toPoint() - self._max_drag_origin
                if delta.manhattanLength() > 6:
                    cursor_global = event.globalPosition().toPoint()
                    ratio = self._max_drag_ratio
                    self._max_drag_origin = None
                    self.showNormal()
                    new_x = int(cursor_global.x() - self.width() * ratio)
                    new_y = max(0, int(cursor_global.y() - self._titlebar.height() // 2))
                    self.move(new_x, new_y)
                    self._drag_pos = cursor_global - self.frameGeometry().topLeft()
                return
            if self._drag_pos is not None:
                self.move(event.globalPosition().toPoint() - self._drag_pos)
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        self._max_drag_origin = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        child = self.childAt(event.position().toPoint())
        if self._in_drag_zone(child):
            self._toggle_max()
            return
        super().mouseDoubleClickEvent(event)

    def _hit_test_pos(self, msg):
        w, h = self.width(), self.height()
        tolerance = 8

        def _in_range(p):
            return -tolerance <= p.x() <= w + tolerance and -tolerance <= p.y() <= h + tolerance

        try:
            pos = self.mapFromGlobal(QCursor.pos())
            if _in_range(pos):
                return pos
        except Exception:
            pass

        try:
            x = ctypes.c_short(msg.lParam & 0xFFFF).value
            y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
            screen = self.screen()
            ratio = (
                screen.devicePixelRatio() if screen is not None else self.devicePixelRatioF()
            ) or 1.0
            pos = self.mapFromGlobal(QPoint(int(x / ratio), int(y / ratio)))
            if _in_range(pos):
                return pos
        except Exception:
            pass

        return None

    def nativeEvent(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            msg = ctypes.wintypes.MSG.from_address(int(message))
            if msg.message == 0x0084:  # WM_NCHITTEST
                pos = self._hit_test_pos(msg)
                if pos is None:
                    return super().nativeEvent(event_type, message)
                w, h, m = self.width(), self.height(), 6
                if not self.isMaximized():
                    top, bottom = pos.y() <= m, pos.y() >= h - m
                    left, right = pos.x() <= m, pos.x() >= w - m
                    if top and left:
                        return True, 13
                    if top and right:
                        return True, 14
                    if bottom and left:
                        return True, 16
                    if bottom and right:
                        return True, 17
                    if top:
                        return True, 12
                    if bottom:
                        return True, 15
                    if left:
                        return True, 10
                    if right:
                        return True, 11
                if pos.y() <= self._titlebar.height():
                    child = self.childAt(pos)
                    if self._in_drag_zone(child):
                        return True, 2  # HTCAPTION
        return super().nativeEvent(event_type, message)

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, "_dwm_applied", False):
            self._dwm_applied = True
            try:
                _sync_dwm_theme(int(self.winId()), get_theme() == "dark")
            except Exception:
                pass

    def open_add_product(
        self,
        paths: list[str] | None = None,
        default_series: str | None = None,
        switch_to_page: bool = True,
    ) -> list:
        """从启动台、标题栏或任意页面调用《添加产品入库》对话框；入库后可自动跳转到产品库查看。"""
        added = self.product_page.open_add_product_dialog(
            initial_paths=paths, default_series=default_series
        )
        if added:
            self._on_library_scan_completed()
            if switch_to_page:
                prod_idx = next((i for i, (k, _l) in enumerate(NAV_ITEMS) if k == "bottle"), 2)
                self.switch_page(prod_idx)
        return added

    def open_psd_tool(self, paths: list[str] | None = None) -> None:
        index = next((i for i, (key, _label) in enumerate(NAV_ITEMS) if key == "layers"), None)
        if index is None:
            return
        self.switch_page(index)
        if paths:
            self.psd_tool_page.add_files(paths)

    def switch_page(self, index: int):
        self._current_page_index = index
        self.pages.setCurrentIndex(index)
        for number, button in enumerate(self.nav_buttons):
            button.setChecked(number == index)
        self._refresh_nav_icons()

        current = self.pages.widget(index)
        if isinstance(current, HomePage):
            current.refresh()
            self.refresh_drive_widget()
        elif isinstance(current, TaskPage):
            current.refresh()
        elif isinstance(current, TodoPage):
            current._refresh()
        elif isinstance(current, ReportPage):
            current.refresh_on_show()
        elif isinstance(current, SettingsPage):
            current.refresh()
            self.refresh_drive_widget()

        widget = self.pages.currentWidget()
        animate_fade_in(widget, duration=165)

    def closeEvent(self, event):
        """窗口关闭时优雅清理：停止定时器、隐藏托盘、清空线程池等待队列、停止后台 QThread 并关闭数据库连接。"""
        try:
            if hasattr(self, "_remind_timer"):
                self._remind_timer.stop()
            if hasattr(self, "tray"):
                self.tray.hide()
            for pool in (
                getattr(self, "thread_pool", None),
                getattr(getattr(self, "file_page", None), "_preview_pool", None),
                getattr(getattr(self, "product_page", None), "_image_pool", None),
                getattr(getattr(self, "product_page", None), "_render_pool", None),
                getattr(getattr(self, "product_page", None), "_scan_pool", None),
            ):
                if pool is not None:
                    pool.clear()
            for page, attr in (
                (getattr(self, "psd_tool_page", None), "analyze_thread"),
                (getattr(self, "psd_tool_page", None), "export_thread"),
                (getattr(self, "psd_text_page", None), "scan_thread"),
                (getattr(self, "psd_text_page", None), "replace_thread"),
                (getattr(self, "cleaner_page", None), "worker"),
            ):
                thread = getattr(page, attr, None) if page is not None else None
                if thread is not None and thread.isRunning():
                    if hasattr(thread, "cancel"):
                        thread.cancel()
                    thread.wait(400)
            self.repository.close()
        except Exception:
            pass
        super().closeEvent(event)
