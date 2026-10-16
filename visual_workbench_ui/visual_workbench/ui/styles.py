"""设计系统 —— 颜色 / 间距 / 圆角 / 字体，以及全局样式表（v1.3 高定微渐变零卡顿版）。

═══════════════════════════════════════════════════════════════════
Design tokens & Zero-Lag Visual Architecture
─────────────────────────────────────────────────────────────────
· 零卡顿立体感：密集列表/网格严禁使用 QGraphicsDropShadowEffect（避免离屏 FBO 高斯模糊掉帧）；
  全局卡片采用纯 GPU 友好的垂直微渐变（Subtle Vertical Gradient）+ 顶部高光边框模拟受光面。
· 中性色：浅色用冷调丝绸灰白（#F3F5FA / #FFFFFF），深色用深空曜石黑（#0B0D13 / #131722）。
· 点缀色：工作室级电光蓝（#2563EB / #3B82F6），辅以翡翠绿、琥珀金与玫瑰红语义色。
· 字色三级：Primary 94% / Secondary 66% / Muted 44%。
· 动效：150–200ms OutCubic，带运行态中断复位防漂移保护。
═══════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QPoint, QPropertyAnimation
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QGraphicsDropShadowEffect, QGraphicsOpacityEffect, QWidget,
)

_RES = Path(__file__).resolve().parent / "resources"
_ARROW_DOWN = (_RES / "arrow_down.png").as_posix()
_ARROW_UP = (_RES / "arrow_up.png").as_posix()
_CHEV_LEFT = (_RES / "chevron_left.png").as_posix()
_CHEV_RIGHT = (_RES / "chevron_right.png").as_posix()
_CHECK = (_RES / "check.png").as_posix()

CURRENT_THEME = "light"

# ── 间距（4pt 栅格） ──
SP_1, SP_2, SP_3, SP_4 = 4, 8, 12, 16
SP_5, SP_6, SP_8, SP_10 = 20, 24, 32, 40

# ── 圆角 ──
R_XS = 6      # 标签 / 徽章
R_SM = 8      # 小控件
R_MD = 12     # 按钮 / 输入框 / 内部元素
R_LG = 16     # 卡片
R_XL = 20     # 外层容器
R_PILL = 999  # 胶囊


def _rgba(r: int, g: int, b: int, alpha: float) -> str:
    """生成 QSS 能识别的 rgba 字符串。注意 QColor 不认这种写法，别拿它去建 QColor。"""
    return f"rgba({r},{g},{b},{alpha:g})"


def to_qcolor(text, fallback: str = "#888888") -> QColor:
    """把颜色字符串转成 QColor。"""
    if isinstance(text, QColor):
        return text
    value = str(text or "").strip()
    if value.startswith("rgb(") or value.startswith("rgba("):
        inner = value[value.index("(") + 1:value.rindex(")")]
        parts = [p.strip() for p in inner.split(",")]
        try:
            r, g, b = (int(float(parts[0])), int(float(parts[1])), int(float(parts[2])))
            alpha = float(parts[3]) if len(parts) > 3 else 1.0
            return QColor(r, g, b, max(0, min(255, round(alpha * 255))))
        except (ValueError, IndexError):
            return QColor(fallback)
    color = QColor(value)
    return color if color.isValid() else QColor(fallback)


_INK = (15, 23, 42)
_PAPER = (241, 245, 249)

# ═══════════════════════════════════════════════════════════════
#  浅色主题（冷调蓝灰丝绸质感）
# ═══════════════════════════════════════════════════════════════
_LIGHT_BG = "#F5F7FB"
_LIGHT_SURFACE = "#FFFFFF"
_LIGHT_SURFACE_TOP = "#FFFFFF"
_LIGHT_SURFACE_BOTTOM = "#FAFBFD"
_LIGHT_SURFACE_2 = "#EDF1F7"
_LIGHT_SURFACE_3 = "#E2E8F2"

_LIGHT_BORDER = _rgba(*_INK, 0.085)
_LIGHT_BORDER_STRONG = _rgba(*_INK, 0.16)
_LIGHT_BORDER_LIGHT = _rgba(*_INK, 0.055)

_LIGHT_SIDEBAR_BG = "#FFFFFF"
_LIGHT_SIDEBAR_TEXT = _rgba(*_INK, 0.64)
_LIGHT_SIDEBAR_TEXT_ACTIVE = _rgba(*_INK, 0.94)
_LIGHT_SIDEBAR_HOVER = _rgba(*_INK, 0.05)
_LIGHT_SIDEBAR_ACTIVE_BG = _rgba(*_INK, 0.06)

_LIGHT_CARD_BG = _LIGHT_SURFACE
_LIGHT_CARD_BORDER = _LIGHT_BORDER
_LIGHT_CARD_SHADOW = _rgba(*_INK, 0.05)
_LIGHT_CARD_SHADOW_HOVER = _rgba(*_INK, 0.09)

_LIGHT_ACCENT = "#315BD7"
_LIGHT_ACCENT_HOVER = "#254ABD"
_LIGHT_ACCENT_PRESSED = "#1D3C9C"
_LIGHT_ACCENT_LIGHT = _rgba(49, 91, 215, 0.09)
_LIGHT_ACCENT_SOFT = _rgba(49, 91, 215, 0.16)
_LIGHT_NAV_ACTIVE_ICON = "#FFFFFF"
_LIGHT_AMBER = "#B45309"

_LIGHT_TEXT_PRIMARY = _rgba(*_INK, 0.93)
_LIGHT_TEXT_SECONDARY = _rgba(*_INK, 0.78)
_LIGHT_TEXT_TERTIARY = _rgba(*_INK, 0.62)
_LIGHT_TEXT_ON_DARK = _rgba(*_INK, 0.66)
_LIGHT_TEXT_ON_DARK_ACTIVE = _rgba(*_INK, 0.94)

_LIGHT_INPUT_BG = "#FFFFFF"
_LIGHT_INPUT_BORDER = _LIGHT_BORDER
_LIGHT_INPUT_FOCUS = _LIGHT_ACCENT
_LIGHT_SEGMENTED_BG = _LIGHT_SURFACE_2
_LIGHT_SEGMENTED_ACTIVE_BG = "#FFFFFF"
_LIGHT_SEGMENTED_ACTIVE_SHADOW = "0 1px 2px rgba(9,9,11,0.06)"
_LIGHT_SCROLLBAR_THUMB = _rgba(*_INK, 0.14)
_LIGHT_SCROLLBAR_THUMB_HOVER = _rgba(*_INK, 0.26)
_LIGHT_LOCATION_BG = _LIGHT_SURFACE_2
_LIGHT_SKELETON_BG = _LIGHT_SURFACE_2
_LIGHT_SKELETON_PULSE = _LIGHT_SURFACE_3

_LIGHT_BADGE_PSD_BG = "#EFEDFB"
_LIGHT_BADGE_PSD_FG = "#4C3E85"
_LIGHT_BADGE_IMAGE_BG = "#E7F4EC"
_LIGHT_BADGE_IMAGE_FG = "#1D6641"
_LIGHT_BADGE_WORKORDER_BG = "#F0EBFA"
_LIGHT_BADGE_WORKORDER_FG = "#5B3E9B"
_LIGHT_BADGE_PDF_BG = "#FBF1DF"
_LIGHT_BADGE_PDF_FG = "#7A5A0C"
_LIGHT_BADGE_OTHER_BG = _LIGHT_SURFACE_2
_LIGHT_BADGE_OTHER_FG = _rgba(*_INK, 0.55)

_LIGHT_BORDER_SIDEBAR = _LIGHT_BORDER
_LIGHT_BORDER_NAV_CHECKED = _rgba(49, 91, 215, 0.24)
_LIGHT_BORDER_INPUT = _LIGHT_BORDER
_LIGHT_BORDER_CARD = _LIGHT_BORDER
_LIGHT_BORDER_CARD_HOVER = _LIGHT_BORDER_STRONG
_LIGHT_NAV_CHECKED_BG = _LIGHT_ACCENT_LIGHT
_LIGHT_NAV_CHECKED_HOVER_BG = _LIGHT_ACCENT_SOFT

_LIGHT_DANGER = "#DC2626"
_LIGHT_SUCCESS = "#059669"
_LIGHT_WARNING = "#D97706"

# ═══════════════════════════════════════════════════════════════
#  深色主题（曜石深空黑，带顶部微光高光层次）
# ═══════════════════════════════════════════════════════════════
_DARK_BG = "#0E121A"
_DARK_SURFACE = "#171D28"
_DARK_SURFACE_TOP = "#1B2230"
_DARK_SURFACE_BOTTOM = "#161B26"
_DARK_SURFACE_2 = "#202837"
_DARK_SURFACE_3 = "#2A3547"

_DARK_BORDER = _rgba(*_PAPER, 0.095)
_DARK_BORDER_STRONG = _rgba(*_PAPER, 0.18)
_DARK_BORDER_LIGHT = _rgba(*_PAPER, 0.06)

_DARK_SIDEBAR_BG = "#131925"
_DARK_SIDEBAR_TEXT = _rgba(*_PAPER, 0.64)
_DARK_SIDEBAR_TEXT_ACTIVE = _rgba(*_PAPER, 0.95)
_DARK_SIDEBAR_HOVER = _rgba(*_PAPER, 0.06)
_DARK_SIDEBAR_ACTIVE_BG = _rgba(*_PAPER, 0.08)

_DARK_CARD_BG = _DARK_SURFACE
_DARK_CARD_BORDER = _DARK_BORDER
_DARK_CARD_SHADOW = "rgba(0,0,0,0.45)"
_DARK_CARD_SHADOW_HOVER = "rgba(0,0,0,0.6)"

_DARK_ACCENT = "#4172D7"
_DARK_ACCENT_HOVER = "#3562C6"
_DARK_ACCENT_PRESSED = "#294FA8"
_DARK_ACCENT_LIGHT = _rgba(91, 140, 255, 0.17)
_DARK_ACCENT_SOFT = _rgba(91, 140, 255, 0.25)
_DARK_NAV_ACTIVE_ICON = "#FFFFFF"
_DARK_AMBER = "#F59E0B"

_DARK_TEXT_PRIMARY = _rgba(*_PAPER, 0.94)
_DARK_TEXT_SECONDARY = _rgba(*_PAPER, 0.80)
_DARK_TEXT_TERTIARY = _rgba(*_PAPER, 0.62)
_DARK_TEXT_ON_DARK = _rgba(*_PAPER, 0.67)
_DARK_TEXT_ON_DARK_ACTIVE = _rgba(*_PAPER, 0.95)

_DARK_INPUT_BG = "#1B2230"
_DARK_INPUT_BORDER = _DARK_BORDER
_DARK_INPUT_FOCUS = _DARK_ACCENT
_DARK_SEGMENTED_BG = _DARK_SURFACE_2
_DARK_SEGMENTED_ACTIVE_BG = _DARK_SURFACE_3
_DARK_SEGMENTED_ACTIVE_SHADOW = "0 1px 2px rgba(0,0,0,0.4)"
_DARK_SCROLLBAR_THUMB = _rgba(*_PAPER, 0.14)
_DARK_SCROLLBAR_THUMB_HOVER = _rgba(*_PAPER, 0.26)
_DARK_LOCATION_BG = _DARK_SURFACE_2
_DARK_SKELETON_BG = _DARK_SURFACE_2
_DARK_SKELETON_PULSE = _DARK_SURFACE_3

_DARK_BADGE_PSD_BG = "rgba(107,91,149,0.24)"
_DARK_BADGE_PSD_FG = "#C4B5FD"
_DARK_BADGE_IMAGE_BG = "rgba(45,122,79,0.24)"
_DARK_BADGE_IMAGE_FG = "#6EE7B7"
_DARK_BADGE_WORKORDER_BG = "rgba(124,92,191,0.24)"
_DARK_BADGE_WORKORDER_FG = "#D8B4FE"
_DARK_BADGE_PDF_BG = "rgba(184,134,11,0.24)"
_DARK_BADGE_PDF_FG = "#FCD34D"
_DARK_BADGE_OTHER_BG = _DARK_SURFACE_2
_DARK_BADGE_OTHER_FG = _rgba(*_PAPER, 0.60)

_DARK_BORDER_SIDEBAR = _DARK_BORDER
_DARK_BORDER_NAV_CHECKED = _rgba(91, 124, 250, 0.28)
_DARK_BORDER_INPUT = _DARK_BORDER
_DARK_BORDER_CARD = _DARK_BORDER
_DARK_BORDER_CARD_HOVER = _DARK_BORDER_STRONG
_DARK_NAV_CHECKED_BG = _DARK_ACCENT_LIGHT
_DARK_NAV_CHECKED_HOVER_BG = _DARK_ACCENT_SOFT

_DARK_DANGER = "#EF4444"
_DARK_SUCCESS = "#34D399"
_DARK_WARNING = "#FBBF24"

# ═══════════════════════════════════════════════════════════════
#  当前生效值（由 set_theme 切换）
# ═══════════════════════════════════════════════════════════════
_BG = _LIGHT_BG
_SURFACE = _LIGHT_SURFACE
_SURFACE_TOP = _LIGHT_SURFACE_TOP
_SURFACE_BOTTOM = _LIGHT_SURFACE_BOTTOM
_SURFACE_2 = _LIGHT_SURFACE_2
_SURFACE_3 = _LIGHT_SURFACE_3
_BORDER = _LIGHT_BORDER
_BORDER_STRONG = _LIGHT_BORDER_STRONG

_SIDEBAR_BG = _LIGHT_SIDEBAR_BG
_SIDEBAR_TEXT = _LIGHT_SIDEBAR_TEXT
_SIDEBAR_TEXT_ACTIVE = _LIGHT_SIDEBAR_TEXT_ACTIVE
_SIDEBAR_HOVER = _LIGHT_SIDEBAR_HOVER
_SIDEBAR_ACTIVE_BG = _LIGHT_SIDEBAR_ACTIVE_BG

_CARD_BG = _LIGHT_CARD_BG
_CARD_BORDER = _LIGHT_CARD_BORDER
_CARD_SHADOW = _LIGHT_CARD_SHADOW
_CARD_SHADOW_HOVER = _LIGHT_CARD_SHADOW_HOVER

_ACCENT = _LIGHT_ACCENT
_ACCENT_HOVER = _LIGHT_ACCENT_HOVER
_ACCENT_PRESSED = _LIGHT_ACCENT_PRESSED
_ACCENT_LIGHT = _LIGHT_ACCENT_LIGHT
_ACCENT_SOFT = _LIGHT_ACCENT_SOFT

_TEXT_PRIMARY = _LIGHT_TEXT_PRIMARY
_TEXT_SECONDARY = _LIGHT_TEXT_SECONDARY
_TEXT_TERTIARY = _LIGHT_TEXT_TERTIARY
_TEXT_ON_DARK = _LIGHT_TEXT_ON_DARK
_TEXT_ON_DARK_ACTIVE = _LIGHT_TEXT_ON_DARK_ACTIVE

_INPUT_BG = _LIGHT_INPUT_BG
_INPUT_BORDER = _LIGHT_INPUT_BORDER
_INPUT_FOCUS = _LIGHT_INPUT_FOCUS

_SEGMENTED_BG = _LIGHT_SEGMENTED_BG
_SEGMENTED_ACTIVE_BG = _LIGHT_SEGMENTED_ACTIVE_BG
_SEGMENTED_ACTIVE_SHADOW = _LIGHT_SEGMENTED_ACTIVE_SHADOW

_SCROLLBAR_THUMB = _LIGHT_SCROLLBAR_THUMB
_SCROLLBAR_THUMB_HOVER = _LIGHT_SCROLLBAR_THUMB_HOVER
_LOCATION_BG = _LIGHT_LOCATION_BG
_SKELETON_BG = _LIGHT_SKELETON_BG
_SKELETON_PULSE = _LIGHT_SKELETON_PULSE

_BADGE_PSD_BG = _LIGHT_BADGE_PSD_BG
_BADGE_PSD_FG = _LIGHT_BADGE_PSD_FG
_BADGE_IMAGE_BG = _LIGHT_BADGE_IMAGE_BG
_BADGE_IMAGE_FG = _LIGHT_BADGE_IMAGE_FG
_BADGE_WORKORDER_BG = _LIGHT_BADGE_WORKORDER_BG
_BADGE_WORKORDER_FG = _LIGHT_BADGE_WORKORDER_FG
_BADGE_PDF_BG = _LIGHT_BADGE_PDF_BG
_BADGE_PDF_FG = _LIGHT_BADGE_PDF_FG
_BADGE_OTHER_BG = _LIGHT_BADGE_OTHER_BG
_BADGE_OTHER_FG = _LIGHT_BADGE_OTHER_FG

_BORDER_LIGHT = _LIGHT_BORDER_LIGHT
_BORDER_SIDEBAR = _LIGHT_BORDER_SIDEBAR
_BORDER_NAV_CHECKED = _LIGHT_BORDER_NAV_CHECKED
_BORDER_INPUT = _LIGHT_BORDER_INPUT
_BORDER_CARD = _LIGHT_BORDER_CARD
_BORDER_CARD_HOVER = _LIGHT_BORDER_CARD_HOVER

_NAV_ACTIVE_ICON = _LIGHT_NAV_ACTIVE_ICON
_NAV_CHECKED_BG = _LIGHT_NAV_CHECKED_BG
_NAV_CHECKED_HOVER_BG = _LIGHT_NAV_CHECKED_HOVER_BG

_AMBER = _LIGHT_AMBER
_DANGER = _LIGHT_DANGER
_SUCCESS = _LIGHT_SUCCESS
_WARNING = _LIGHT_WARNING

_SHADOW_LIGHT = QColor(9, 9, 11, 16)
_SHADOW_HOVER = QColor(9, 9, 11, 26)
_SHADOW_SIDEBAR = QColor(9, 9, 11, 22)

_THEME_TABLE = {
    "_BG": (_LIGHT_BG, _DARK_BG),
    "_SURFACE": (_LIGHT_SURFACE, _DARK_SURFACE),
    "_SURFACE_TOP": (_LIGHT_SURFACE_TOP, _DARK_SURFACE_TOP),
    "_SURFACE_BOTTOM": (_LIGHT_SURFACE_BOTTOM, _DARK_SURFACE_BOTTOM),
    "_SURFACE_2": (_LIGHT_SURFACE_2, _DARK_SURFACE_2),
    "_SURFACE_3": (_LIGHT_SURFACE_3, _DARK_SURFACE_3),
    "_BORDER": (_LIGHT_BORDER, _DARK_BORDER),
    "_BORDER_STRONG": (_LIGHT_BORDER_STRONG, _DARK_BORDER_STRONG),
    "_SIDEBAR_BG": (_LIGHT_SIDEBAR_BG, _DARK_SIDEBAR_BG),
    "_SIDEBAR_TEXT": (_LIGHT_SIDEBAR_TEXT, _DARK_SIDEBAR_TEXT),
    "_SIDEBAR_TEXT_ACTIVE": (_LIGHT_SIDEBAR_TEXT_ACTIVE, _DARK_SIDEBAR_TEXT_ACTIVE),
    "_SIDEBAR_HOVER": (_LIGHT_SIDEBAR_HOVER, _DARK_SIDEBAR_HOVER),
    "_SIDEBAR_ACTIVE_BG": (_LIGHT_SIDEBAR_ACTIVE_BG, _DARK_SIDEBAR_ACTIVE_BG),
    "_CARD_BG": (_LIGHT_CARD_BG, _DARK_CARD_BG),
    "_CARD_BORDER": (_LIGHT_CARD_BORDER, _DARK_CARD_BORDER),
    "_CARD_SHADOW": (_LIGHT_CARD_SHADOW, _DARK_CARD_SHADOW),
    "_CARD_SHADOW_HOVER": (_LIGHT_CARD_SHADOW_HOVER, _DARK_CARD_SHADOW_HOVER),
    "_ACCENT": (_LIGHT_ACCENT, _DARK_ACCENT),
    "_ACCENT_HOVER": (_LIGHT_ACCENT_HOVER, _DARK_ACCENT_HOVER),
    "_ACCENT_PRESSED": (_LIGHT_ACCENT_PRESSED, _DARK_ACCENT_PRESSED),
    "_ACCENT_LIGHT": (_LIGHT_ACCENT_LIGHT, _DARK_ACCENT_LIGHT),
    "_ACCENT_SOFT": (_LIGHT_ACCENT_SOFT, _DARK_ACCENT_SOFT),
    "_TEXT_PRIMARY": (_LIGHT_TEXT_PRIMARY, _DARK_TEXT_PRIMARY),
    "_TEXT_SECONDARY": (_LIGHT_TEXT_SECONDARY, _DARK_TEXT_SECONDARY),
    "_TEXT_TERTIARY": (_LIGHT_TEXT_TERTIARY, _DARK_TEXT_TERTIARY),
    "_TEXT_ON_DARK": (_LIGHT_TEXT_ON_DARK, _DARK_TEXT_ON_DARK),
    "_TEXT_ON_DARK_ACTIVE": (_LIGHT_TEXT_ON_DARK_ACTIVE, _DARK_TEXT_ON_DARK_ACTIVE),
    "_INPUT_BG": (_LIGHT_INPUT_BG, _DARK_INPUT_BG),
    "_INPUT_BORDER": (_LIGHT_INPUT_BORDER, _DARK_INPUT_BORDER),
    "_INPUT_FOCUS": (_LIGHT_INPUT_FOCUS, _DARK_INPUT_FOCUS),
    "_SEGMENTED_BG": (_LIGHT_SEGMENTED_BG, _DARK_SEGMENTED_BG),
    "_SEGMENTED_ACTIVE_BG": (_LIGHT_SEGMENTED_ACTIVE_BG, _DARK_SEGMENTED_ACTIVE_BG),
    "_SEGMENTED_ACTIVE_SHADOW": (_LIGHT_SEGMENTED_ACTIVE_SHADOW, _DARK_SEGMENTED_ACTIVE_SHADOW),
    "_SCROLLBAR_THUMB": (_LIGHT_SCROLLBAR_THUMB, _DARK_SCROLLBAR_THUMB),
    "_SCROLLBAR_THUMB_HOVER": (_LIGHT_SCROLLBAR_THUMB_HOVER, _DARK_SCROLLBAR_THUMB_HOVER),
    "_LOCATION_BG": (_LIGHT_LOCATION_BG, _DARK_LOCATION_BG),
    "_SKELETON_BG": (_LIGHT_SKELETON_BG, _DARK_SKELETON_BG),
    "_SKELETON_PULSE": (_LIGHT_SKELETON_PULSE, _DARK_SKELETON_PULSE),
    "_BADGE_PSD_BG": (_LIGHT_BADGE_PSD_BG, _DARK_BADGE_PSD_BG),
    "_BADGE_PSD_FG": (_LIGHT_BADGE_PSD_FG, _DARK_BADGE_PSD_FG),
    "_BADGE_IMAGE_BG": (_LIGHT_BADGE_IMAGE_BG, _DARK_BADGE_IMAGE_BG),
    "_BADGE_IMAGE_FG": (_LIGHT_BADGE_IMAGE_FG, _DARK_BADGE_IMAGE_FG),
    "_BADGE_WORKORDER_BG": (_LIGHT_BADGE_WORKORDER_BG, _DARK_BADGE_WORKORDER_BG),
    "_BADGE_WORKORDER_FG": (_LIGHT_BADGE_WORKORDER_FG, _DARK_BADGE_WORKORDER_FG),
    "_BADGE_PDF_BG": (_LIGHT_BADGE_PDF_BG, _DARK_BADGE_PDF_BG),
    "_BADGE_PDF_FG": (_LIGHT_BADGE_PDF_FG, _DARK_BADGE_PDF_FG),
    "_BADGE_OTHER_BG": (_LIGHT_BADGE_OTHER_BG, _DARK_BADGE_OTHER_BG),
    "_BADGE_OTHER_FG": (_LIGHT_BADGE_OTHER_FG, _DARK_BADGE_OTHER_FG),
    "_BORDER_LIGHT": (_LIGHT_BORDER_LIGHT, _DARK_BORDER_LIGHT),
    "_BORDER_SIDEBAR": (_LIGHT_BORDER_SIDEBAR, _DARK_BORDER_SIDEBAR),
    "_BORDER_NAV_CHECKED": (_LIGHT_BORDER_NAV_CHECKED, _DARK_BORDER_NAV_CHECKED),
    "_BORDER_INPUT": (_LIGHT_BORDER_INPUT, _DARK_BORDER_INPUT),
    "_BORDER_CARD": (_LIGHT_BORDER_CARD, _DARK_BORDER_CARD),
    "_BORDER_CARD_HOVER": (_LIGHT_BORDER_CARD_HOVER, _DARK_BORDER_CARD_HOVER),
    "_NAV_ACTIVE_ICON": (_LIGHT_NAV_ACTIVE_ICON, _DARK_NAV_ACTIVE_ICON),
    "_AMBER": (_LIGHT_AMBER, _DARK_AMBER),
    "_NAV_CHECKED_BG": (_LIGHT_NAV_CHECKED_BG, _DARK_NAV_CHECKED_BG),
    "_NAV_CHECKED_HOVER_BG": (_LIGHT_NAV_CHECKED_HOVER_BG, _DARK_NAV_CHECKED_HOVER_BG),
    "_DANGER": (_LIGHT_DANGER, _DARK_DANGER),
    "_SUCCESS": (_LIGHT_SUCCESS, _DARK_SUCCESS),
    "_WARNING": (_LIGHT_WARNING, _DARK_WARNING),
}

_COLOR_NAMES = list(_THEME_TABLE)

_PRIORITY_PALETTE = {
    "high":   (("#FCEBEB", "#A32D2D"), ("rgba(239,68,68,0.20)", "#FCA5A5")),
    "medium": (("#E6ECFB", "#2B5CE6"), ("rgba(59,130,246,0.20)", "#93C5FD")),
    "low":    (("#F0F2F8", "#5F5E5A"), ("rgba(236,239,244,0.10)", "#94A3B8")),
}

_CLEANER_BADGE_PALETTE = {
    "win_illegal":  (("#FFEBE9", "#C62F26"), ("rgba(239,68,68,0.22)", "#FCA5A5")),
    "zero_width":   (("#EDE7FF", "#6941C6"), ("rgba(139,92,246,0.22)", "#C4B5FD")),
    "control":      (("#FFF4E5", "#B25E09"), ("rgba(245,158,11,0.22)", "#FCD34D")),
    "variation":    (("#E5F3FF", "#0B6BCB"), ("rgba(59,130,246,0.22)", "#93C5FD")),
    "trim":         (("#E8F3EE", "#18794E"), ("rgba(16,185,129,0.22)", "#6EE7B7")),
    "fullwidth":    (("#EDE7FF", "#6941C6"), ("rgba(139,92,246,0.22)", "#C4B5FD")),
    "ooxml_escape": (("#FFF4E5", "#B25E09"), ("rgba(245,158,11,0.22)", "#FCD34D")),
    "emoji":        (("#FFE4F1", "#C2255C"), ("rgba(236,72,153,0.22)", "#F9A8D4")),
    "dingbat":      (("#FFE4F1", "#C2255C"), ("rgba(236,72,153,0.22)", "#F9A8D4")),
}


def priority_badge_colors(priority: str) -> tuple[str, str]:
    light_pair, dark_pair = _PRIORITY_PALETTE.get(priority, _PRIORITY_PALETTE["medium"])
    return dark_pair if CURRENT_THEME == "dark" else light_pair


def cleaner_badge_colors(rule_id: str) -> tuple[str, str]:
    default_pair = (("#EEEEEE", "#555555"), ("rgba(236,239,244,0.12)", "#CBD5E1"))
    light_pair, dark_pair = _CLEANER_BADGE_PALETTE.get(rule_id, default_pair)
    return dark_pair if CURRENT_THEME == "dark" else light_pair


def duplicate_badge_colors() -> tuple[str, str]:
    if CURRENT_THEME == "dark":
        return "rgba(239,68,68,0.22)", "#FCA5A5"
    return "#FCEBEB", "#A32D2D"


def remind_badge_colors(active: bool = True) -> tuple[str, str]:
    if not active:
        return _SEGMENTED_BG, _TEXT_TERTIARY
    if CURRENT_THEME == "dark":
        return "rgba(59,130,246,0.22)", "#93C5FD"
    return "#E6ECFB", "#2B5CE6"


def set_theme(theme: str) -> None:
    """切换主题（'light' / 'dark'）并更新所有颜色变量。"""
    global CURRENT_THEME
    if theme not in ("light", "dark"):
        return
    column = 0 if theme == "light" else 1
    module_globals = globals()
    for name, values in _THEME_TABLE.items():
        module_globals[name] = values[column]

    global _SHADOW_LIGHT, _SHADOW_HOVER, _SHADOW_SIDEBAR
    if theme == "dark":
        _SHADOW_LIGHT = QColor(0, 0, 0, 110)
        _SHADOW_HOVER = QColor(0, 0, 0, 150)
        _SHADOW_SIDEBAR = QColor(0, 0, 0, 140)
    else:
        _SHADOW_LIGHT = QColor(9, 9, 11, 16)
        _SHADOW_HOVER = QColor(9, 9, 11, 26)
        _SHADOW_SIDEBAR = QColor(9, 9, 11, 22)

    CURRENT_THEME = theme
    _sync_imported_modules()


def _sync_imported_modules() -> None:
    import sys

    own = sys.modules.get(__name__)
    module_globals = globals()
    for mod in list(sys.modules.values()):
        if mod is None or mod is own:
            continue
        for name in _COLOR_NAMES:
            if hasattr(mod, name):
                setattr(mod, name, module_globals[name])


def get_theme() -> str:
    return CURRENT_THEME


_BACKDROP_TINT = {
    "light": "rgba(243,245,250,0.74)",
    "dark": "rgba(11,13,19,0.72)",
}


def backdrop_tint() -> str:
    return _BACKDROP_TINT[CURRENT_THEME]


def apply_palette(app: QApplication) -> None:
    from PySide6.QtGui import QPalette

    palette = app.palette()
    palette.setColor(QPalette.ColorRole.PlaceholderText, to_qcolor(_TEXT_TERTIARY))
    palette.setColor(QPalette.ColorRole.WindowText, to_qcolor(_TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.Text, to_qcolor(_TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.Window, to_qcolor(_BG))
    palette.setColor(QPalette.ColorRole.Base, to_qcolor(_INPUT_BG))
    palette.setColor(QPalette.ColorRole.Highlight, to_qcolor(_ACCENT))
    app.setPalette(palette)


def surface_shadow(widget: QWidget, hover: bool = False) -> None:
    shadow = QGraphicsDropShadowEffect(widget)
    shadow.setBlurRadius(26 if hover else 20)
    shadow.setXOffset(0)
    shadow.setYOffset(4 if hover else 2)
    shadow.setColor(_SHADOW_HOVER if hover else _SHADOW_LIGHT)
    widget.setGraphicsEffect(shadow)


def animate_fade_in(widget: QWidget, duration: int = 165,
                    start_pos: tuple[int, int] | None = None,
                    parent=None) -> None:
    """淡入 + 轻微上浮（带中断复位保护，快速切页绝不偏移或卡顿）。"""
    if start_pos is None:
        start_pos = (0, 5)

    prev_pos_anim = getattr(widget, "_pos_anim", None)
    if isinstance(prev_pos_anim, QPropertyAnimation):
        try:
            if prev_pos_anim.state() == QAbstractAnimation.State.Running:
                prev_pos_anim.stop()
                resting = getattr(widget, "_resting_pos", QPoint(0, 0))
                widget.move(resting)
        except RuntimeError:
            pass

    prev_fade_anim = getattr(widget, "_fade_anim", None)
    if isinstance(prev_fade_anim, QPropertyAnimation):
        try:
            if prev_fade_anim.state() == QAbstractAnimation.State.Running:
                prev_fade_anim.stop()
        except RuntimeError:
            pass

    resting_pos = QPoint(0, 0) if widget.parentWidget() is not None else widget.pos()
    widget._resting_pos = resting_pos

    opacity = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(opacity)
    anim_opacity = QPropertyAnimation(opacity, b"opacity", parent or widget)
    anim_opacity.setDuration(duration)
    anim_opacity.setStartValue(0.0)
    anim_opacity.setEndValue(1.0)
    anim_opacity.setEasingCurve(QEasingCurve.Type.OutCubic)

    anim_pos = QPropertyAnimation(widget, b"pos", parent or widget)
    anim_pos.setDuration(duration)
    anim_pos.setStartValue(QPoint(resting_pos.x() + start_pos[0],
                                  resting_pos.y() + start_pos[1]))
    anim_pos.setEndValue(resting_pos)
    anim_pos.setEasingCurve(QEasingCurve.Type.OutCubic)

    widget._fade_anim = anim_opacity
    widget._pos_anim = anim_pos

    def cleanup() -> None:
        try:
            widget.setGraphicsEffect(None)
            widget.move(resting_pos)
        except RuntimeError:
            pass

    anim_opacity.finished.connect(cleanup)
    anim_opacity.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
    anim_pos.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)


FONT_STACK = ("'Microsoft YaHei UI', 'Microsoft YaHei', 'Segoe UI', "
              "'HarmonyOS Sans SC', 'Source Han Sans CN', 'Inter'")


def build_app_style(translucent: bool = False) -> str:
    """生成全局样式表（微渐变材质 + 精细边框）。"""
    ad = _ARROW_DOWN.replace("\\", "/")
    au = _ARROW_UP.replace("\\", "/")
    cl = _CHEV_LEFT.replace("\\", "/")
    cr = _CHEV_RIGHT.replace("\\", "/")
    chk = _CHECK.replace("\\", "/")

    return f"""
