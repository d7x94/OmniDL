"""Settings tab — thin orchestrator that stacks sub-panels (PySide6)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ui.tabs.settings import (
    GeneralPanel,
    NetworkPanel,
    RemoteApiPanel,
    TaildropPanel,
    ToolsPanel,
)

# Re-export frozen-build updaters for backward compatibility
from ui.tabs.settings.tools_panel import (  # noqa: F401
    _install_gallery_dl_frozen,
    _install_ytdlp_frozen,
)

if TYPE_CHECKING:
    from ui.main_window import MainWindow


class SettingsTab(QWidget):

    def __init__(self, app: "MainWindow") -> None:
        super().__init__()
        self._app = app
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        hdr = QWidget()
        hdr.setStyleSheet("background: transparent;")
        hdr_layout = QVBoxLayout(hdr)
        hdr_layout.setContentsMargins(28, 24, 28, 14)
        title = QLabel("Cài đặt")
        title.setObjectName("page_title")
        hdr_layout.addWidget(title)
        layout.addWidget(hdr)

        # Scrollable content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        self._general_panel  = GeneralPanel(content,  self._app)
        self._network_panel  = NetworkPanel(content,  self._app)
        self._tools_panel    = ToolsPanel(content,    self._app)
        self._taildrop_panel = TaildropPanel(content, self._app)
        self._api_panel      = RemoteApiPanel(content, self._app)

        for panel in (
            self._general_panel,
            self._network_panel,
            self._tools_panel,
            self._taildrop_panel,
            self._api_panel,
        ):
            content_layout.addWidget(panel)

        content_layout.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        from PySide6.QtCore import QPropertyAnimation
        from PySide6.QtWidgets import QGraphicsOpacityEffect

        effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", self)
        anim.setDuration(150)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.start()
