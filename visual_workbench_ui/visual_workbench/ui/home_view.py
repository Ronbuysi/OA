# -*- coding: utf-8 -*-
"""工作台首页（启动台）：缓存式极光 Hero + 快捷启动台磁贴（支持一键/拖拽添加产品入库）+ 高定数据卡 + 最近更新。

性能保障（零卡顿设计）：
- `_AuroraHero` 的多层径向极光与科技网格仅在 `resizeEvent` 或 `refresh_theme` 时渲染到离屏 `QPixmap`，
  日常滚动与悬停重绘耗时 < 0.1ms，CPU 零负担。
- `_LaunchpadTile` 与 `_StatCard` 全部采用轻量级 QPainter 单次路径绘制，不使用任何 QGraphicsDropShadowEffect。
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient,
)
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from ..repository import Repository
from .icons import icon as painted_icon, icon_pixmap, to_qcolor
from .layout import page_body
from . import styles as S

_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
_PSD_EXTS = (".psd", ".psb")


class _AuroraHero(QFrame):
    """缓存式极光 Hero 横幅：只在尺寸变化或主题切换时合成一次背景位图，平时 O(1) 贴图。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Hero")
        self._bg_cache: QPixmap | None = None

    def invalidate_cache(self) -> None:
        self._bg_cache = None
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._bg_cache = None

    def _build_cache(self) -> QPixmap:
        w, h = max(1, self.width()), max(1, self.height())
        pm = QPixmap(w, h)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0.5, 0.5, w - 1.0, h - 1.0)
        radius = 20.0
        dark = S.get_theme() == "dark"

        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        p.save()
        p.setClipPath(path)

        # 基底渐变
        bg = QLinearGradient(rect.topLeft(), rect.bottomRight())
        if dark:
            bg.setColorAt(0.0, QColor("#141926"))
            bg.setColorAt(0.6, QColor("#121722"))
            bg.setColorAt(1.0, QColor("#152036"))
        else:
            bg.setColorAt(0.0, QColor("#FFFFFF"))
            bg.setColorAt(0.65, QColor("#F8FAFF"))
            bg.setColorAt(1.0, QColor("#EEF4FF"))
        p.fillRect(rect, bg)

        # 右侧电光蓝极光晕染 + 中部翡翠绿柔光
        orb1 = QRadialGradient(QPointF(w - 90, 28), 260)
        orb1.setColorAt(0.0, QColor(37, 99, 235, 52 if dark else 34))
        orb1.setColorAt(0.6, QColor(99, 102, 241, 22 if dark else 14))
        orb1.setColorAt(1.0, QColor(37, 99, 235, 0))
        p.fillRect(rect, orb1)

        orb2 = QRadialGradient(QPointF(w * 0.55, h + 10), 200)
        orb2.setColorAt(0.0, QColor(16, 185, 129, 32 if dark else 20))
        orb2.setColorAt(1.0, QColor(16, 185, 129, 0))
        p.fillRect(rect, orb2)

        # 右侧极淡几何装饰网格
        grid_pen = QPen(QColor(255, 255, 255, 8) if dark else QColor(37, 99, 235, 10), 1.0)
        p.setPen(grid_pen)
        for x in range(int(w * 0.55), w, 28):
            p.drawLine(x, 0, x, h)
        for y in range(0, h, 28):
            p.drawLine(int(w * 0.55), y, w, y)
        p.restore()

        # 外框与顶部 1px 受光高光
        p.setPen(QPen(to_qcolor(S._BORDER), 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, radius, radius)

        hi_alpha = 40 if dark else 200
        p.setPen(QPen(QColor(255, 255, 255, hi_alpha), 1.0))
        p.drawLine(int(radius), 1, int(w - radius), 1)
        p.end()
        return pm

    def paintEvent(self, event):
        if self._bg_cache is None or self._bg_cache.size() != self.size():
            self._bg_cache = self._build_cache()
        p = QPainter(self)
        p.drawPixmap(0, 0, self._bg_cache)
        p.end()


class _LaunchpadTile(QFrame):
    """启动台快捷动作磁贴：支持点击触发，也支持把文件直接拖到磁贴上触发（零阴影纯自绘）。"""

    clicked = Signal()
    filesDropped = Signal(list)

    def __init__(
        self,
        icon_name: str,
        title: str,
        subtitle: str,
        accent_hex: str,
        badge_text: str = "",
        accept_drops: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("LaunchpadCard")
        self.icon_name = icon_name
        self.accent_hex = accent_hex
        self._hover = False
        self._drag_over = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAcceptDrops(accept_drops)
        self.setMinimumHeight(86)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 13, 16, 13)
        lay.setSpacing(12)

        self.icon_box = QLabel()
        self.icon_box.setFixedSize(40, 40)
        self.icon_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.icon_box)

        text_col = QVBoxLayout()
        text_col.setSpacing(3)
        top_row = QHBoxLayout()
        top_row.setSpacing(6)
        self.title_lbl = QLabel(title)
        self.title_lbl.setObjectName("SubSectionTitle")
        top_row.addWidget(self.title_lbl)
        if badge_text:
            self.badge_lbl = QLabel(badge_text)
            top_row.addWidget(self.badge_lbl)
        else:
            self.badge_lbl = None
        top_row.addStretch()
        text_col.addLayout(top_row)

        self.sub_lbl = QLabel(subtitle)
        self.sub_lbl.setObjectName("HintLabel")
        self.sub_lbl.setWordWrap(True)
        text_col.addWidget(self.sub_lbl)
        lay.addLayout(text_col, 1)

        self.refresh_theme()

    def refresh_theme(self) -> None:
        dark = S.get_theme() == "dark"
        c = QColor(self.accent_hex)
        bg_alpha = 42 if dark else 24
        self.icon_box.setPixmap(icon_pixmap(self.icon_name, c, 20))
        self.icon_box.setStyleSheet(
            f"background: rgba({c.red()},{c.green()},{c.blue()},{bg_alpha});"
            f"border: 1px solid rgba({c.red()},{c.green()},{c.blue()},65);"
            "border-radius: 11px;"
        )
        if self.badge_lbl is not None:
            self.badge_lbl.setStyleSheet(
                f"color: {c.name()};"
                f"background: rgba({c.red()},{c.green()},{c.blue()},{bg_alpha});"
                "border-radius: 6px; padding: 1px 6px; font-size: 10px; font-weight: 700;"
            )
        self.update()

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            return
        super().mousePressEvent(event)

    def dragEnterEvent(self, event):
        if self.acceptDrops() and event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._drag_over = True
            self.update()

    def dragLeaveEvent(self, event):
        self._drag_over = False
        self.update()

    def dropEvent(self, event):
        self._drag_over = False
        self.update()
        if event.mimeData().hasUrls():
            paths = [u.toLocalFile() for u in event.mimeData().urls() if u.toLocalFile()]
            if paths:
                event.acceptProposedAction()
                self.filesDropped.emit(paths)

    def paintEvent(self, event):
        super().paintEvent(event)
        if not (self._hover or self._drag_over):
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QColor(self.accent_hex)
        rect = QRectF(1.0, 1.0, self.width() - 2.0, self.height() - 2.0)
        pen = QPen(c, 1.5 if self._drag_over else 1.2)
        if self._drag_over:
            pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(QColor(c.red(), c.green(), c.blue(), 18 if self._drag_over else 8))
        p.drawRoundedRect(rect, 13.5, 13.5)
        p.end()