/* ══════════════════════════════════════════════════════════
   电商视觉工作台 v1.3 · 全局高定微渐变样式系统
   ══════════════════════════════════════════════════════════ */

QMainWindow, QDialog, QWidget {{
    background: {_BG}; color: {_TEXT_PRIMARY};
    font-family: {FONT_STACK};
    font-size: 14px; font-weight: 400;
}}
QWidget#Root {{ background: {_BG}; }}
QWidget#PageScrollViewport {{ background: transparent; }}
QToolTip {{
    background: {_CARD_BG}; color: {_TEXT_PRIMARY};
    border: 1px solid {_BORDER_STRONG}; border-radius: {R_SM}px;
    padding: 6px 10px; font-size: 12px;
}}

/* ── 顶部自绘标题栏 ── */
QFrame#TitleBar {{
    background: {_BG};
    border-bottom: 1px solid {_BORDER_LIGHT};
}}
QLabel#TitleBarName {{
    font-size: 13px; font-weight: 600; color: {_TEXT_SECONDARY};
    background: transparent; letter-spacing: -0.01em;
}}
QLabel#TitleBarBadge {{
    background: {_ACCENT_LIGHT}; color: {_ACCENT};
    border: 1px solid {_BORDER_NAV_CHECKED}; border-radius: 999px;
    padding: 1px 8px; font-size: 11px; font-weight: 700;
}}

