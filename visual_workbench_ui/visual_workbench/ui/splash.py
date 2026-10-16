# -*- coding: utf-8 -*-
"""高定无边框程序启动动画屏（StartupSplash）。

性能与体验原则：
1. 静态极光底图（Aurora Mesh + 精细网格 + 玻璃高光边框）在初始化时一次性预渲染到 QPixmap 缓存，
   动画帧只重绘「旋转流光环 + 3px 流光进度条 + 阶段文字」，CPU 占用趋近于 0。
2. 总时长约 1.15 秒（干练不拖沓），支持鼠标点击或按 Esc/Space 立即跳过，
   并在结束时与主窗口做无缝交叉淡入（Cross-Fade Reveal）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import (
    QEasingCurve, QPointF, QPropertyAnimation, QRectF, Qt, QTimer, QVariantAnimation,
)
from PySide6.QtGui import (
    QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient,
)
from PySide6.QtWidgets import QApplication, QGraphicsOpacityEffect, QWidget

_RES = Path(__file__).resolve().parent / "resources"
_LOGO_PATH = (_RES / "logo.png").as_posix()

SPLASH_STEPS = (
    (0.18, "正在连接本地 SQLite 高速存储引擎…"),
    (0.46, "正在挂载视觉资产与免抠产品库索引…"),
    (0.76, "正在校验 Photoshop 脚本通道与工作区…"),
    (1.00, "视觉工作台已就绪"),
)


class StartupSplash(QWidget):
    """电影感轻量流光启动屏。"""

    WIDTH = 520
    HEIGHT = 310

    def __init__(self, on_finished: Optional[Callable[[], None]] = None):
        super().__init__(None)
        self._on_finished = on_finished
        self._progress = 0.0
        self._angle = 0.0
        self._step_text = SPLASH_STEPS[0][1]
        self._closing = False

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.SplashScreen
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._center_on_screen()

        self._bg_cache = self._render_static_backdrop()
        self._logo = self._load_logo()

        # 进度推进动画（0.0 -> 1.0，总长 1050ms，OutCubic 缓动先快后稳）
        self._prog_anim = QVariantAnimation(self)
        self._prog_anim.setStartValue(0.0)
        self._prog_anim.setEndValue(1.0)
        self._prog_anim.setDuration(1050)
        self._prog_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._prog_anim.valueChanged.connect(self._on_progress_tick)
        self._prog_anim.finished.connect(self._begin_fade_out)

    def _center_on_screen(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            self.move(
                geo.x() + (geo.width() - self.WIDTH) // 2,
                geo.y() + (geo.height() - self.HEIGHT) // 2,
            )

    def _load_logo(self) -> Optional[QPixmap]:
        if Path(_LOGO_PATH).exists():
            pm = QPixmap(_LOGO_PATH)
            if not pm.isNull():
                return pm.scaled(
                    44, 44,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
        return None

    def _render_static_backdrop(self) -> QPixmap:
        """一次性预渲染深空极光背景、微网格与高光内描边，避免动画期间每帧重算渐变。"""
        pm = QPixmap(self.WIDTH, self.HEIGHT)
        pm.fill(Qt.GlobalColor.transparent)

        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(8, 8, self.WIDTH - 16, self.HEIGHT - 16)
        radius = 22.0

        # 外围柔和环境阴影（分 3 层半透明圆角框叠加，比 QGraphicsDropShadowEffect 快 10 倍）
        for spread, alpha in ((6, 18), (3, 32), (1, 50)):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(4, 7, 15, alpha))
            p.drawRoundedRect(rect.adjusted(-spread, -spread + 2, spread, spread + 3), radius + spread, radius + spread)

        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        p.save()
        p.setClipPath(path)

        # 1. 深空曜石底色
        bg_grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
        bg_grad.setColorAt(0.0, QColor("#0B0F1A"))
        bg_grad.setColorAt(0.55, QColor("#101626"))
        bg_grad.setColorAt(1.0, QColor("#0D1220"))
        p.fillRect(rect, bg_grad)

        # 2. 右上电光蓝极光光斑 + 左下翡翠青光斑
        orb1 = QRadialGradient(QPointF(rect.right() - 70, rect.top() + 50), 210)
        orb1.setColorAt(0.0, QColor(37, 99, 235, 95))
        orb1.setColorAt(0.5, QColor(99, 102, 241, 38))
        orb1.setColorAt(1.0, QColor(37, 99, 235, 0))
        p.fillRect(rect, orb1)

        orb2 = QRadialGradient(QPointF(rect.left() + 80, rect.bottom() - 40), 180)
        orb2.setColorAt(0.0, QColor(16, 185, 129, 55))
        orb2.setColorAt(1.0, QColor(16, 185, 129, 0))
        p.fillRect(rect, orb2)

        # 3. 极细科技感坐标网格线
        grid_pen = QPen(QColor(255, 255, 255, 7), 1.0)
        p.setPen(grid_pen)
        for x in range(int(rect.left()), int(rect.right()), 32):
            p.drawLine(x, int(rect.top()), x, int(rect.bottom()))
        for y in range(int(rect.top()), int(rect.bottom()), 32):
            p.drawLine(int(rect.left()), y, int(rect.right()), y)

        # 4. 静态文案与品牌标识
        # 右上角 PRO 胶囊
        pill_rect = QRectF(rect.right() - 106, rect.top() + 22, 82, 24)
        p.setPen(QPen(QColor(96, 165, 250, 90), 1.0))
        p.setBrush(QColor(37, 99, 235, 42))
        p.drawRoundedRect(pill_rect, 12, 12)
        f_pill = QFont("Segoe UI", 8)
        f_pill.setPixelSize(10)
        f_pill.setBold(True)
        p.setFont(f_pill)
        p.setPen(QColor("#93C5FD"))
        p.drawText(pill_rect, Qt.AlignmentFlag.AlignCenter, "STUDIO v1.3")

        # 主标题与副标题
        f_title = QFont("Microsoft YaHei UI", 16)
        f_title.setPixelSize(23)
        f_title.setBold(True)
        p.setFont(f_title)
        p.setPen(QColor("#F8FAFC"))
        p.drawText(
            QRectF(rect.left() + 36, rect.top() + 124, rect.width() - 72, 32),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "电商视觉工作台",
        )

        f_sub = QFont("Microsoft YaHei UI", 10)
        f_sub.setPixelSize(12)
        p.setFont(f_sub)
        p.setPen(QColor(203, 213, 225, 175))
        p.drawText(
            QRectF(rect.left() + 36, rect.top() + 158, rect.width() - 72, 22),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "免抠产品库  ·  AI 智能归档  ·  PSD 批量工厂  ·  工单效能中心",
        )
        p.restore()

        # 5. 玻璃质感双层外框（整体柔光外框 + 顶部 1px 金属高光反射）
        border_grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        border_grad.setColorAt(0.0, QColor(255, 255, 255, 68))
        border_grad.setColorAt(0.4, QColor(255, 255, 255, 24))
        border_grad.setColorAt(1.0, QColor(59, 130, 246, 45))
        p.setPen(QPen(border_grad, 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, radius, radius)

        p.end()
        return pm

    def start(self) -> None:
        self.show()
        self.raise_()
        self._prog_anim.start()

    def _on_progress_tick(self, value) -> None:
        val = float(value)
        self._progress = val
        self._angle = (val * 540.0) % 360.0
        for threshold, text in SPLASH_STEPS:
            if val <= threshold:
                self._step_text = text
                break
        self.update()

    def _begin_fade_out(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._prog_anim.stop()
        if self._on_finished is not None:
            cb = self._on_finished
            self._on_finished = None
            cb()

        effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(effect)
        fade = QPropertyAnimation(effect, b"opacity", self)
        fade.setDuration(220)
        fade.setStartValue(1.0)
        fade.setEndValue(0.0)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        fade.finished.connect(self.close)
        self._fade_anim = fade
        fade.start()

    def mousePressEvent(self, event):
        self._begin_fade_out()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Space, Qt.Key.Key_Return):
            self._begin_fade_out()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        # 1. 绘制缓存的静态深空极光背景（O(1) 零开销）
        p.drawPixmap(0, 0, self._bg_cache)

        rect = QRectF(8, 8, self.WIDTH - 16, self.HEIGHT - 16)

        # 2. 绘制左上角 Logo 座舱与流光旋转外环
        badge_rect = QRectF(rect.left() + 36, rect.top() + 34, 64, 64)
        p.setPen(QPen(QColor(255, 255, 255, 25), 1.0))
        p.setBrush(QColor(15, 23, 42, 190))
        p.drawRoundedRect(badge_rect, 18, 18)

        # 动态流光弧线
        arc_pen = QPen(QColor("#60A5FA"), 2.2)
        arc_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(arc_pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(badge_rect.adjusted(-3, -3, 3, 3), int(-self._angle * 16), 110 * 16)

        if self._logo is not None:
            lx = int(badge_rect.center().x() - self._logo.width() / 2)
            ly = int(badge_rect.center().y() - self._logo.height() / 2)
            p.drawPixmap(lx, ly, self._logo)
        else:
            f_logo = QFont("Microsoft YaHei UI", 16)
            f_logo.setPixelSize(22)
            f_logo.setBold(True)
            p.setFont(f_logo)
            p.setPen(QColor("#60A5FA"))
            p.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, "视")

        # 3. 底部加载阶段文案与百分比
        bar_left = rect.left() + 36
        bar_width = rect.width() - 72
        text_y = rect.bottom() - 62

        f_step = QFont("Microsoft YaHei UI", 9)
        f_step.setPixelSize(11)
        p.setFont(f_step)
        p.setPen(QColor(148, 163, 184, 210))
        p.drawText(
            QRectF(bar_left, text_y, bar_width - 60, 18),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self._step_text,
        )

        f_pct = QFont("Consolas", 9)
        f_pct.setPixelSize(11)
        f_pct.setBold(True)
        p.setFont(f_pct)
        p.setPen(QColor("#60A5FA"))
        p.drawText(
            QRectF(bar_left, text_y, bar_width, 18),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            f"{int(round(self._progress * 100))}%",
        )

        # 4. 3.5px 流光渐变进度条
        track_rect = QRectF(bar_left, rect.bottom() - 34, bar_width, 4.0)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 22))
        p.drawRoundedRect(track_rect, 2.0, 2.0)

        fill_w = max(6.0, bar_width * self._progress)
        fill_rect = QRectF(bar_left, rect.bottom() - 34, fill_w, 4.0)
        bar_grad = QLinearGradient(fill_rect.topLeft(), fill_rect.topRight())
        bar_grad.setColorAt(0.0, QColor("#2563EB"))
        bar_grad.setColorAt(0.6, QColor("#3B82F6"))
        bar_grad.setColorAt(1.0, QColor("#34D399"))
        p.setBrush(bar_grad)
        p.drawRoundedRect(fill_rect, 2.0, 2.0)

        p.end()
