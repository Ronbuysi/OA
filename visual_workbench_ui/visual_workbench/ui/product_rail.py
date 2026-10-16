# -*- coding: utf-8 -*-
"""产品库的左侧系列栏（按设计稿重写）。

设计稿（`e_commerce_designer_workspace (2).tsx`）的要点：

· 头部：系列图标 + 「产品系列分类」+ 右侧「共 N 款」胶囊；下面一个带放大镜的搜索框；
  再下面一行**彩色图例**（劲酒 / 养生 / 滋补 / 本草 的圆点）。
· 树：根行「全部」用实心主色；组行有**展开箭头 + 彩色圆点 + 彩色文件夹图标**；
  子项缩进并带**彩色的垂直分支线与横向小钩**，选中时左侧有一条**颜色强调条**，
  整体是「浅色底 + 同色描边 + 同色文字」的配色逻辑。
· 底部：脉动绿点 + 「免抠透底保障」+ 「800×800」胶囊，第二行是路径与索引状态。

实现上没有用 QTreeWidget —— 设计里的彩色分支线、左侧强调条、圆点徽标用控件自绘更稳，
也不受 QSS 对 QTreeView 分支绘制的限制。
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from .icons import icon as painted_icon, to_qcolor
from . import styles as S

RAIL_WIDTH = 272

# 四个系列组 → 各自的颜色（对应设计稿的 THEME_COLORS）
GROUP_COLORS: dict[str, str] = {
    "保健酒系列": "emerald",
    "白酒系列": "blue",
    "品牌素材": "amber",
    "其他素材": "rose",
}
GROUP_ORDER = ("保健酒系列", "白酒系列", "品牌素材", "其他素材")

# 分组维度：同一批图可以按「系列」看，也可以按「容量」看（用户要的第二种归法）
DIMENSIONS: tuple[tuple[str, str], ...] = (("series", "按系列"), ("capacity", "按容量"))
DIMENSION_KEYS = tuple(key for key, _label in DIMENSIONS)

# 图例标签（设计稿里是四个圆点 + 简称）
LEGEND = (
    ("保健酒系列", "保健"),
    ("白酒系列", "白酒"),
    ("品牌素材", "品牌"),
    ("其他素材", "其他"),
)

# 每个维度自己的标题 / 搜索提示 / 图例
DIMENSION_TITLES = {"series": "产品系列分类", "capacity": "按容量分类"}
DIMENSION_PLACEHOLDERS = {
    "series": "搜索产品系列（如 养生1号 / 7号样）…",
    "capacity": "搜索容量或系列（如 125ml / 2.5L）…",
}
DIMENSION_LEGENDS: dict[str, tuple[tuple[str, str], ...]] = {
    "series": (("保健", "emerald"), ("白酒", "blue"), ("品牌", "amber"), ("其他", "rose")),
    "capacity": (("小规格", "emerald"), ("常规装", "blue"), ("大规格", "amber"), ("未标注", "rose")),
}

_PALETTES = {
    # key: (浅色 accent, 深色 accent, 浅色文字, 深色文字)
    "blue": ("#2563EB", "#60A5FA", "#1D4ED8", "#93C5FD"),
    "rose": ("#E11D48", "#FB7185", "#BE123C", "#FDA4AF"),
    "amber": ("#D97706", "#FBBF24", "#B45309", "#FCD34D"),
    "emerald": ("#059669", "#34D399", "#047857", "#6EE7B7"),
}


def palette(key: str, dark: bool) -> dict[str, object]:
    """某个系列组的配色（随主题切换）。"""
    light, darkc, light_text, dark_text = _PALETTES.get(key, _PALETTES["blue"])
    accent = QColor(darkc if dark else light)
    text = QColor(dark_text if dark else light_text)
    soft = QColor(accent)
    soft.setAlpha(38 if dark else 22)
    return {
        "key": key,
        "accent": accent,
        "text": text,
        "soft": soft,
        "line": QColor(accent.red(), accent.green(), accent.blue(), 90 if dark else 70),
    }


class _ElidedLabel(QLabel):
    """按控件宽度做尾部省略（QLabel 本身不会省略，长系列名会硬裁）。"""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setStyleSheet("background: transparent; border: 0;")
        self._full = text
        self._color = QColor("#000000")
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

    def setText(self, text: str):  # noqa: N802 - Qt 命名
        self._full = text
        super().setText(text)

    def set_color(self, color: QColor | str):
        """颜色自己记着 —— 样式表里的 color 不会进 QPalette，自绘时取不到。"""
        self._color = QColor(color)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        metrics = painter.fontMetrics()
        elided = metrics.elidedText(self._full, Qt.TextElideMode.ElideRight, self.width())
        painter.setPen(QPen(self._color))
        painter.drawText(self.rect(), int(self.alignment()), elided)
        painter.end()


class RailRow(QFrame):
    """一行：根 / 组 / 子项。配色、箭头、圆点、徽标都在这里画。"""

    clicked = Signal()
    expandToggled = Signal()      # 只点箭头：切换展开/收起，不改选中

    def __init__(self, level: int, text: str, count: int, theme: dict | None = None,
                 icon: str = "folder", key: str = "", parent=None):
        super().__init__(parent)
        self.level = level               # 0 根 / 1 组 / 2 子项
        self.key = key
        self.theme = theme
        self._checked = False
        self._hover = False
        self._expanded = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

        self.setFixedHeight(32 if level < 2 else 28)
        lay = QHBoxLayout(self)
        # ⚠ 上下必须留边距：描边画在行内的 y=inset 与 y=h-inset 上，
        # 子控件要是撑满整行高，就会把上下两条横边盖掉 ——
        # 屏幕上表现成「这个高亮框怎么只有左右两边」。
        lay.setContentsMargins(10 if level < 2 else 22, 5, 8, 5)
        lay.setSpacing(6)

        self.chevron = None
        if level == 1:
            self.chevron = QLabel(self)
            self.chevron.setFixedSize(12, 12)
            self.chevron.setStyleSheet("background: transparent;")
            self.chevron.setCursor(Qt.CursorShape.PointingHandCursor)
            self.chevron.setToolTip("展开 / 收起该分类（不改变筛选）")
            lay.addWidget(self.chevron)

        self.dot = None
        if level == 1:
            self.dot = QLabel(self)
            self.dot.setFixedSize(6, 6)
            lay.addWidget(self.dot)

        # ⚠ QLabel 必须传 parent。不带 parent 的 QWidget 是【顶层窗口】，
        # 若在被 addWidget 收编成子控件之前调用了 setVisible(True)，
        # 它会先以顶层窗口身份在屏幕正中闪一下（还带系统窗口阴影）——
        # 切系列时那一闪，就是下面这行曾经踩的坑。
        self.icon_label = QLabel(self)
        self.icon_label.setFixedSize(14, 14)
        self.icon_label.setStyleSheet("background: transparent;")
        lay.addWidget(self.icon_label)
        # 子项只有文字（设计稿如此），层级靠分支线表达
        self.icon_label.setVisible(level < 2)
        self._icon_name = icon

        self.text_label = _ElidedLabel(text)
        font = self.text_label.font()
        font.setPixelSize(12 if level < 2 else 11)
        font.setBold(level <= 1)
        self.text_label.setFont(font)
        lay.addWidget(self.text_label, 1)

        self.count_label = QLabel(str(count))
        count_font = self.count_label.font()
        count_font.setPixelSize(10)
        self.count_label.setFont(count_font)
        self.count_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.count_label.setMinimumWidth(24)
        # 徽标有不透明底色，高度必须小于行高、且落在描边以内，否则会啃掉上下横边
        self.count_label.setFixedHeight(18)
        lay.addWidget(self.count_label)

        self.refresh_theme()

    # ── 状态 ──
    def set_checked(self, checked: bool):
        if self._checked != checked:
            self._checked = checked
            self.refresh_theme()

    def is_checked(self) -> bool:
        return self._checked

    def set_expanded(self, expanded: bool):
        self._expanded = expanded
        self.refresh_theme()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        # 箭头区单独响应展开/收起，点别处才是「选中这个分类」
        if self.chevron is not None and event.position().x() <= self.chevron.geometry().right() + 2:
            self.expandToggled.emit()
            return
        self.clicked.emit()

    def enterEvent(self, event):
        self._hover = True
        self.refresh_theme()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.refresh_theme()
        super().leaveEvent(event)

    # ── 配色 ──
    def refresh_theme(self):
        accent = self.theme["accent"] if self.theme else QColor(S._ACCENT)
        text_color = QColor(to_qcolor(S._TEXT_PRIMARY if self.level <= 1 else S._TEXT_SECONDARY))
        muted = QColor(to_qcolor(S._TEXT_TERTIARY))

        card = S._CARD_BG
        border = S._BORDER_LIGHT
        white = "#FFFFFF"

        if self._checked and self.level == 0:
            # 根行选中：实心主色 + 白字（设计稿的 bg-blue-600 text-white）
            self.text_label.set_color(white)
            self.count_label.setStyleSheet(
                f"color: {white}; background: rgba(255,255,255,0.22); font-weight: 700;"
                " border-radius: 6px; padding: 1px 6px;")
        elif self._checked:
            self.text_label.set_color(accent)
            self.count_label.setStyleSheet(
                f"color: {accent.name()}; background: {card}; border: 1px solid {border};"
                " border-radius: 6px; padding: 1px 5px; font-weight: 700;")
        else:
            self.text_label.set_color(text_color)
            if self.level <= 1:
                self.count_label.setStyleSheet(
                    f"color: {muted.name()}; background: {card}; border: 1px solid {border};"
                    " border-radius: 6px; padding: 1px 5px;")
            else:
                self.count_label.setStyleSheet(
                    f"color: {muted.name()}; background: transparent; padding: 1px 5px;")

        # 图标
        if self.level == 0:
            icon_color = white if self._checked else accent.name()
            self.icon_label.setPixmap(
                painted_icon(self._icon_name, icon_color, 14).pixmap(14, 14))
        elif self.level == 1:
            self.icon_label.setPixmap(painted_icon("folder", accent.name(), 14).pixmap(14, 14))
            self.dot.setStyleSheet(f"background: {accent.name()}; border-radius: 3px;")
            chevron = "chevron_down" if self._expanded else "chevron_right"
            self.chevron.setPixmap(
                painted_icon(chevron, muted.name(), 12).pixmap(12, 12))
        else:
            self.icon_label.setPixmap(painted_icon(self._icon_name, muted.name(), 12).pixmap(12, 12))

        self.update()

    # ── 绘制：底 / 描边 / 左侧强调条 ──
    def highlight_rect(self) -> QRect:
        """高亮 / 悬停底色占的矩形。

        子项（level 2）收得更紧：行本来就只有 28px、行距又近，底色占满整格
        会跟上下两行糊在一起，看着像连成一条。留出上下各 3px 才读得出「只选中这一项」。
        """
        inset = 3 if self.level == 2 else 1
        return self.rect().adjusted(0, inset, 0, -inset)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.highlight_rect()
        radius = 8 if self.level < 2 else 6
        dark = S.get_theme() == "dark"
        accent = self.theme["accent"] if self.theme else QColor(S._ACCENT)
        soft = QColor(self.theme["soft"]) if self.theme else QColor(accent)
        hover_bg = QColor(255, 255, 255, 16) if dark else QColor(15, 23, 42, 12)

        if self._checked and self.level == 0:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(accent)
            painter.drawRoundedRect(rect, radius, radius)
        elif self._checked:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(soft)
            painter.drawRoundedRect(rect, radius, radius)
            painter.setPen(QPen(accent, 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect, radius, radius)
            if self.level == 2:      # 子项选中：左侧一条颜色强调条
                bar = rect.adjusted(3, 4, 0, -4)
                bar.setWidth(3)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(accent)
                painter.drawRoundedRect(bar, 1.5, 1.5)
        elif self._hover:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(hover_bg)
            painter.drawRoundedRect(rect, radius, radius)
        painter.end()


class BranchBox(QWidget):
    """子项容器：画一条竖直分支线 + 每个子项一个横向小钩。

    ⚠ 这里**绝对不能**加 `WA_TransparentForMouseEvents`。
    Qt 的 `QWidget::childAt()` 在挑选候选子控件时会跳过带这个标记的控件，
    而且**不会继续往它里面找** —— 也就是说整个子树（所有子项行）永远不可能
    被命中，点击、悬停、按下全被吃掉，表现就是「下面的子项点不了」。
    """
    def __init__(self, theme: dict, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.rows: list[QWidget] = []

    def set_rows(self, rows: list[QWidget]):
        self.rows = rows
        self.update()

    def paintEvent(self, event):
        if not self.rows:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(self.theme["line"]), 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        x = 14
        top = self.rows[0].geometry().center().y()
        bottom = self.rows[-1].geometry().center().y()
        painter.drawLine(x, top, x, bottom)
        for row in self.rows:
            y = row.geometry().center().y()
            painter.drawLine(x, y, x + 6, y)
        painter.end()


class ProductRail(QFrame):
    """整个左侧栏：头部（标题/搜索/图例）+ 系列树 + 底部状态。"""

    # (kind, key, group)，kind: all / group / series；group 只在 kind=series 时有值
    selectionChanged = Signal(str, str, str)
    searchChanged = Signal(str)
    dimensionChanged = Signal(str)      # "series" / "capacity"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ProductRail")
        self._dimension = "series"
        self._groups: list[tuple[str, str, list[tuple[str, int]]]] = []
        self._rows: dict[str, RailRow] = {}
        self._active_kind = "all"
        self._active_key = ""
        self._active_group = ""
        self._expanded: set[str] = set()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_header())

        self.scroll = QScrollArea()
        self.scroll.setObjectName("RailScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.viewport().setAutoFillBackground(False)
        holder = QWidget()
        holder.setObjectName("RailHolder")
        self.tree_layout = QVBoxLayout(holder)
        self.tree_layout.setContentsMargins(8, 8, 8, 8)
        self.tree_layout.setSpacing(3)
        self.tree_layout.addStretch(1)
        self.scroll.setWidget(holder)
        outer.addWidget(self.scroll, 1)

        outer.addWidget(self._build_footer())
        self.setFixedWidth(RAIL_WIDTH)

    # ── 头部 ──
    def _build_header(self) -> QWidget:
        head = QFrame()
        head.setObjectName("RailHead")
        lay = QVBoxLayout(head)
        lay.setContentsMargins(12, 12, 12, 10)
        lay.setSpacing(8)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        self.title_icon = QLabel()
        self.title_icon.setFixedSize(24, 24)
        self.title_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_icon.setObjectName("RailTitleIcon")
        title_row.addWidget(self.title_icon)
        self.title_label = QLabel(DIMENSION_TITLES["series"])
        self.title_label.setObjectName("RailTitle")
        title_row.addWidget(self.title_label)
        title_row.addStretch()
        self.total_pill = QLabel("—")
        self.total_pill.setObjectName("RailTotalPill")
        title_row.addWidget(self.total_pill)
        lay.addLayout(title_row)

        # 分组维度切换：同一批图，按系列看 or 按容量看
        seg_row = QHBoxLayout()
        seg_row.setSpacing(4)
        seg_row.setContentsMargins(0, 0, 0, 0)
        self._seg_buttons: dict[str, QPushButton] = {}
        for key, label in DIMENSIONS:
            button = QPushButton(label)
            button.setObjectName("RailSeg")
            button.setCheckable(True)
            button.setChecked(key == self._dimension)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFixedHeight(24)
            button.setToolTip("切换左栏的归类方式：按产品系列，或按容量（50ml / 125ml / 2.5L …）")
            button.clicked.connect(lambda _checked=False, k=key: self.set_dimension(k))
            seg_row.addWidget(button)
            self._seg_buttons[key] = button
        seg_row.addStretch()
        lay.addLayout(seg_row)

        self.search = QLineEdit()
        self.search.setObjectName("RailSearch")
        self.search.setPlaceholderText(DIMENSION_PLACEHOLDERS[self._dimension])
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumHeight(30)
        self.search.addAction(painted_icon("search", S._TEXT_TERTIARY, 13), QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(self._on_search_text)
        lay.addWidget(self.search)

        self._legend_lay = QHBoxLayout()
        self._legend_lay.setSpacing(6)
        self._legend_lay.setContentsMargins(2, 0, 2, 0)
        lay.addLayout(self._legend_lay)
        self._rebuild_legend()
        return head

    # ── 分组维度 ──
    def dimension(self) -> str:
        return self._dimension

    def set_dimension(self, key: str) -> None:
        """切到另一种归类方式（标题 / 搜索提示 / 图例一起换）。"""
        if key not in DIMENSION_KEYS:
            return
        same = key == self._dimension
        self._dimension = key
        for button_key, button in self._seg_buttons.items():
            button.setChecked(button_key == key)
        if same:
            return
        self.title_label.setText(DIMENSION_TITLES[key])
        self.search.setPlaceholderText(DIMENSION_PLACEHOLDERS[key])
        self._rebuild_legend()
        self.dimensionChanged.emit(key)

    def _rebuild_legend(self) -> None:
        """重建图例（两个维度各有各的四色）。"""
        lay = self._legend_lay
        while lay.count():
            item = lay.takeAt(0)
            widget = item.widget()
            if widget is None:
                continue
            # ⚠ 只 deleteLater 不够：控件仍是父控件的子控件，会照原位置继续画
            widget.setParent(None)
            widget.deleteLater()
        dark = S.get_theme() == "dark"
        for text, color_key in DIMENSION_LEGENDS.get(self._dimension, ()):
            chip = QLabel(text)
            chip.setObjectName("RailLegend")
            dot = QLabel()
            dot.setFixedSize(6, 6)
            color = palette(color_key, dark)["accent"]
            dot.setStyleSheet(f"background: {color.name()}; border-radius: 3px;")
            lay.addWidget(dot)
            lay.addWidget(chip)
            lay.addSpacing(2)
        lay.addStretch()

    # ── 底部 ──
    def _build_footer(self) -> QWidget:
        foot = QFrame()
        foot.setObjectName("RailFoot")
        lay = QVBoxLayout(foot)
        lay.setContentsMargins(12, 9, 12, 10)
        lay.setSpacing(5)

        top = QHBoxLayout()
        top.setSpacing(6)
        pulse = QLabel()
        pulse.setFixedSize(8, 8)
        pulse.setStyleSheet(f"background: {S._SUCCESS}; border-radius: 4px;")
        top.addWidget(pulse)
        guard = QLabel("免抠透底保障")
        guard.setObjectName("RailGuard")
        top.addWidget(guard)
        top.addStretch()
        self.quality_pill = QLabel("800×800")
        self.quality_pill.setObjectName("RailQualityPill")
        top.addWidget(self.quality_pill)
        lay.addLayout(top)

        self.footer_text = _ElidedLabel("")
        self.footer_text.setObjectName("RailFootText")
        lay.addWidget(self.footer_text)

        self.footer_hint = _ElidedLabel("")
        self.footer_hint.setObjectName("RailFootHint")
        lay.addWidget(self.footer_hint)
        return foot

    def _on_search_text(self, text: str):
        """左栏搜索：先自己把行过滤掉，再通知外面去过滤右侧网格。"""
        if text.strip() and self._active_kind != "all":
            # 一边搜索一边锁着某个系列，很容易搜出空结果；搜索时自动回到「全部」
            self._active_kind, self._active_key, self._active_group = "all", "", ""
            self.selectionChanged.emit("all", "", "")
        self._rebuild()
        self.searchChanged.emit(text)

    # ── 数据 ──
    def set_groups(self, groups: list[tuple[str, str, list[tuple[str, int]]]],
                   total: int) -> None:
        """groups: [(组名, 颜色键, [(系列名, 数量), …]), …]"""
        first_load = not self._groups
        self._groups = groups
        self.total_pill.setText(f"共 {total:,} 款")
        if first_load and groups:
            # 默认展开条目最多的那个组，免得一进来看到一排收起的文件夹
            biggest = max(groups, key=lambda g: sum(n for _n, n in g[2]))[0]
            self._expanded = {biggest}
        self._rebuild()

    def _rebuild(self):
        bar = self.scroll.verticalScrollBar()
        keep_offset = bar.value() if bar is not None else 0
        while self.tree_layout.count() > 1:      # 保留末尾的 stretch
            item = self.tree_layout.takeAt(0)
            widget = item.widget()
            if widget is None:
                continue
            # 只 deleteLater 是不够的：控件此刻仍是 holder 的子控件，
            # 在新布局重排生效前会照原位置继续绘制，于是新旧两批行叠在一起。
            # 必须先 setParent(None) 把它摘下来，叠影就没了。
            widget.setParent(None)
            widget.deleteLater()
        self._rows.clear()

        dark = S.get_theme() == "dark"
        keyword = self.search.text().strip().lower()

        root_theme = {"accent": QColor(S._ACCENT), "soft": QColor(S._ACCENT), "line": QColor(S._ACCENT)}
        soft = QColor(S._ACCENT)
        soft.setAlpha(38 if dark else 22)
        root_theme["soft"] = soft
        root_row = RailRow(0, "全部（当前来源全量）", sum(
            sum(n for _n, n in children) for _g, _c, children in self._groups),
            root_theme, icon="image", key="all")
        root_row.clicked.connect(lambda: self._select("all", "", root_row))
        self.tree_layout.insertWidget(self.tree_layout.count() - 1, root_row)
        self._rows["all"] = root_row

        for group, color_key, children in self._groups:
            theme = palette(color_key, dark)
            shown = [(name, count) for name, count in children
                     if not keyword or keyword in name.lower() or keyword in group.lower()]
            group_total = sum(count for _n, count in children)
            if keyword and not shown:
                continue
            group_row = RailRow(1, group, group_total, theme, icon="folder", key=group)
            group_row.set_expanded(group in self._expanded or bool(keyword))
            group_row.clicked.connect(lambda g=group, r=group_row: self._select("group", g, r))
            group_row.expandToggled.connect(
                lambda g=group, r=group_row: self._toggle_expand(g, r))
            self.tree_layout.insertWidget(self.tree_layout.count() - 1, group_row)
            self._rows[f"group:{group}"] = group_row

            if group in self._expanded or keyword:
                box = BranchBox(theme)
                box_lay = QVBoxLayout(box)
                box_lay.setContentsMargins(22, 2, 0, 2)
                box_lay.setSpacing(4)
                rows: list[QWidget] = []
                for name, count in shown:
                    child = RailRow(2, name, count, theme, icon="file",
                                    key=f"{group}/{name}")
                    child.clicked.connect(
                        lambda n=name, g=group, r=child: self._select("series", n, r, g))
                    box_lay.addWidget(child)
                    rows.append(child)
                    self._rows[f"series:{group}/{name}"] = child
                box.set_rows(rows)
                self.tree_layout.insertWidget(self.tree_layout.count() - 1, box)

        self._apply_active()
        self._sync_quality_pill()
        # ⚠ 不能在这里直接 ensureWidgetVisible：此刻布局还没跑，新建的行 y 全是 0，
        # 它会照着错误几何把滚动条拉到顶部 —— 表现就是「滚到下面点一项，视图跳回上面」。
        # 用 0 延时等布局生效，再决定要不要滚。
        QTimer.singleShot(0, lambda keep=keep_offset: self._restore_view(keep))

    def _restore_view(self, offset: int):
        """重建后恢复原来的滚动位置；只有当选中行确实不在视野里时才把它滚进来。"""
        bar = self.scroll.verticalScrollBar()
        if bar is None:
            return
        bar.setValue(min(offset, bar.maximum()))
        row = self._rows.get(self._row_key_for_active())
        if row is None:
            return
        viewport = self.scroll.viewport()
        top = row.mapTo(viewport, QPoint(0, 0)).y()
        if top < 0 or top + row.height() > viewport.height():
            self.scroll.ensureWidgetVisible(row, 0, 24)

    def _sync_quality_pill(self):
        squares = sum(count for _g, _c, children in self._groups
                      for name, count in children if "800" in name)
        self.quality_pill.setText(f"800×800 · {squares}" if squares else "800×800")

    def _toggle_expand(self, group: str, row: RailRow):
        """只切换展开/收起，不动左侧高亮，也不改右侧筛选。"""
        if group in self._expanded:
            self._expanded.discard(group)
        else:
            self._expanded.add(group)
        self._rebuild()

    def _select(self, kind: str, key: str, row: RailRow, group: str = ""):
        """选中：改高亮 + 通知外面过筛（大类会顺手展开，免得选了个看不见的空目录）。"""
        self._active_kind = kind
        self._active_key = key
        self._active_group = group
        if kind == "group" and key:
            self._expanded.add(key)
        self._rebuild()
        self.selectionChanged.emit(kind, key, group)

    def _row_key_for_active(self) -> str:
        if self._active_kind == "all":
            return "all"
        if self._active_kind == "group":
            return f"group:{self._active_key}"
        return f"series:{self._active_group}/{self._active_key}"

    def _apply_active(self):
        """只有当前真正选中的那一行点亮 —— 根行以前写成 `key in ("all", target)`，
        key 恒等于 "all" 所以恒为真，「全部」会一直挂着实心蓝底，
        和大类/系列的高亮同时出现，看起来像选中了两处。"""
        target = self._row_key_for_active()
        for key, row in self._rows.items():
            row.set_checked(key == "all" if self._active_kind == "all" else key == target)

    # ── 外部接口 ──
    def set_footer(self, path_text: str, hint: str):
        self.footer_text.setText(path_text)
        self.footer_hint.setText(hint)
        self.footer_text.set_color(to_qcolor(S._TEXT_TERTIARY))
        self.footer_hint.set_color(to_qcolor(S._TEXT_TERTIARY))

    def reset_selection(self):
        self._active_kind = "all"
        self._active_key = ""
        self._active_group = ""
        self._apply_active()

    def set_expanded_all(self, expanded: bool = True):
        self._expanded = {group for group, _c, _ch in self._groups} if expanded else set()
        self._rebuild()

    def refresh_theme(self):
        accent = S._ACCENT
        self.title_icon.setPixmap(painted_icon("library", "#FFFFFF", 14).pixmap(14, 14))
        self.title_icon.setStyleSheet(
            f"background: {accent}; border-radius: 7px;")
        self._rebuild_legend()          # 图例颜色也要跟着主题换
        for row in self._rows.values():
            row.refresh_theme()
        self._rebuild()