/* ── 侧边栏：分组标题 + 胶囊 + 磁盘用量 ── */
QFrame#ProfileCapsule {{
    background: {_SURFACE_2};
    border: 1px solid {_BORDER};
    border-radius: 13px;
}}
QLabel#ProfileStatus {{
    color: {_SUCCESS}; font-size: 11px; font-weight: 600; background: transparent;
}}
QLabel#NavGroupLabel {{
    color: {_TEXT_TERTIARY}; font-size: 11px; font-weight: 700;
    letter-spacing: 0.6px; padding: 8px 10px 2px 10px; background: transparent;
}}
QLabel#DriveTitle {{
    color: {_TEXT_SECONDARY}; font-size: 12px; font-weight: 600; background: transparent;
}}
QLabel#DriveValue {{
    color: {_TEXT_TERTIARY}; font-size: 11px; background: transparent;
    font-family: 'Consolas', monospace;
}}
QLabel#DriveHint {{
    color: {_TEXT_TERTIARY}; font-size: 11px; background: transparent;
}}
QFrame#DriveWidget {{
    background: {_SURFACE_2}; border: 1px solid {_BORDER_LIGHT};
    border-radius: 12px;
}}
QFrame#Sidebar {{
    background: {_SIDEBAR_BG};
    border: 1px solid {_BORDER};
    border-radius: {R_XL}px;
}}
QLabel#BrandTitle {{
    color: {_TEXT_PRIMARY}; font-size: 13.5px; font-weight: 700;
    letter-spacing: -0.01em; background: transparent;
}}
QLabel#BrandSub {{
    color: {_TEXT_TERTIARY}; font-size: 11px; font-weight: 500;
    letter-spacing: 0.5px; background: transparent; text-transform: uppercase;
}}
QLabel#SidebarVersion {{
    color: {_TEXT_TERTIARY}; font-size: 11px; background: transparent;
}}

