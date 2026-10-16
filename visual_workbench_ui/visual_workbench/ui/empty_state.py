"""Reusable, action-oriented empty state for library views."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from . import styles as S
from .icons import icon as painted_icon


class EmptyStatePanel(QFrame):
    def __init__(self, icon_name: str, parent=None):
        super().__init__(parent)
        self.setObjectName("EmptyState")
        self.setMinimumHeight(260)
        self._icon_name = icon_name

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.addStretch(1)

        visual_row = QHBoxLayout()
        visual_row.addStretch()
        visual = QFrame()
        visual.setObjectName("EmptyStateVisual")
        visual.setFixedSize(64, 64)
        visual_layout = QHBoxLayout(visual)
        visual_layout.setContentsMargins(0, 0, 0, 0)
        self.icon_label = QLabel()
        self.icon_label.setObjectName("EmptyStateIcon")
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        visual_layout.addWidget(self.icon_label)
        visual_row.addWidget(visual)
        visual_row.addStretch()
        outer.addLayout(visual_row)
        outer.addSpacing(16)

        self.title_label = QLabel()
        self.title_label.setObjectName("EmptyStateTitle")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(self.title_label)
        outer.addSpacing(6)

        self.detail_label = QLabel()
        self.detail_label.setObjectName("EmptyStateDetail")
        self.detail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_label.setWordWrap(True)
        self.detail_label.setMaximumWidth(430)
        detail_row = QHBoxLayout()
        detail_row.addStretch()
        detail_row.addWidget(self.detail_label)
        detail_row.addStretch()
        outer.addLayout(detail_row)
        outer.addSpacing(18)

        action_row = QHBoxLayout()
        action_row.addStretch()
        self.action_button = QPushButton()
        self.action_button.setObjectName("PrimaryButton")
        self.action_button.setMinimumWidth(132)
        self.action_button.setCursor(Qt.CursorShape.PointingHandCursor)
        action_row.addWidget(self.action_button)
        action_row.addStretch()
        outer.addLayout(action_row)
        outer.addStretch(1)
        self.refresh_theme()

    def set_content(self, title: str, detail: str, action: str) -> None:
        self.title_label.setText(title)
        self.detail_label.setText(detail)
        self.action_button.setText(action)

    def refresh_theme(self) -> None:
        self.icon_label.setPixmap(painted_icon(self._icon_name, S._ACCENT, 28).pixmap(28, 28))
