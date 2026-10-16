# -*- coding: utf-8 -*-
"""全应用统一的滚轮步长。

Qt 默认「滚一格」= `wheelScrollLines`（系统值，通常 3）× 滚动条的 `singleStep`，
而 `singleStep` 在不同控件里差别巨大：

| 控件 | singleStep | 一格实际滚 |
|---|---|---|
| `QScrollArea` | 20 | 60px（正常） |
| `QListWidget` / `QListView`（图标模式） | **一个条目的高度**（产品卡片 207px） | 3×207=621 → 被页高一钳 = **整整一页** |
| `QTableWidget` | 1 | **3px**（几乎不动） |

结果就是整个工作台「滚一下翻一整页」「有的东西只能看到一半」，或者干脆滚不动。

这里统一成**固定像素**：一格滚 `WHEEL_STEP_PX`，跟内容尺寸无关；触控板/高精度滚轮
给的是像素增量，按像素跟手。条目特别大的视图可以实现 `wheel_step()` 自定义步长
（产品卡片网格按「一行卡片」滚，保证卡片不被切一半），
还可以实现 `snap_scroll()` 在滚动后把落点对齐（同样是为了不切条目）。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QAbstractScrollArea, QWidget

#: 一格滚轮滚多少像素。约等于 3 行 32px 的行 —— 和 Windows 默认手感接近。
WHEEL_STEP_PX = 96

#: 查不到行高时，按一行 18px 折算（普通列表行高就在这附近）
DEFAULT_ROW_PX = 18


def wheel_step_for(area: QAbstractScrollArea, default: int = WHEEL_STEP_PX) -> int:
    """这个滚动区域一格该滚多少像素。

    优先让视图自己说（`wheel_step()`）；否则：**条目比一格还高时一格滚一行**，
    不然缩略图墙那种一行 240px 的视图，按 96px 滚会显得「滚半天不动一点」。
    """
    provider = getattr(area, "wheel_step", None)
    if callable(provider):
        try:
            value = int(provider())
        except Exception:  # noqa: BLE001 - 视图自己算错了就退回默认
            value = 0
        if value > 0:
            return value
    row = _row_height(area)
    if row > default:
        # 一行最多占视口的 60% —— 免得遇到特别高的条目时一格又变成「翻大半页」
        viewport = area.viewport().height()
        return min(row, max(default, int(viewport * 0.6)) if viewport > 0 else row)
    return default


def _row_height(area: QAbstractScrollArea) -> int:
    """条目视图里「一行」多高（像素）。

    ⚠ 图标/网格模式要先用 `gridSize()`：那里 `sizeHintForRow(0)` 返回的是**文字行高**
    （实测 12px），而格子高是 240px —— 用它算会得出荒谬的步长。
    """
    gets_grid = getattr(area, "gridSize", None)
    if callable(gets_grid):
        try:
            size = gets_grid()
            if size.isValid() and size.height() > 0:
                return int(size.height())
        except Exception:  # noqa: BLE001
            pass
    for name in ("sizeHintForRow", "rowHeight"):
        getter = getattr(area, name, None)
        if not callable(getter):
            continue
        try:
            value = int(getter(0))
        except Exception:  # noqa: BLE001 - 没有行、或超出范围
            value = 0
        if value > 0:
            return value
    return DEFAULT_ROW_PX


def uses_item_units(area: QAbstractScrollArea) -> bool:
    """滚动条的刻度是「条目数」吗（而不是像素）。

    ⚠ 不能只看 `verticalScrollMode()`：图标模式（IconMode）**报的是 ScrollPerItem，
    但刻度其实是像素** —— 实测缩略图墙 pageStep=698（= 视口高），按条目算会把一格
    算成 8 像素，滚轮几乎不动。可靠判据：刻度是像素时 `pageStep` 就等于视口高度，
    是条目数时 `pageStep` 是「可见几个条目」。
    """
    bar = area.verticalScrollBar()
    viewport = area.viewport().height()
    if bar is None or viewport <= 0:
        return False
    return abs(bar.pageStep() - viewport) > max(2, viewport * 0.25)


def pixels_to_units(area: QAbstractScrollArea, pixels: float) -> float:
    """把「像素」换算成滚动条的刻度单位。

    ⚠ 刻度单位不一定是像素：`QScrollArea` 是像素，但 `QAbstractItemView` 默认是
    **按条目滚动**（`ScrollPerItem`），刻度就是条目数 —— 直接拿像素当刻度去设值，
    一格会滚出几十个条目，比原来更糟。所以要先看滚动模式再换算。
    """
    if not uses_item_units(area):
        return pixels                        # 刻度就是像素
    return pixels / max(1, _row_height(area))   # 逐条目：折算成几个条目


def _step_units(area: QAbstractScrollArea, step_px: int) -> float:
    """一格滚多少「刻度」。逐条目视图至少要滚一个条目 —— 否则条目比一格还高时
    四舍五入会变成 0，滚轮直接失灵（一行都不动）。"""
    units = pixels_to_units(area, step_px)
    if uses_item_units(area):
        return max(1.0, units)
    return units


def scroll_by_wheel(area: QAbstractScrollArea, event, default: int = WHEEL_STEP_PX,
                    pending: dict | None = None) -> bool:
    """按统一策略滚一次；返回 True 表示这次滚轮事件已经被处理掉了。

    `pending`：逐条目视图上用来攒触控板的小增量（传 dict 就启用累积，
    不传就是无状态，每次各自取整）。
    """
    bar = area.verticalScrollBar()
    if bar is None or bar.maximum() <= 0 or not bar.isVisible():
        return False

    pixel = event.pixelDelta().y()
    if pixel:                                # 触控板 / 高精度滚轮
        units = pixels_to_units(area, pixel)
        if not uses_item_units(area):
            bar.setValue(bar.value() - round(units))     # 像素刻度：直接跟手
        else:
            # 条目刻度：一格一格地走；余量攒起来，否则细碎的触控板事件
            # 每次都被取整成 0，在列表上根本滚不动
            total = units + (pending.get(id(area), 0.0) if pending is not None else 0.0)
            whole = int(total)               # 朝零取整
            if pending is not None:
                pending[id(area)] = total - whole
            if whole:
                bar.setValue(bar.value() - whole)
        event.accept()
        return True

    notches = event.angleDelta().y() / 120.0
    if not notches:
        return False

    units = _step_units(area, wheel_step_for(area, default))
    bar.setValue(bar.value() - round(notches * units))
    snap = getattr(area, "snap_scroll", None)
    if callable(snap):
        snap()
    event.accept()
    return True


class WheelStepFilter(QObject):
    """装在 `QApplication` 上的全局滚轮过滤器，让所有滚动区域都走统一策略。

    这是**唯一**的滚轮策略入口：修一处，整个工作台生效。
    """

    def __init__(self, step: int = WHEEL_STEP_PX, parent=None):
        super().__init__(parent)
        self.step = step
        # 触控板在「逐条目」视图上的余量，按控件记（像素）
        self._pending: dict[int, float] = {}

    def eventFilter(self, obj, event):
        if event.type() != QEvent.Type.Wheel:
            return False
        # 应用级过滤器会收到非控件对象的事件，先挡掉
        if not isinstance(obj, QWidget):
            return False
        # 滚动区域的滚轮事件是发到 viewport 上的
        area = obj.parentWidget()
        if not isinstance(area, QAbstractScrollArea) or obj is not area.viewport():
            return False
        # 带修饰键的（Ctrl 缩放 / Shift 横向）不拦
        if event.modifiers() != Qt.KeyboardModifier.NoModifier:
            return False
        return scroll_by_wheel(area, event, self.step, self._pending)


def install_wheel_policy(app):
    """给应用装上统一滚轮策略（幂等，重复调用不会装两次）。"""
    existing = getattr(app, "_wheel_step_filter", None)
    if existing is not None:
        return existing
    filt = WheelStepFilter(parent=app)
    app.installEventFilter(filt)
    app._wheel_step_filter = filt        # 保住引用，别被 GC 回收
    return filt
