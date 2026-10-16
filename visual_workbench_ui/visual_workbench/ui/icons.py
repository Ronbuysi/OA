"""导航与界面图标 —— 全部用 QPainter 画，不依赖字体字形。

规格：18×18 画布，1.5px 描边，圆头圆角，颜色由调用方给。
包含：plus、upload（导入产品）、sparkles（AI星芒）、bolt（快捷闪电）、
check_circle（完成勾）、tag（系列标签）、edit（编辑）、restore、trash 等。
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

from .styles import to_qcolor  # noqa: F401  (对外仍然可用：icons.to_qcolor)

CANVAS = 18.0
INSET = 2.6
STROKE = 1.5

NAV_ICONS = (
    "home", "library", "archive", "task", "calendar", "chart", "cleaner", "sliders",
)


# ── 各图标的路径定义（都在 18×18 画布内） ──

def _home(p: QPainter, path: QPainterPath) -> None:
    path.moveTo(2.8, 8.4)
    path.lineTo(9, 3.0)
    path.lineTo(15.2, 8.4)
    p.drawPath(path)
    p.drawRoundedRect(QRectF(4.6, 7.6, 8.8, 7.6), 1.6, 1.6)


def _library(p: QPainter, path: QPainterPath) -> None:
    p.drawRoundedRect(QRectF(2.8, 4.2, 12.4, 9.6), 2.2, 2.2)
    p.drawEllipse(QPointF(6.4, 7.6), 1.05, 1.05)
    path.moveTo(3.9, 12.6)
    path.lineTo(7.4, 9.2)
    path.lineTo(10.1, 11.8)
    path.lineTo(11.9, 10.1)
    path.lineTo(14.1, 12.2)
    p.drawPath(path)


def _archive(p: QPainter, path: QPainterPath) -> None:
    p.drawRoundedRect(QRectF(3.0, 3.6, 12.0, 4.2), 1.6, 1.6)
    p.drawRoundedRect(QRectF(3.0, 10.2, 12.0, 4.2), 1.6, 1.6)
    path.moveTo(7.2, 5.7)
    path.lineTo(10.8, 5.7)
    p.drawPath(path)


def _task(p: QPainter, path: QPainterPath) -> None:
    path.moveTo(3.4, 9.6)
    path.lineTo(7.2, 13.2)
    path.lineTo(14.6, 5.0)
    p.drawPath(path)


def _calendar(p: QPainter, path: QPainterPath) -> None:
    p.drawRoundedRect(QRectF(2.8, 4.4, 12.4, 10.8), 2.2, 2.2)
    path.moveTo(2.8, 8.2)
    path.lineTo(15.2, 8.2)
    p.drawPath(path)
    p.drawLine(QPointF(6.4, 2.9), QPointF(6.4, 5.7))
    p.drawLine(QPointF(11.6, 2.9), QPointF(11.6, 5.7))


def _chart(p: QPainter, path: QPainterPath) -> None:
    for x, top in ((4.5, 9.8), (9.0, 4.6), (13.5, 7.4)):
        path.moveTo(x, 14.6)
        path.lineTo(x, top)
    p.drawPath(path)


def _cleaner(p: QPainter, path: QPainterPath) -> None:
    """四角星（魔棒 / 清理）。"""
    cx, cy, r_out, r_in = 9.0, 9.0, 6.4, 2.0
    start = -math.pi / 2
    path.moveTo(cx, cy - r_out)
    for step in range(1, 8):
        angle = start + step * math.pi / 4
        radius = r_in if step % 2 else r_out
        path.lineTo(cx + radius * math.cos(angle), cy + radius * math.sin(angle))
    path.closeSubpath()
    p.drawPath(path)


def _sliders(p: QPainter, path: QPainterPath) -> None:
    path.moveTo(2.9, 5.8)
    path.lineTo(15.1, 5.8)
    path.moveTo(2.9, 12.2)
    path.lineTo(15.1, 12.2)
    p.drawPath(path)
    p.drawEllipse(QPointF(6.6, 5.8), 1.85, 1.85)
    p.drawEllipse(QPointF(11.4, 12.2), 1.85, 1.85)


def _moon(p: QPainter, path: QPainterPath) -> None:
    p.drawArc(QRectF(3.4, 3.0, 11.2, 12.0), 60 * 16, 250 * 16)


def _sun(p: QPainter, path: QPainterPath) -> None:
    p.drawEllipse(QPointF(9.0, 9.0), 3.1, 3.1)
    for step in range(8):
        angle = step * math.pi / 4
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        path.moveTo(9 + 5.3 * cos_a, 9 + 5.3 * sin_a)
        path.lineTo(9 + 7.1 * cos_a, 9 + 7.1 * sin_a)
    p.drawPath(path)


def _search(p: QPainter, path: QPainterPath) -> None:
    p.drawEllipse(QPointF(8.1, 8.1), 5.0, 5.0)
    path.moveTo(11.8, 11.8)
    path.lineTo(15.2, 15.2)
    p.drawPath(path)


def _folder(p: QPainter, path: QPainterPath) -> None:
    path.moveTo(2.8, 13.6)
    path.lineTo(2.8, 5.4)
    path.lineTo(6.9, 5.4)
    path.lineTo(8.3, 7.3)
    path.lineTo(15.2, 7.3)
    path.lineTo(15.2, 13.6)
    path.closeSubpath()
    p.drawPath(path)


def _bottle(p: QPainter, path: QPainterPath) -> None:
    """产品瓶：瓶颈 + 瓶身 + 标签线（产品库图标）。"""
    path.moveTo(7.6, 2.8)
    path.lineTo(10.4, 2.8)
    path.lineTo(10.4, 5.6)
    path.lineTo(12.0, 7.6)
    path.lineTo(12.0, 14.6)
    path.lineTo(6.0, 14.6)
    path.lineTo(6.0, 7.6)
    path.lineTo(7.6, 5.6)
    path.closeSubpath()
    p.drawPath(path)
    label = QPainterPath()
    label.moveTo(6.0, 10.2)
    label.lineTo(12.0, 10.2)
    p.drawPath(label)


def _file(p: QPainter, path: QPainterPath) -> None:
    path.moveTo(4.2, 2.9)
    path.lineTo(10.3, 2.9)
    path.lineTo(13.8, 6.4)
    path.lineTo(13.8, 15.1)
    path.lineTo(4.2, 15.1)
    path.closeSubpath()
    p.drawPath(path)
    fold = QPainterPath()
    fold.moveTo(10.3, 2.9)
    fold.lineTo(10.3, 6.4)
    fold.lineTo(13.8, 6.4)
    p.drawPath(fold)


def _image(p: QPainter, path: QPainterPath) -> None:
    """图片（相框 + 远山）。"""
    p.drawRoundedRect(QRectF(2.8, 3.6, 12.4, 10.8), 2.2, 2.2)
    p.drawEllipse(QPointF(6.6, 7.2), 1.15, 1.15)
    path.moveTo(4.2, 13.2)
    path.lineTo(8.0, 9.4)
    path.lineTo(10.4, 11.8)
    path.lineTo(12.2, 10.1)
    path.lineTo(14.0, 11.9)
    p.drawPath(path)


def _copy(p: QPainter, path: QPainterPath) -> None:
    """复制（两张叠纸）。"""
    p.drawRoundedRect(QRectF(3.0, 3.0, 8.4, 8.4), 1.8, 1.8)
    p.drawRoundedRect(QRectF(6.6, 6.6, 8.4, 8.4), 1.8, 1.8)


def _external(p: QPainter, path: QPainterPath) -> None:
    """定位 / 外链（方框 + 逃逸箭头）。"""
    p.drawRoundedRect(QRectF(3.0, 6.0, 9.0, 9.0), 1.8, 1.8)
    path.moveTo(9.4, 8.6)
    path.lineTo(15.0, 3.0)
    p.drawPath(path)
    path.moveTo(10.6, 3.0)
    path.lineTo(15.0, 3.0)
    path.lineTo(15.0, 7.4)
    p.drawPath(path)


def _close(p: QPainter, path: QPainterPath) -> None:
    """关闭（叉号）。"""
    path.moveTo(4.8, 4.8)
    path.lineTo(13.2, 13.2)
    path.moveTo(13.2, 4.8)
    path.lineTo(4.8, 13.2)
    p.drawPath(path)


def _plus(p: QPainter, path: QPainterPath) -> None:
    """加号（继续添加）。"""
    path.moveTo(9.0, 4.0)
    path.lineTo(9.0, 14.0)
    path.moveTo(4.0, 9.0)
    path.lineTo(14.0, 9.0)
    p.drawPath(path)


def _upload(p: QPainter, path: QPainterPath) -> None:
    """上传 / 导入产品入库（向上箭头 + 底部托盘）。"""
    path.moveTo(9.0, 11.5)
    path.lineTo(9.0, 3.4)
    path.moveTo(5.8, 6.4)
    path.lineTo(9.0, 3.2)
    path.lineTo(12.2, 6.4)
    path.moveTo(3.2, 11.6)
    path.lineTo(3.2, 14.0)
    path.lineTo(14.8, 14.0)
    path.lineTo(14.8, 11.6)
    p.drawPath(path)


def _bolt(p: QPainter, path: QPainterPath) -> None:
    """闪电（高效 / 快捷动作）。"""
    path.moveTo(9.8, 2.6)
    path.lineTo(4.4, 9.8)
    path.lineTo(9.0, 9.8)
    path.lineTo(8.2, 15.4)
    path.lineTo(13.6, 8.2)
    path.lineTo(9.0, 8.2)
    path.closeSubpath()
    p.drawPath(path)


def _sparkles(p: QPainter, path: QPainterPath) -> None:
    """双星芒（AI / 视觉高光）。"""
    cx, cy, r_out, r_in = 7.8, 9.6, 5.0, 1.5
    start = -math.pi / 2
    path.moveTo(cx, cy - r_out)
    for step in range(1, 8):
        angle = start + step * math.pi / 4
        radius = r_in if step % 2 else r_out
        path.lineTo(cx + radius * math.cos(angle), cy + radius * math.sin(angle))
    path.closeSubpath()
    path.moveTo(13.6, 3.2)
    path.lineTo(13.6, 6.8)
    path.moveTo(11.8, 5.0)
    path.lineTo(15.4, 5.0)
    p.drawPath(path)


def _check_circle(p: QPainter, path: QPainterPath) -> None:
    """圆圈对勾。"""
    p.drawEllipse(QPointF(9.0, 9.0), 6.0, 6.0)
    path.moveTo(6.3, 9.2)
    path.lineTo(8.2, 11.1)
    path.lineTo(12.0, 7.1)
    p.drawPath(path)


def _tag(p: QPainter, path: QPainterPath) -> None:
    """分类标签。"""
    path.moveTo(3.2, 9.2)
    path.lineTo(3.2, 3.6)
    path.lineTo(8.8, 3.6)
    path.lineTo(14.8, 9.6)
    path.lineTo(9.2, 15.2)
    path.closeSubpath()
    p.drawPath(path)
    p.drawEllipse(QPointF(6.2, 6.6), 0.9, 0.9)


def _edit(p: QPainter, path: QPainterPath) -> None:
    """编辑 / 重命名铅笔。"""
    path.moveTo(11.8, 3.4)
    path.lineTo(14.6, 6.2)
    path.lineTo(6.8, 14.0)
    path.lineTo(3.4, 14.6)
    path.lineTo(4.0, 11.2)
    path.closeSubpath()
    p.drawPath(path)


def _restore(p: QPainter, path: QPainterPath) -> None:
    """窗口还原（双层叠框）。"""
    p.drawRoundedRect(QRectF(3.4, 6.0, 8.6, 8.0), 1.4, 1.4)
    path.moveTo(6.2, 6.0)
    path.lineTo(6.2, 4.0)
    path.lineTo(14.4, 4.0)
    path.lineTo(14.4, 11.6)
    path.lineTo(12.0, 11.6)
    p.drawPath(path)


def _trash(p: QPainter, path: QPainterPath) -> None:
    """清空 / 垃圾桶。"""
    path.moveTo(3.6, 5.4)
    path.lineTo(14.4, 5.4)
    path.moveTo(7.0, 5.4)
    path.lineTo(7.0, 3.6)
    path.lineTo(11.0, 3.6)
    path.lineTo(11.0, 5.4)
    p.drawPath(path)
    p.drawRoundedRect(QRectF(4.8, 5.4, 8.4, 9.4), 1.4, 1.4)


def _chevron_right(p: QPainter, path: QPainterPath) -> None:
    path.moveTo(7.0, 4.2)
    path.lineTo(11.4, 9.0)
    path.lineTo(7.0, 13.8)
    p.drawPath(path)


def _chevron_down(p: QPainter, path: QPainterPath) -> None:
    path.moveTo(4.2, 7.0)
    path.lineTo(9.0, 11.4)
    path.lineTo(13.8, 7.0)
    p.drawPath(path)


def _grid(p: QPainter, path: QPainterPath) -> None:
    """网格密度（四宫格）。"""
    for x, y in ((3.0, 3.0), (9.6, 3.0), (3.0, 9.6), (9.6, 9.6)):
        p.drawRoundedRect(QRectF(x, y, 5.4, 5.4), 1.4, 1.4)


def _layers(p: QPainter, path: QPainterPath) -> None:
    """图层堆叠（三层菱形），用于 PSD 工具。"""
    for cy in (5.4, 9.0, 12.6):
        p.drawPolyline([
            QPointF(9.0, cy - 3.0),
            QPointF(16.2, cy),
            QPointF(9.0, cy + 3.0),
            QPointF(1.8, cy),
            QPointF(9.0, cy - 3.0),
        ])


def _text(p: QPainter, path: QPainterPath) -> None:
    """文字（T 字形），用于 PSD 文字替换。"""
    p.drawLine(QPointF(3.2, 4.4), QPointF(14.8, 4.4))
    p.drawLine(QPointF(9.0, 4.4), QPointF(9.0, 13.6))
    p.drawLine(QPointF(6.0, 13.6), QPointF(12.0, 13.6))


_PAINTERS = {
    "home": _home,
    "library": _library,
    "archive": _archive,
    "task": _task,
    "calendar": _calendar,
    "chart": _chart,
    "cleaner": _cleaner,
    "sliders": _sliders,
    "moon": _moon,
    "sun": _sun,
    "search": _search,
    "folder": _folder,
    "file": _file,
    "bottle": _bottle,
    "image": _image,
    "copy": _copy,
    "external": _external,
    "close": _close,
    "plus": _plus,
    "upload": _upload,
    "bolt": _bolt,
    "sparkles": _sparkles,
    "check_circle": _check_circle,
    "tag": _tag,
    "edit": _edit,
    "restore": _restore,
    "trash": _trash,
    "grid": _grid,
    "layers": _layers,
    "text": _text,
    "chevron_right": _chevron_right,
    "chevron_down": _chevron_down,
}


def icon_pixmap(name: str, color: QColor, size: int = 18, weight: float = STROKE) -> QPixmap:
    """画一个图标位图。size 不是 18 时会等比缩放。"""
    scale = size / CANVAS
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(color)
    pen.setWidthF(weight)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.scale(scale, scale)

    path = QPainterPath()
    _PAINTERS.get(name, _file)(painter, path)
    painter.end()
    return pixmap


def icon(name: str, color, size: int = 18, weight: float = STROKE) -> QIcon:
    """生成 QIcon，颜色可以是 #hex 或 rgba(...) 字符串，也可以是 QColor。"""
    qcolor = color if isinstance(color, QColor) else to_qcolor(str(color))
    return QIcon(icon_pixmap(name, qcolor, size, weight))