/* 导航项：选中 = 渐变电光蓝铺满 + 白字 */
QPushButton#NavButton {{
    border: 0; border-radius: 10px; padding: 0 12px; margin: 1px 0;
    min-height: 34px; text-align: left; background: transparent;
    color: {_SIDEBAR_TEXT}; font-size: 13px; font-weight: 500;
}}
QPushButton#NavButton:hover {{
    background: {_SIDEBAR_HOVER}; color: {_TEXT_PRIMARY};
}}
QPushButton#NavButton:checked {{
    background: {_ACCENT};
    color: #FFFFFF; font-weight: 600;
}}
QPushButton#NavButton:checked:pressed {{ background: {_ACCENT_PRESSED}; }}

QPushButton#ThemeButton {{
    border: 0; border-radius: {R_MD}px; padding: 0 12px; margin: 1px 0;
    min-height: 34px; text-align: left; background: transparent;
    color: {_TEXT_TERTIARY}; font-size: 13px; font-weight: 500;
}}
QPushButton#ThemeButton:hover {{ background: {_SIDEBAR_HOVER}; color: {_TEXT_PRIMARY}; }}

/* ── 页面与卡片语义化标题 / 正文 / 提示文本 ── */
QLabel#PageTitle {{
    font-size: 27px; font-weight: 700; color: {_TEXT_PRIMARY};
    letter-spacing: -0.02em; background: transparent;
}}
QLabel#HeroGreeting {{
    font-size: 25px; font-weight: 700; color: {_TEXT_PRIMARY};
    letter-spacing: -0.02em; background: transparent;
}}
QLabel#SectionTitle {{
    font-size: 16.5px; font-weight: 700; color: {_TEXT_PRIMARY};
    letter-spacing: -0.01em; background: transparent;
}}
QLabel#CardTitle {{
    font-size: 15px; font-weight: 600; color: {_TEXT_PRIMARY};
    background: transparent; border: 0;
}}
QLabel#SubSectionTitle {{
    font-size: 13.5px; font-weight: 700; color: {_TEXT_PRIMARY};
    background: transparent; border: 0;
}}
QLabel#DropZoneTitle {{
    font-size: 14px; font-weight: 600; color: {_TEXT_PRIMARY};
    background: transparent; border: 0;
}}
QLabel#PageSubtitle {{
    font-size: 14px; font-weight: 400; color: {_TEXT_SECONDARY};
    line-height: 1.5; background: transparent;
}}
QLabel#BodyLabel {{
    font-size: 14px; font-weight: 400; color: {_TEXT_SECONDARY};
    background: transparent; border: 0;
}}
QLabel#HintLabel {{
    font-size: 13px; font-weight: 400; color: {_TEXT_TERTIARY};
    background: transparent; border: 0;
}}
QLabel#TitleTag {{
    font-size: 11px; font-weight: 700; color: {_TEXT_TERTIARY};
    letter-spacing: 2px; background: transparent; padding-top: 8px;
}}
QLabel#Muted {{ color: {_TEXT_TERTIARY}; font-weight: 400; background: transparent; border: 0; }}
QLabel#Caption {{ font-size: 13px; color: {_TEXT_TERTIARY}; background: transparent; border: 0; }}
QLabel#StatValue {{
    font-size: 26px; font-weight: 700; color: {_TEXT_PRIMARY};
    background: transparent; border: 0; letter-spacing: -0.02em;
}}
QLabel#StatValue[accent="true"] {{ color: {_ACCENT}; }}
QLabel#StatValueCompact {{
    font-size: 18px; font-weight: 600; color: {_TEXT_PRIMARY};
    background: transparent; border: 0; letter-spacing: -0.01em;
}}
QLabel#StatValueCompact[accent="true"] {{ color: {_ACCENT}; }}

