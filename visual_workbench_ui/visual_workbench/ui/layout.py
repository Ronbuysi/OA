"""页面布局脚手架：给内容加一个居中的最大宽度列，并可选支持纵向自适应滚动。

宽屏下让内容铺满整行，卡片会被拉得很长、视线要来回扫，反而不好读。
收进一列并居中后信息更聚拢。窗口不够宽时（窄于 max_width）自动退化成普通满宽布局。
对于纵向堆叠较多卡片的页面（如工作台首页、设置页），开启 `scrollable=True`
可在 1080p + 125%/150% 缩放或小窗口下平滑纵向滚动，彻底避免卡片挤压裁切。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QScrollArea, QVBoxLayout, QWidget

CONTENT_MAX_WIDTH = 1120
CONTENT_MARGINS = (32, 28, 32, 24)


class PageColumn(QWidget):
    """页面内容的居中列。"""

    def __init__(self, parent=None, max_width: int = CONTENT_MAX_WIDTH,
                 margins: tuple[int, int, int, int] = CONTENT_MARGINS):
        super().__init__(parent)
        self.setObjectName("PageScrollViewport")
        self._max_width = max_width
        self._margins = margins
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(*margins)
        self.body.setSpacing(0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        left, top, right, bottom = self._margins
        extra = max(0, (self.width() - self._max_width) // 2)
        self.body.setContentsMargins(left + extra, top, right + extra, bottom)


def page_body(page: QWidget, spacing: int = 0,
              max_width: int = CONTENT_MAX_WIDTH,
              margins: tuple[int, int, int, int] = CONTENT_MARGINS,
              scrollable: bool = False) -> QVBoxLayout:
    """给页面套一个居中列，返回真正放内容的布局。

    `scrollable=True` 时外层自动包裹无边框透明 QScrollArea，防止高分屏/小窗口下纵向溢出挤压。
    """
    outer = QVBoxLayout(page)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)
    column = PageColumn(page, max_width, margins)
    if scrollable:
        scroll = QScrollArea(page)
        scroll.setObjectName("PageScrollArea")
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.viewport().setObjectName("PageScrollViewport")
        scroll.setWidget(column)
        outer.addWidget(scroll)
        page._scroll = scroll
    else:
        outer.addWidget(column)
    page._column = column  # 保住引用，别被 GC 掉
    body = column.body
    body.setSpacing(spacing)
    return body