def _stat_card(title: str, icon_name: str = "library", color_hex: str = "#2563EB", accent: bool = False) -> QFrame:
    """高定数据卡：左上方小字标签 + 右上方柔和彩色图标徽章，下方大号数字。"""
    card = QFrame()
    card.setObjectName("Surface")
    lay = QVBoxLayout(card)
    lay.setContentsMargins(18, 14, 18, 15)
    lay.setSpacing(6)

    top = QHBoxLayout()
    top.setSpacing(8)
    label = QLabel(title)
    label.setObjectName("Caption")
    top.addWidget(label)
    top.addStretch()

    icon_badge = QLabel()
    icon_badge.setFixedSize(26, 26)
    icon_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
    top.addWidget(icon_badge)
    lay.addLayout(top)

    value = QLabel("—")
    value.setObjectName("StatValue")
    value.setProperty("accent", "true" if accent else "false")
    lay.addWidget(value)

    card._value_label = value
    card._title_label = label
    card._icon_badge = icon_badge
    card._icon_name = icon_name
    card._color_hex = color_hex
    card._accent = accent
    refresh_stat_card_style(card)
    return card


def refresh_stat_card_style(card: QFrame) -> None:
    """按当前主题刷新数据卡数字与彩色图标徽章。"""
    from .styles import _ACCENT, _TEXT_PRIMARY
    color = _ACCENT if getattr(card, "_accent", False) else _TEXT_PRIMARY
    card._value_label.setStyleSheet(
        f"font-size:26px;font-weight:700;color:{color};"
        "background:transparent;border:0;letter-spacing:-0.02em;"
    )
    icon_badge = getattr(card, "_icon_badge", None)
    if icon_badge is not None:
        c = QColor(getattr(card, "_color_hex", "#2563EB"))
        dark = S.get_theme() == "dark"
        alpha = 40 if dark else 22
        icon_badge.setPixmap(icon_pixmap(getattr(card, "_icon_name", "library"), c, 14))
        icon_badge.setStyleSheet(
            f"background: rgba({c.red()},{c.green()},{c.blue()},{alpha});"
            "border-radius: 8px; border: 0;"
        )