/* ── 按钮 ── */
QPushButton {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {_SURFACE_TOP}, stop:1 {_SURFACE_BOTTOM});
    border: 1px solid {_BORDER}; border-radius: {R_MD}px;
    padding: 7px 15px; color: {_TEXT_SECONDARY};
    font-size: 13px; font-weight: 500; min-height: 20px;
}}
QPushButton:hover {{
    background: {_SURFACE_TOP}; border-color: {_BORDER_STRONG}; color: {_TEXT_PRIMARY};
}}
QPushButton:pressed {{ background: {_SURFACE_2}; color: {_TEXT_PRIMARY}; }}
QPushButton:disabled {{
    background: {_SURFACE_2}; border-color: {_BORDER_LIGHT};
    color: {_TEXT_TERTIARY};
}}
QPushButton#PrimaryButton {{
    background: {_ACCENT};
    border: 1px solid {_ACCENT}; color: #FFFFFF;
    border-radius: {R_MD}px; padding: 7px 18px; font-size: 13px; font-weight: 600;
}}
QPushButton#PrimaryButton:hover {{ background: {_ACCENT_HOVER}; border-color: {_ACCENT_HOVER}; }}
QPushButton#PrimaryButton:pressed {{ background: {_ACCENT_PRESSED}; border-color: {_ACCENT_PRESSED}; }}
QPushButton#PrimaryButton:disabled {{
    background: {_SURFACE_2}; border-color: {_BORDER_LIGHT}; color: {_TEXT_TERTIARY};
}}
QPushButton#GhostButton {{
    border: 0; background: transparent; color: {_TEXT_TERTIARY};
    font-size: 12px; font-weight: 600; padding: 2px 10px;
    border-radius: {R_SM}px; min-height: 24px;
}}
QPushButton#GhostButton:hover {{
    color: {_TEXT_PRIMARY}; background: {_SIDEBAR_HOVER};
}}
QPushButton#GhostAccentButton {{
    border: 1px solid {_BORDER_NAV_CHECKED}; background: {_ACCENT_LIGHT}; color: {_ACCENT};
    font-size: 12px; font-weight: 600; padding: 2px 10px;
    border-radius: {R_SM}px; min-height: 24px;
}}
QPushButton#GhostAccentButton:hover {{
    background: {_ACCENT_SOFT}; border-color: {_ACCENT};
}}
QPushButton#DangerIconButton {{
    color: {_TEXT_TERTIARY}; background: transparent; border: none;
    border-radius: 6px; font-size: 16px; font-weight: 700; padding: 0; min-height: 24px;
}}
QPushButton#DangerIconButton:hover {{
    color: #FFFFFF; background: {_DANGER};
}}

