"""Settings tab — thin orchestrator that stacks sub-panels (PySide6)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLineEdit,
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

        from PySide6.QtCore import QPropertyAnimation
        from PySide6.QtWidgets import QGraphicsOpacityEffect

        self._fade_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._fade_effect)
        self._fade_anim = QPropertyAnimation(self._fade_effect, b"opacity", self)
        self._fade_anim.setDuration(150)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Search box
        search_row = QWidget()
        search_row.setStyleSheet("background: transparent;")
        srl = QHBoxLayout(search_row)
        srl.setContentsMargins(28, 16, 28, 4)
        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("Tìm kiếm cài đặt...")
        self._search_box.setObjectName("settings_search")
        self._search_box.setClearButtonEnabled(True)
        self._search_box.textChanged.connect(self._on_search)
        srl.addWidget(self._search_box)
        layout.addWidget(search_row)

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

        self._general_panel = GeneralPanel(content, self._app)
        self._network_panel = NetworkPanel(content, self._app)
        self._tools_panel = ToolsPanel(content, self._app)
        self._taildrop_panel = TaildropPanel(content, self._app)
        self._api_panel = RemoteApiPanel(content, self._app)

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

    def _on_search(self, text: str) -> None:
        q = text.strip().lower()
        panels = (
            self._general_panel,
            self._network_panel,
            self._tools_panel,
            self._taildrop_panel,
            self._api_panel,
        )
        for panel in panels:
            for sec_text, cfg_key, wrapper, content in panel._sections:
                if q:
                    match = q in sec_text
                    wrapper.setVisible(match)
                    if match:
                        content.setVisible(True)
                else:
                    wrapper.setVisible(True)
                    collapsed = bool(panel._app.config.get(cfg_key, False))
                    content.setVisible(not collapsed)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._fade_anim.stop()
        self._fade_anim.start()