class HomePage(QWidget):
    """AI 视觉生产力工作台 · 首页（启动台）。"""

    def __init__(self, repository: Repository, parent=None):
        super().__init__(parent)
        self.repository = repository
        self._compact_layout: bool | None = None
        self._build_ui()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_launchpad_tiles"):
            self._apply_responsive_layout()

    def _apply_responsive_layout(self):
        compact = self.width() < 980
        if compact == self._compact_layout:
            return
        self._compact_layout = compact
        self.launch_sub.setVisible(not compact)
        self.ai_label.setVisible(not compact)
        self._recent_add_button.setVisible(not compact)

        self._hero_layout.removeItem(self._hero_right)
        self._hero_layout.addLayout(self._hero_right, 1 if compact else 0,
                                    0 if compact else 1)
        for layout, widgets, columns in (
            (self._launch_grid, self._launchpad_tiles, 2 if compact else 4),
            (self._stats_grid, self._stat_cards, 3 if compact else 5),
        ):
            while layout.count():
                layout.takeAt(0)
            for index, widget in enumerate(widgets):
                layout.addWidget(widget, index // columns, index % columns)

    def _build_ui(self):
        root = page_body(self, spacing=20, margins=(32, 24, 32, 24), scrollable=True)

        # ── 1. 极光 Hero：问候语 + 快捷添加产品主按钮 ──
        self.hero = _AuroraHero()
        hero_lay = QGridLayout(self.hero)
        self._hero_layout = hero_lay
        hero_lay.setContentsMargins(28, 22, 28, 22)
        left = QVBoxLayout()
        left.setSpacing(6)
        self.greeting = QLabel()
        self.greeting.setObjectName("HeroGreeting")
        self.date_line = QLabel()
        self.date_line.setObjectName("PageSubtitle")
        self.date_line.setWordWrap(True)
        left.addWidget(self.greeting)
        left.addWidget(self.date_line)
        hero_lay.addLayout(left, 0, 0)
        hero_lay.setColumnStretch(0, 1)

        right_col = QVBoxLayout()
        right_col.setSpacing(10)
        right_col.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.ai_label = QLabel("✦  设计工作流")
        self.ai_label.setObjectName("StatusPill")
        right_col.addWidget(self.ai_label, alignment=Qt.AlignmentFlag.AlignRight)

        hero_actions = QHBoxLayout()
        hero_actions.setSpacing(8)
        self.hero_add_product_btn = QPushButton("  添加产品入库")
        self.hero_add_product_btn.setObjectName("PrimaryButton")
        self.hero_add_product_btn.setIcon(painted_icon("plus", "#FFFFFF", 14))
        self.hero_add_product_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.hero_add_product_btn.setFixedHeight(34)
        self.hero_add_product_btn.setToolTip("直接向产品库添加免抠 PNG / 产品图片（可新建产品系列）")
        self.hero_add_product_btn.clicked.connect(lambda: self._open_add_product())
        hero_actions.addWidget(self.hero_add_product_btn)

        self.hero_browse_product_btn = QPushButton("进入产品库")
        self.hero_browse_product_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.hero_browse_product_btn.setFixedHeight(34)
        self.hero_browse_product_btn.clicked.connect(lambda: self._switch_to(2))
        hero_actions.addWidget(self.hero_browse_product_btn)

        right_col.addLayout(hero_actions)
        hero_lay.addLayout(right_col, 0, 1)
        self._hero_right = right_col
        root.addWidget(self.hero)

        # ── 2. 快捷启动台（Quick Launchpad）：一键直达 & 拖拽入库 ──
        launch_head = QHBoxLayout()
        launch_title = QLabel("快捷启动台")
        launch_title.setObjectName("SectionTitle")
        launch_head.addWidget(launch_title)
        launch_sub = QLabel("点击卡片即刻启动对应工作流 · 支持直接将产品图片拖入第一张卡片快速入库")
        launch_sub.setObjectName("Caption")
        self.launch_sub = launch_sub
        launch_head.addWidget(launch_sub)
        launch_head.addStretch()
        root.addLayout(launch_head)

        launch_grid = QGridLayout()
        self._launch_grid = launch_grid
        launch_grid.setSpacing(12)

        self.tile_add_product = _LaunchpadTile(
            "upload",
            "添加产品入库",
            "点击选图或把 PNG 直接拖到这里，归入指定系列",
            "#059669",
            badge_text="支持拖入",
            accept_drops=True,
        )
        self.tile_add_product.clicked.connect(lambda: self._open_add_product())
        self.tile_add_product.filesDropped.connect(self._on_product_files_dropped)

        self.tile_archive = _LaunchpadTile(
            "archive",
            "AI 智能归档",
            "扫描桌面/下载散图，按年月与分类规则自动归位",
            "#2563EB",
        )
        self.tile_archive.clicked.connect(lambda: self._switch_to(3))

        self.tile_psd_export = _LaunchpadTile(
            "layers",
            "PSD 分组切图",
            "识别最外层分组与公共底，批量导出透明 PNG",
            "#7C3AED",
            badge_text="批量",
            accept_drops=True,
        )
        self.tile_psd_export.clicked.connect(lambda: self._switch_to(8))
        self.tile_psd_export.filesDropped.connect(self._on_psd_files_dropped)

        self.tile_cleaner = _LaunchpadTile(
            "cleaner",
            "工单符号清洗",
            "一键清除 Excel 单元格非法字符与零宽脏字符",
            "#D97706",
        )
        self.tile_cleaner.clicked.connect(lambda: self._switch_to(7))

        self._launchpad_tiles = (
            self.tile_add_product,
            self.tile_archive,
            self.tile_psd_export,
            self.tile_cleaner,
        )
        for index, tile in enumerate(self._launchpad_tiles):
            launch_grid.addWidget(tile, 0, index)
        root.addLayout(launch_grid)

        # ── 3. 核心资产与产出数据卡行 ──
        stats_row = QGridLayout()
        self._stats_grid = stats_row
        stats_row.setSpacing(12)
        self.card_total = _stat_card("设计素材总量", "library", "#2563EB")
        self.card_psd = _stat_card("PSD 源文件", "layers", "#7C3AED")
        self.card_image = _stat_card("图片资产", "image", "#059669")
        self.card_month = _stat_card("本月出图", "bolt", "#D97706")
        self.card_task = _stat_card("今日完成任务", "check_circle", "#2563EB", accent=True)
        self._stat_cards = (
            self.card_total, self.card_psd, self.card_image,
            self.card_month, self.card_task,
        )
        for index, card in enumerate(self._stat_cards):
            stats_row.addWidget(card, 0, index)
        root.addLayout(stats_row)

        # ── 4. 最近更新 ──
        recent_head = QHBoxLayout()
        self.recent_title = QLabel("最近更新")
        self.recent_title.setObjectName("SectionTitle")
        recent_head.addWidget(self.recent_title)
        recent_head.addStretch()

        add_prod_link = QPushButton("＋ 添加产品")
        add_prod_link.setObjectName("GhostAccentButton")
        add_prod_link.setCursor(Qt.CursorShape.PointingHandCursor)
        add_prod_link.clicked.connect(lambda: self._open_add_product())
        self._recent_add_button = add_prod_link
        recent_head.addWidget(add_prod_link)

        jump_button = QPushButton("进入资产库")
        jump_button.setCursor(Qt.CursorShape.PointingHandCursor)
        jump_button.clicked.connect(self._go_library)
        recent_head.addWidget(jump_button)
        root.addLayout(recent_head)

        self.recent_holder = QVBoxLayout()
        self.recent_holder.setSpacing(12)
        root.addLayout(self.recent_holder, 1)
        self._style_hero()
        self.refresh()
        self._apply_responsive_layout()

    def _switch_to(self, index: int):
        window = self.window()
        if hasattr(window, "switch_page"):
            window.switch_page(index)

    def _go_library(self):
        self._switch_to(1)

    def _open_add_product(self, paths: list[str] | None = None):
        """从启动台直接调起《添加产品入库》对话框；入库成功后可自动跳转到产品库查看。"""
        window = self.window()
        if hasattr(window, "open_add_product"):
            window.open_add_product(paths=paths, switch_to_page=True)
            return
        product_page = getattr(window, "product_page", None)
        if product_page is not None and hasattr(product_page, "open_add_product_dialog"):
            added = product_page.open_add_product_dialog(initial_paths=paths)
            if added:
                self.refresh()
                self._switch_to(2)

    def _on_product_files_dropped(self, paths: list[str]):
        valid = [
            p for p in paths
            if Path(p).is_dir() or Path(p).suffix.lower() in _IMAGE_EXTS
        ]
        if valid:
            self._open_add_product(valid)

    def _on_psd_files_dropped(self, paths: list[str]):
        psd_files = [p for p in paths if Path(p).suffix.lower() in _PSD_EXTS]
        window = self.window()
        if psd_files and hasattr(window, "open_psd_tool"):
            window.open_psd_tool(psd_files)

    def refresh_theme(self):
        """刷新主题相关的样式。"""
        self.hero.invalidate_cache()
        for tile in self._launchpad_tiles:
            tile.refresh_theme()
        for card in (
            self.card_total, self.card_psd, self.card_image,
            self.card_month, self.card_task,
        ):
            refresh_stat_card_style(card)
        self._style_hero()
        self.refresh()

    def _style_hero(self):
        from .styles import _TEXT_PRIMARY
        self.greeting.setStyleSheet(
            f"font-size:25px;font-weight:700;color:{_TEXT_PRIMARY};"
            "background:transparent;letter-spacing:-0.02em;"
        )

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            sub = item.layout()
            if sub is not None:
                self._clear_layout(sub)
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()

    def _populate_recent(self):
        from .file_library import FileCard, PreviewWorker

        self._clear_layout(self.recent_holder)
        self._recent_cards: dict[int, FileCard] = {}
        recent = self.repository.list_files(limit=8)
        if not recent:
            panel = QFrame()
            panel.setObjectName("EmptyState")
            panel_lay = QVBoxLayout(panel)
            panel_lay.setContentsMargins(24, 32, 24, 32)
            panel_lay.setSpacing(6)
            title = QLabel("还没有索引到素材")
            title.setObjectName("CardTitle")
            title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hint = QLabel("点击上方「添加产品入库」直接导入产品图，或前往「资产库」选择设计目录扫描")
            hint.setObjectName("Caption")
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            panel_lay.addWidget(title)
            panel_lay.addWidget(hint)
            self.recent_holder.addWidget(panel)
            self.recent_holder.addStretch()
            return
        grid = QGridLayout()
        grid.setSpacing(12)
        win = self.window()
        pool = getattr(win, "thread_pool", None)
        for i, file in enumerate(recent):
            card = FileCard(file)
            card.setMaximumWidth(9999)
            card.installEventFilter(self)
            grid.addWidget(card, i // 4, i % 4)
            if file.id is not None:
                self._recent_cards[file.id] = card
                if file.preview_status == "pending" and pool is not None:
                    worker = PreviewWorker(file, self.repository, 0)
                    worker.signals.finished.connect(self._on_recent_preview_done)
                    pool.start(worker)
        self.recent_holder.addLayout(grid)
        self.recent_holder.addStretch()

    def _on_recent_preview_done(self, payload):
        from dataclasses import replace
        file_id, result, _epoch = payload
        card = getattr(self, "_recent_cards", {}).get(file_id)
        if card is not None:
            try:
                card.file = replace(card.file, preview_path=result.path, preview_status=result.status)
                card.set_preview(result.path)
            except RuntimeError:
                pass

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.MouseButtonDblClick:
            file = getattr(obj, "file", None)
            if file is not None:
                from ..preview import open_file
                open_file(file.absolute_path)
                return True
        return super().eventFilter(obj, event)

    def refresh(self):
        now = datetime.now()
        greet = (
            "早上好" if now.hour < 11 else
            "中午好" if now.hour < 13 else
            "下午好" if now.hour < 18 else "晚上好"
        )
        week = "一二三四五六日"[now.weekday()]
        self.greeting.setText(f"{greet}，视觉设计师")
        self.date_line.setText(
            f"{now.year} 年 {now.month} 月 {now.day} 日 · 星期{week}"
            "  ·  从启动台快速添加产品、批量切图或归档散图"
        )

        if hasattr(self.repository, "get_dashboard_stats"):
            stats = self.repository.get_dashboard_stats(now)
            total = stats["total"]
            psd = stats["psd"]
            img = stats["image"]
            month_images = stats["month_images"]
            month_all = stats["month_all"]
            done_today = stats["done_today"]
        else:
            with self.repository._connection() as db:
                total = db.execute(
                    "SELECT COUNT(*) n FROM files WHERE is_available=1"
                ).fetchone()["n"]
                psd = db.execute(
                    "SELECT COUNT(*) n FROM files WHERE is_available=1 AND category='psd'"
                ).fetchone()["n"]
                img = db.execute(
                    "SELECT COUNT(*) n FROM files WHERE is_available=1 AND category='image'"
                ).fetchone()["n"]
                month_start = datetime(now.year, now.month, 1).timestamp()
                month_images = db.execute(
                    "SELECT COUNT(*) n FROM files WHERE is_available=1"
                    " AND category='image' AND modified_at >= ?",
                    (month_start,),
                ).fetchone()["n"]
                month_all = db.execute(
                    "SELECT COUNT(*) n FROM files WHERE is_available=1 AND modified_at >= ?",
                    (month_start,),
                ).fetchone()["n"]
                done_today = db.execute(
                    "SELECT COUNT(*) n FROM tasks WHERE is_completed=1 AND completed_at=?",
                    (date.today().isoformat(),),
                ).fetchone()["n"]

        self.card_total._value_label.setText(f"{total:,}")
        self.card_psd._value_label.setText(f"{psd:,}")
        self.card_image._value_label.setText(f"{img:,}")
        self.card_month._value_label.setText(f"{month_images:,}")
        self.card_task._value_label.setText(str(done_today))
        self.card_month.setToolTip(
            f"本月保存过的输出图片（图片类素材）{month_images:,} 个\n"
            f"本月改动过的素材合计（含 PSD 等过程稿）{month_all:,} 个"
        )
        self._populate_recent()