/* ── 输入 ── */
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QDateEdit, QTimeEdit, QSpinBox {{
    background: {_INPUT_BG}; border: 1px solid {_INPUT_BORDER};
    border-radius: {R_MD}px; padding: 8px 12px; color: {_TEXT_PRIMARY};
    font-size: 13.5px; selection-background-color: {_ACCENT_SOFT};
    selection-color: {_TEXT_PRIMARY};
}}
QLineEdit:hover, QTextEdit:hover, QPlainTextEdit:hover,
QComboBox:hover, QDateEdit:hover, QTimeEdit:hover, QSpinBox:hover {{
    border-color: {_BORDER_STRONG};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,
QComboBox:focus, QDateEdit:focus, QTimeEdit:focus, QSpinBox:focus {{
    border-color: {_ACCENT}; background: {_INPUT_BG};
}}
QLineEdit:disabled, QTextEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{
    background: {_SURFACE_2}; color: {_TEXT_TERTIARY};
}}
QLineEdit#SearchField {{
    background: {_INPUT_BG}; border: 1px solid {_INPUT_BORDER};
    border-radius: {R_MD}px; padding: 0 14px; font-size: 13.5px;
    color: {_TEXT_PRIMARY};
}}
QLineEdit#SearchField:focus {{ border-color: {_ACCENT}; }}

/* ── 下拉 ── */
QComboBox::drop-down, QDateEdit::drop-down, QTimeEdit::drop-down {{ border: 0; width: 24px; }}
QComboBox::down-arrow, QDateEdit::down-arrow {{ image: url({ad}); width: 11px; height: 7px; }}
QComboBox QAbstractItemView {{
    background: {_CARD_BG}; border: 1px solid {_BORDER};
    border-radius: {R_MD}px; padding: 4px; outline: 0; font-size: 13.5px;
    selection-background-color: {_ACCENT}; selection-color: #FFFFFF;
}}

/* ── 数字框 ── */
QSpinBox::up-button {{ subcontrol-origin: border; subcontrol-position: top right; width: 18px; border: 0; background: transparent; }}
QSpinBox::down-button {{ subcontrol-origin: border; subcontrol-position: bottom right; width: 18px; border: 0; background: transparent; }}
QSpinBox::up-arrow {{ image: url({au}); width: 11px; height: 7px; }}
QSpinBox::down-arrow {{ image: url({ad}); width: 11px; height: 7px; }}
QSpinBox::up-button:hover {{ background: {_ACCENT_LIGHT}; border-top-right-radius: 11px; }}
QSpinBox::down-button:hover {{ background: {_ACCENT_LIGHT}; border-bottom-right-radius: 11px; }}

/* ── 日历 ── */
QCalendarWidget QWidget {{ background: {_CARD_BG}; color: {_TEXT_PRIMARY}; font-size: 12.5px; }}
QCalendarWidget QToolButton {{
    background: transparent; border: 0; color: {_TEXT_PRIMARY};
    font-weight: 500; padding: 5px 10px; border-radius: {R_SM}px; font-size: 13px;
}}
QCalendarWidget QToolButton:hover {{ background: {_ACCENT_LIGHT}; }}
QCalendarWidget QToolButton#qt_calendar_prevmonth {{ image: url({cl}); width: 9px; height: 14px; }}
QCalendarWidget QToolButton#qt_calendar_nextmonth {{ image: url({cr}); width: 9px; height: 14px; }}
QCalendarWidget QToolButton#qt_calendar_monthbutton,
QCalendarWidget QToolButton#qt_calendar_yearbutton {{ font-size: 13px; padding: 3px 8px; }}
QCalendarWidget QAbstractItemView {{
    background: {_CARD_BG}; selection-background-color: {_ACCENT};
    selection-color: #FFFFFF; outline: 0;
}}
QCalendarWidget QAbstractItemView:disabled {{ color: {_TEXT_TERTIARY}; }}

