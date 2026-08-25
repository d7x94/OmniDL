"""Settings tab — thin orchestrator that stacks sub-panels (PySide6)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
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
from ui.themes.tokens import T
from utils.i18n import t

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

        # Panels bake T.* colours into inline stylesheets at build time, so a
        # theme switch only reaches them by rebuilding.  Deferred through the
        # event loop: the theme combo that triggered this lives inside the
        # widget tree being replaced.
        T.register(self._on_theme_changed)

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
        self._search_box.setPlaceholderText(t("settings.search"))
        self._search_box.setObjectName("settings_search")
        self._search_box.setClearButtonEnabled(True)
        self._search_box.textChanged.connect(self._on_search)
        srl.addWidget(self._search_box)
        layout.addWidget(search_row)

        # Scrollable content
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setWidget(self._build_content())
        layout.addWidget(self._scroll, 1)

        self._no_results = QLabel(t("settings.no_results"))
        self._no_results.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._no_results.setStyleSheet(f"color: {T.text3}; font-size: 12px; padding: 40px 28px;")
        self._no_results.hide()
        layout.addWidget(self._no_results)

    def _build_content(self) -> QWidget:
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
        return content

    def retranslate(self) -> None:
        """Rebuild every sub-panel in the new language.

        The panels bake their label text in at construction time (there are no
        stored widget refs to re-set), so rebuilding the whole content widget is
        both the smallest and the only complete fix. In-flight worker callbacks
        target widgets that are gone by then; ui_bridge swallows the resulting
        "already deleted" errors, so a running cookie extract cannot crash this.
        """
        self._search_box.setPlaceholderText(t("settings.search"))
        self._no_results.setText(t("settings.no_results"))
        self._no_results.setStyleSheet(f"color: {T.text3}; font-size: 12px; padding: 40px 28px;")
        old = self._scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        self._scroll.setWidget(self._build_content())
        self._on_search(self._search_box.text())

    def _on_theme_changed(self) -> None:
        # `self` as timer context: Qt drops the call if this tab is destroyed.
        QTimer.singleShot(0, self, self.retranslate)

    @staticmethod
    def _haystack(content: QWidget, sec_text: str) -> str:
        """Lowercased text of every label/button/field inside a section.

        Cached on the widget: matching only the section title made real
        settings ("proxy", "theme", ...) unfindable and blanked the page.
        """
        cached = content.property("_omnidl_search")
        if cached:
            return cached
        parts = [sec_text]
        for w in content.findChildren(QWidget):
            for getter in ("text", "placeholderText", "toolTip"):
                fn = getattr(w, getter, None)
                if callable(fn):
                    try:
                        val = fn()
                    except (TypeError, RuntimeError):
                        continue
                    if isinstance(val, str):
                        parts.append(val)
        hay = " ".join(parts).lower()
        content.setProperty("_omnidl_search", hay)
        return hay

    def _on_search(self, text: str) -> None:
        q = text.strip().lower()
        panels = (
            self._general_panel,
            self._network_panel,
            self._tools_panel,
            self._taildrop_panel,
            self._api_panel,
        )
        hits = 0
        for panel in panels:
            for sec_text, cfg_key, wrapper, content, set_expanded in panel._sections:
                if q:
                    match = q in self._haystack(content, sec_text)
                    wrapper.setVisible(match)
                    if match:
                        hits += 1
                        set_expanded(True)
                else:
                    wrapper.setVisible(True)
                    set_expanded(not bool(panel._app.config.get(cfg_key, False)))
        self._no_results.setVisible(bool(q) and hits == 0)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._fade_anim.stop()
        self._fade_anim.start()