/* ── 分段筛选 ── */
QFrame#Segmented {{ background: {_SEGMENTED_BG}; border: 1px solid {_BORDER_LIGHT}; border-radius: {R_MD}px; }}
QPushButton#FilterButton {{
    background: transparent; border: 1px solid transparent; border-radius: {R_SM}px;
    padding: 6px 16px; color: {_TEXT_TERTIARY}; font-size: 13px; font-weight: 500;
}}
QPushButton#FilterButton:hover {{ background: {_SURFACE_3}; color: {_TEXT_SECONDARY}; }}
QPushButton#FilterButton:checked {{
    background: {_SEGMENTED_ACTIVE_BG}; color: {_TEXT_PRIMARY}; font-weight: 600;
    border: 1px solid {_BORDER};
}}

/* ── 产品库：系列树 / 密度滑块 / 检视面板 ── */
QLineEdit#RailSearch {{
    background: {_SURFACE_2}; border: 1px solid transparent; border-radius: {R_SM}px;
    padding: 0 8px; color: {_TEXT_PRIMARY}; font-size: 12px;
}}
QLineEdit#RailSearch:focus {{ background: {_INPUT_BG}; border-color: {_ACCENT}; }}

QFrame#RailTip {{
    background: {_SURFACE_2}; border: 1px solid {_BORDER_LIGHT}; border-radius: {R_MD}px;
}}
QLabel#RailTipTitle {{ color: {_TEXT_PRIMARY}; font-size: 11px; font-weight: 600; background: transparent; }}
QLabel#RailTipText {{ color: {_TEXT_TERTIARY}; font-size: 10px; background: transparent; line-height: 1.4; }}

QCheckBox#AlphaCheck {{
    color: {_TEXT_SECONDARY}; font-size: 12px; spacing: 6px; padding: 0 4px;
}}
QCheckBox#AlphaCheck::indicator {{
    width: 15px; height: 15px; border: 1.5px solid {_BORDER_STRONG};
    border-radius: 4px; background: {_CARD_BG};
}}
QCheckBox#AlphaCheck::indicator:hover {{ border-color: {_ACCENT}; }}
QCheckBox#AlphaCheck::indicator:checked {{
    background: {_ACCENT}; border-color: {_ACCENT}; image: url("{chk}");
}}

/* ── 产品库左栏（彩色系列树） ── */
QPushButton#RailSeg {{
    background: {_CARD_BG}; border: 1px solid {_BORDER_LIGHT}; border-radius: {R_SM}px;
    color: {_TEXT_SECONDARY}; font-size: 11px; font-weight: 600; padding: 0 12px;
}}
QPushButton#RailSeg:hover {{ border-color: {_ACCENT}; color: {_TEXT_PRIMARY}; }}
QPushButton#RailSeg:checked {{
    background: {_ACCENT}; border-color: {_ACCENT}; color: #FFFFFF;
}}
QFrame#ProductRail {{ background: {_SURFACE_2}; border: 1px solid {_BORDER_LIGHT}; border-radius: {R_LG}px; }}
QFrame#RailHead {{ background: transparent; border-bottom: 1px solid {_BORDER_LIGHT}; }}
QLabel#RailTitle {{ color: {_TEXT_PRIMARY}; font-size: 13px; font-weight: 700; background: transparent; }}
QLabel#RailTotalPill {{
    color: {_TEXT_SECONDARY}; background: {_CARD_BG}; border: 1px solid {_BORDER_LIGHT};
    border-radius: 999px; padding: 2px 9px; font-size: 10px; font-weight: 600;
}}
QLabel#RailLegend {{ color: {_TEXT_TERTIARY}; font-size: 10px; background: transparent; }}
QScrollArea#RailScroll {{ background: transparent; border: 0; }}
QScrollArea#RailScroll > QWidget > QWidget {{ background: transparent; }}
QWidget#RailHolder {{ background: transparent; }}
QFrame#RailFoot {{
    background: {_CARD_BG}; border-top: 1px solid {_BORDER_LIGHT};
    border-bottom-left-radius: {R_LG}px; border-bottom-right-radius: {R_LG}px;
}}
QLabel#RailGuard {{ color: {_TEXT_PRIMARY}; font-size: 11px; font-weight: 600; background: transparent; }}
QLabel#RailQualityPill {{
    color: {_SUCCESS}; background: {_ACCENT_LIGHT}; border: 1px solid {_BORDER_LIGHT};
    border-radius: 999px; padding: 1px 8px; font-size: 10px; font-weight: 700;
}}
QLabel#RailFootText, QLabel#RailFootHint {{ color: {_TEXT_TERTIARY}; font-size: 10px; background: transparent; }}
QComboBox#SourceCombo {{
    background: {_CARD_BG}; border: 1px solid {_BORDER}; border-radius: {R_SM}px;
    padding: 0 8px; color: {_TEXT_PRIMARY}; font-size: 12px; font-weight: 600;
}}
QComboBox#SourceCombo:hover {{ border-color: {_ACCENT}; }}
QComboBox#SourceCombo::drop-down {{ border: 0; width: 20px; }}
QComboBox#SourceCombo::down-arrow {{ image: url("{ad}"); width: 10px; height: 10px; }}
QComboBox#SourceCombo QAbstractItemView {{
    background: {_CARD_BG}; border: 1px solid {_BORDER}; border-radius: {R_SM}px;
    padding: 4px; color: {_TEXT_PRIMARY}; font-size: 12px;
    selection-background-color: {_ACCENT}; selection-color: #FFFFFF;
    outline: 0;
}}
QLabel#CountPill {{
    background: {_SURFACE_2}; color: {_TEXT_TERTIARY}; border-radius: 999px;
    padding: 2px 10px; font-size: 11px; font-weight: 500;
}}

QLabel#SegmentHint {{ color: {_TEXT_TERTIARY}; font-size: 11px; padding: 0 6px 0 4px; background: transparent; }}
QPushButton#SegmentButton {{
    background: transparent; border: 0; border-radius: 6px; padding: 4px 10px;
    color: {_TEXT_TERTIARY}; font-size: 11px; font-weight: 500;
}}
QPushButton#SegmentButton:hover {{ color: {_TEXT_PRIMARY}; }}
QPushButton#SegmentButton:checked {{
    background: {_SEGMENTED_ACTIVE_BG}; color: {_ACCENT}; font-weight: 700;
    border: 1px solid {_BORDER_LIGHT};
}}

QLabel#DensityLabel {{
    color: {_TEXT_SECONDARY}; font-size: 11px; background: transparent;
    font-family: 'Consolas', monospace;
}}
QSlider#DensitySlider::groove:horizontal {{
    height: 4px; background: {_BORDER_STRONG}; border-radius: 2px;
}}
QSlider#DensitySlider::sub-page:horizontal {{ background: {_ACCENT}; border-radius: 2px; }}
QSlider#DensitySlider::handle:horizontal {{
    background: {_CARD_BG}; border: 2px solid {_ACCENT}; width: 10px; height: 10px;
    margin: -4px 0; border-radius: 7px;
}}

QToolButton#InspectorToggle {{
    background: {_CARD_BG}; border: 1px solid {_BORDER}; border-radius: {R_SM}px;
    padding: 5px 7px;
}}
QToolButton#InspectorToggle:hover {{ border-color: {_ACCENT}; }}
QToolButton#InspectorToggle:checked {{
    background: {_ACCENT_LIGHT}; border-color: {_ACCENT};
}}
QToolButton#InspectorClose {{ background: transparent; border: 0; padding: 3px; }}
QToolButton#InspectorClose:hover {{ background: {_SURFACE_2}; border-radius: 6px; }}

QLabel#InspectorName {{ color: {_TEXT_PRIMARY}; font-size: 12px; font-weight: 600; background: transparent; }}
QFrame#SpecBox {{ background: {_SURFACE_2}; border: 1px solid {_BORDER_LIGHT}; border-radius: {R_MD}px; }}
QLabel#SpecKey {{ color: {_TEXT_TERTIARY}; font-size: 11px; background: transparent; }}
QLabel#SpecValue {{
    color: {_TEXT_PRIMARY}; font-size: 11px; background: transparent;
    font-family: 'Consolas', monospace;
}}
QLabel#TagChip {{
    background: {_SURFACE_2}; color: {_TEXT_SECONDARY}; border: 1px solid {_BORDER_LIGHT};
    border-radius: 5px; padding: 2px 8px; font-size: 10.5px; font-weight: 500;
}}

/* ── 卡片、微渐变立体表面与底部操作栏 ── */
QFrame#Card, QFrame#GlassCard, QFrame#Surface, QFrame#ActionBar {{
    background: {_CARD_BG};
    border: 1px solid {_BORDER}; border-radius: {R_LG}px;
}}
QFrame#Card:hover, QFrame#Surface:hover {{ border-color: {_BORDER_STRONG}; }}

QFrame#LaunchpadCard {{
    background: {_CARD_BG};
    border: 1px solid {_BORDER}; border-radius: 14px;
}}
QFrame#LaunchpadCard:hover {{
    border-color: {_ACCENT};
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {_SURFACE_TOP}, stop:1 {_ACCENT_LIGHT});
}}

QFrame#FilesBar {{
    background: {_SURFACE_2}; border: 1px solid {_BORDER}; border-radius: {R_MD}px;
}}
QFrame#PreviewPanel {{
    background: {_CARD_BG}; border: 1px solid {_BORDER}; border-radius: {R_LG}px;
}}
QFrame#Divider {{ background: {_BORDER_LIGHT}; border: 0; max-height: 1px; min-height: 1px; }}
QFrame#EmptyState {{
    background: {_CARD_BG}; border: 1px solid {_BORDER}; border-radius: {R_LG}px;
}}
QFrame#EmptyStateVisual {{
    background: {_ACCENT_LIGHT}; border: 1px solid {_BORDER_NAV_CHECKED};
    border-radius: 20px;
}}
QLabel#EmptyStateIcon {{ background: transparent; border: 0; }}
QLabel#EmptyStateTitle {{
    color: {_TEXT_PRIMARY}; background: transparent; border: 0;
    font-size: 19px; font-weight: 700;
}}
QLabel#EmptyStateDetail {{
    color: {_TEXT_SECONDARY}; background: transparent; border: 0;
    font-size: 13.5px;
}}
QLabel#StatusPill {{
    background: {_ACCENT_LIGHT}; color: {_ACCENT};
    border: 1px solid {_BORDER_NAV_CHECKED}; border-radius: 999px;
    padding: 5px 12px; font-size: 12px; font-weight: 600;
}}
QFrame#PathChip {{
    background: {_SURFACE_2}; border: 1px solid {_BORDER_LIGHT};
    border-radius: {R_SM}px;
}}
QLabel#PathText {{
    color: {_TEXT_SECONDARY}; font-size: 12px; background: transparent;
}}
QLabel#Kbd {{
    color: {_TEXT_TERTIARY}; background: {_SURFACE_2};
    border: 1px solid {_BORDER_LIGHT}; border-radius: {R_XS}px;
    padding: 4px 8px; font-size: 11px; font-weight: 600;
}}
QFrame#Hero {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0.8,
        stop:0 {_SURFACE_TOP}, stop:0.55 {_SURFACE_TOP}, stop:1 {_ACCENT_LIGHT});
    border: 1px solid {_BORDER}; border-radius: {R_XL}px;
}}

/* ── 列表 ── */
QListWidget {{ background: transparent; border: 0; outline: 0; }}
QListWidget::item {{ border: 0; border-radius: {R_MD}px; margin: 1px 0; }}
QListWidget::item:hover {{ background: {_ACCENT_LIGHT}; }}
QListWidget::item:selected {{ background: {_ACCENT_LIGHT}; color: {_TEXT_PRIMARY}; }}

/* ── 表格 ── */
QTableView, QTableWidget {{
    background: {_CARD_BG}; border: 1px solid {_BORDER};
    border-radius: {R_MD}px; gridline-color: {_BORDER_LIGHT};
    selection-background-color: {_ACCENT_LIGHT}; selection-color: {_TEXT_PRIMARY};
    alternate-background-color: {_SURFACE_2}; outline: 0;
}}
QTableWidget#BorderlessTable {{
    background: transparent; border: 0;
    selection-background-color: {_ACCENT_LIGHT}; selection-color: {_TEXT_PRIMARY};
    alternate-background-color: {_SURFACE_2}; outline: 0;
}}
QTableWidget#BorderlessTable::item {{
    padding: 6px 8px; border: 0;
}}
QHeaderView::section {{
    background: {_SURFACE_2}; color: {_TEXT_SECONDARY}; border: 0;
    border-bottom: 1px solid {_BORDER}; padding: 8px 10px;
    font-size: 12px; font-weight: 600;
}}
QTableWidget#BorderlessTable QHeaderView::section {{
    background: transparent; color: {_TEXT_SECONDARY}; border: 0;
    border-bottom: 1px solid {_BORDER_LIGHT}; padding: 6px 8px;
    font-size: 12px; font-weight: 700;
}}

/* ── 勾选框 ── */
QCheckBox {{ spacing: 8px; background: transparent; font-size: 13.5px; color: {_TEXT_PRIMARY}; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border: 1.5px solid {_BORDER_STRONG};
    border-radius: {R_XS}px; background: {_CARD_BG};
}}
QCheckBox::indicator:hover {{ border-color: {_ACCENT}; }}
QCheckBox::indicator:checked {{
    background: {_ACCENT}; border-color: {_ACCENT}; image: url({chk});
}}

/* ── 标签页 ── */
QTabWidget::pane {{ border: 0; background: transparent; }}
QTabBar {{ qproperty-drawBase: 0; }}
QTabBar::tab {{
    background: transparent; color: {_TEXT_TERTIARY}; padding: 8px 4px;
    margin-right: 24px; border: 0; border-bottom: 2px solid transparent;
    font-size: 13.5px; font-weight: 500;
}}
QTabBar::tab:hover {{ color: {_TEXT_SECONDARY}; }}
QTabBar::tab:selected {{ color: {_TEXT_PRIMARY}; font-weight: 600; border-bottom: 2px solid {_ACCENT}; }}

/* ── 滚动条 ── */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{
    background: {_SCROLLBAR_THUMB}; border-radius: 999px; min-height: 32px;
}}
QScrollBar::handle:vertical:hover {{ background: {_SCROLLBAR_THUMB_HOVER}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{
    background: {_SCROLLBAR_THUMB}; border-radius: 999px; min-width: 32px;
}}
QScrollBar::handle:horizontal:hover {{ background: {_SCROLLBAR_THUMB_HOVER}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ── 其他 ── */
QScrollArea {{ border: 0; background: transparent; }}
QSplitter::handle {{ background: transparent; width: 6px; }}
QProgressBar {{
    border: 0; background: {_SKELETON_BG}; border-radius: 3px;
    height: 6px; max-height: 6px;
}}
QProgressBar::chunk {{ background: {_ACCENT}; border-radius: 3px; }}
QMenu {{
    background: {_CARD_BG}; border: 1px solid {_BORDER};
    border-radius: {R_MD}px; padding: 6px; font-size: 13px;
}}
QMenu::item {{ border-radius: {R_SM}px; padding: 7px 14px; color: {_TEXT_SECONDARY}; }}
QMenu::item:selected {{ background: {_ACCENT_LIGHT}; color: {_TEXT_PRIMARY}; }}
QMenu::separator {{ height: 1px; background: {_BORDER_LIGHT}; margin: 4px 8px; }}
"""


APP_STYLE = build_app_style(False)
