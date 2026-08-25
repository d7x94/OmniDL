"""Special Downloads tab — Facebook Story (CDP) and similar."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.components.post_download_actions import PostDownloadActions
from ui.signals import ui_bridge
from ui.themes.tokens import T
from utils.i18n import t

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = logging.getLogger(__name__)

# "label" is a proper noun and stays identical across languages, so platform
# matching by combo text (see _on_platform_change) is unaffected by retranslate().
_PLATFORMS = {
    "facebook_story": {
        "label": "Facebook Story",
        "needs_browser": True,
        "browsers": ["brave", "chrome"],
    },
}


class SpecialDlTab(QWidget):
    def __init__(self, app: "MainWindow") -> None:
        super().__init__()
        self._app = app
        self._config = app.config
        self._running = False
        self._last_dest: Path | None = None
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

        # Header
        hdr = QWidget()
        hdr.setStyleSheet("background: transparent;")
        hdr_layout = QVBoxLayout(hdr)
        hdr_layout.setContentsMargins(28, 24, 28, 4)
        hdr_layout.setSpacing(2)

        self._title_lbl = QLabel(t("special.title"))
        self._title_lbl.setObjectName("page_title")
        hdr_layout.addWidget(self._title_lbl)

        self._subtitle_lbl = QLabel(t("special.subtitle"))
        self._subtitle_lbl.setStyleSheet(f"color: {T.text2}; font-size: 12px;")
        hdr_layout.addWidget(self._subtitle_lbl)

        layout.addWidget(hdr)

        # Settings card
        card_wrap = QWidget()
        card_wrap.setStyleSheet("background: transparent;")
        cw_layout = QVBoxLayout(card_wrap)
        cw_layout.setContentsMargins(28, 16, 28, 0)

        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background-color: {T.surface2};
                border: 1px solid {T.border};
                border-radius: 12px;
            }}
            QLabel {{ border: none; }}
        """)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 20, 20, 20)
        card_layout.setSpacing(10)

        # Row 1: platform selector
        row1 = QWidget()
        row1.setStyleSheet("background: transparent;")
        r1_layout = QHBoxLayout(row1)
        r1_layout.setContentsMargins(0, 0, 0, 0)
        r1_layout.setSpacing(12)

        self._plat_lbl = QLabel(t("special.platform_label"))
        self._plat_lbl.setFixedWidth(90)
        self._plat_lbl.setStyleSheet(f"color: {T.text}; font-size: 13px; font-weight: bold;")
        r1_layout.addWidget(self._plat_lbl)

        self._platform_combo = QComboBox()
        self._platform_combo.setFixedSize(200, 34)
        for v in _PLATFORMS.values():
            self._platform_combo.addItem(v["label"])
        self._platform_combo.currentTextChanged.connect(self._on_platform_change)
        r1_layout.addWidget(self._platform_combo)
        r1_layout.addStretch()

        card_layout.addWidget(row1)

        # Row 2: URL entry + download button
        row2 = QWidget()
        row2.setStyleSheet("background: transparent;")
        r2_layout = QHBoxLayout(row2)
        r2_layout.setContentsMargins(0, 0, 0, 0)
        r2_layout.setSpacing(12)

        self._url_entry = QLineEdit()
        self._url_entry.setPlaceholderText(t("special.url_placeholder.facebook_story"))
        self._url_entry.setFixedHeight(40)
        self._url_entry.setStyleSheet(f"""
            QLineEdit {{
                background-color: {T.surface3};
                border: 1px solid {T.border};
                border-radius: 8px;
                color: {T.text};
                font-size: 13px;
                padding: 0 10px;
            }}
        """)
        self._url_entry.returnPressed.connect(self._on_download)
        r2_layout.addWidget(self._url_entry, 1)

        self._dl_btn = QPushButton(t("special.download_btn"))
        self._dl_btn.setFixedSize(120, 40)
        self._dl_btn.setStyleSheet(
            f"background: {T.primary}; color: white; border: none; border-radius: 8px; font-size: 13px; font-weight: bold; padding: 0 8px;"
        )
        self._dl_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._dl_btn.clicked.connect(self._on_download)
        r2_layout.addWidget(self._dl_btn)

        card_layout.addWidget(row2)

        # Row 3: browser selector
        self._browser_row = QWidget()
        self._browser_row.setStyleSheet("background: transparent;")
        r3_layout = QHBoxLayout(self._browser_row)
        r3_layout.setContentsMargins(0, 0, 0, 0)
        r3_layout.setSpacing(12)

        self._br_lbl = QLabel(t("special.browser_label"))
        self._br_lbl.setFixedWidth(90)
        self._br_lbl.setStyleSheet(f"color: {T.text2}; font-size: 12px;")
        r3_layout.addWidget(self._br_lbl)

        self._browser_combo = QComboBox()
        self._browser_combo.addItems(["brave", "chrome"])
        self._browser_combo.setFixedSize(120, 30)
        r3_layout.addWidget(self._browser_combo)
        r3_layout.addStretch()

        card_layout.addWidget(self._browser_row)

        # Separator
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background-color: {T.border};")
        card_layout.addWidget(sep)

        # Guide text
        self._guide_lbl = QLabel(t("special.guide.facebook_story"))
        self._guide_lbl.setStyleSheet(f"color: {T.text2}; font-size: 12px;")
        self._guide_lbl.setWordWrap(True)
        card_layout.addWidget(self._guide_lbl)

        cw_layout.addWidget(card)
        layout.addWidget(card_wrap)

        # Progress card
        prog_wrap = QWidget()
        prog_wrap.setStyleSheet("background: transparent;")
        pw_layout = QVBoxLayout(prog_wrap)
        pw_layout.setContentsMargins(28, 16, 28, 0)

        prog_card = QFrame()
        prog_card.setStyleSheet(f"""
            QFrame {{
                background-color: {T.surface2};
                border: 1px solid {T.border};
                border-radius: 12px;
            }}
            QLabel {{ border: none; }}
        """)
        prog_layout = QVBoxLayout(prog_card)
        prog_layout.setContentsMargins(20, 16, 20, 14)
        prog_layout.setSpacing(6)

        # Status row
        status_row = QWidget()
        status_row.setStyleSheet("background: transparent;")
        sr_layout = QHBoxLayout(status_row)
        sr_layout.setContentsMargins(0, 0, 0, 0)
        sr_layout.setSpacing(6)

        self._status_dot = QLabel("●")
        self._status_dot.setFixedWidth(16)
        self._status_dot.setStyleSheet(f"color: {T.text3}; font-size: 14px;")
        sr_layout.addWidget(self._status_dot)

        self._status_lbl = QLabel(t("special.status.waiting"))
        self._status_lbl.setStyleSheet(f"color: {T.text2}; font-size: 13px;")
        sr_layout.addWidget(self._status_lbl, 1)

        self._speed_lbl = QLabel("")
        self._speed_lbl.setStyleSheet(f"color: {T.text3}; font-size: 12px;")
        self._speed_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        sr_layout.addWidget(self._speed_lbl)

        prog_layout.addWidget(status_row)

        # Retry row (hidden until error)
        self._retry_row = QWidget()
        self._retry_row.setStyleSheet("background: transparent;")
        rr_layout = QHBoxLayout(self._retry_row)
        rr_layout.setContentsMargins(0, 0, 0, 0)
        rr_layout.setSpacing(8)

        self._retry_btn = QPushButton(t("special.retry"))
        self._retry_btn.setFixedHeight(28)
        self._retry_btn.setStyleSheet(
            f"background: {T.primary_dim}; color: {T.primary_text}; border: none; border-radius: 6px; font-size: 12px; padding: 0 12px;"
        )
        self._retry_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._retry_btn.clicked.connect(self._on_download)
        rr_layout.addWidget(self._retry_btn)

        self._clear_hist_btn = QPushButton(t("special.clear_history"))
        self._clear_hist_btn.setFixedHeight(28)
        self._clear_hist_btn.setStyleSheet(
            f"background: {T.surface3}; color: {T.text3}; border: none; border-radius: 6px; font-size: 12px; padding: 0 12px;"
        )
        self._clear_hist_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_hist_btn.clicked.connect(self._clear_status)
        rr_layout.addWidget(self._clear_hist_btn)
        rr_layout.addStretch()

        self._retry_row.hide()
        prog_layout.addWidget(self._retry_row)

        # Progress bar
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(6)
        self._progress.setStyleSheet(f"""
            QProgressBar {{
                background-color: {T.surface3};
                border-radius: 3px;
                border: none;
            }}
            QProgressBar::chunk {{
                background-color: {T.primary};
                border-radius: 3px;
            }}
        """)
        prog_layout.addWidget(self._progress)

        # Button row (hidden until done)
        self._btn_row = QWidget()
        self._btn_row.setStyleSheet("background: transparent;")
        br_layout = QHBoxLayout(self._btn_row)
        br_layout.setContentsMargins(0, 0, 0, 0)
        br_layout.setSpacing(8)

        self._open_btn = QPushButton(t("special.open_folder"))
        self._open_btn.setFixedHeight(32)
        self._open_btn.setStyleSheet(
            f"background: {T.surface3}; color: {T.text2}; border: none; border-radius: 6px; font-size: 12px; padding: 0 12px;"
        )
        self._open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._open_btn.clicked.connect(self._open_output_folder)
        br_layout.addWidget(self._open_btn)

        self._preview_btn = QPushButton(t("special.view"))
        self._preview_btn.setFixedHeight(32)
        self._preview_btn.setStyleSheet(
            f"background: {T.primary_dim}; color: {T.primary_text}; border: none; border-radius: 6px; font-size: 12px; padding: 0 12px;"
        )
        self._preview_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._preview_btn.clicked.connect(self._open_preview)
        br_layout.addWidget(self._preview_btn)

        self._post_actions = PostDownloadActions(
            self._btn_row,
            on_convert=self._on_post_convert,
            on_send=self._on_post_send,
            on_delete=self._on_post_delete,
            on_edit=lambda p: self._app.navigate_to("editor", file_path=str(p)),
            compact=False,
        )
        br_layout.addWidget(self._post_actions)
        br_layout.addStretch()

        self._btn_row.hide()
        prog_layout.addWidget(self._btn_row)

        pw_layout.addWidget(prog_card)
        layout.addWidget(prog_wrap)
        layout.addStretch()

    # ── Platform change ───────────────────────────────────────────────────────

    def _on_platform_change(self, friendly_name: str) -> None:
        key = next(
            (k for k, v in _PLATFORMS.items() if v["label"] == friendly_name),
            "facebook_story",
        )
        info = _PLATFORMS.get(key, _PLATFORMS["facebook_story"])
        self._guide_lbl.setText(t(f"special.guide.{key}"))
        self._url_entry.setPlaceholderText(t(f"special.url_placeholder.{key}"))
        if info.get("needs_browser"):
            self._browser_row.show()
        else:
            self._browser_row.hide()

    # ── Download ──────────────────────────────────────────────────────────────

    def _on_download(self) -> None:
        if self._running:
            return

        url = self._url_entry.text().strip()
        if not url:
            self._set_status("error", t("special.error.paste_url"))
            return

        friendly = self._platform_combo.currentText()
        platform_key = next(
            (k for k, v in _PLATFORMS.items() if v["label"] == friendly),
            "facebook_story",
        )
        browser = self._browser_combo.currentText()

        self._running = True
        self._dl_btn.setEnabled(False)
        self._dl_btn.setText(t("special.downloading"))
        self._set_status("info", t("special.status.starting"), 0)

        threading.Thread(
            target=self._worker,
            args=(platform_key, url, browser),
            daemon=True,
        ).start()

    def _worker(self, platform_key: str, url: str, browser: str) -> None:
        self._last_dest = None
        try:
            if platform_key == "facebook_story":
                results = [self._run_facebook_story(url, browser)]
            else:
                results = []

            results = [r for r in results if r is not None]

            if results:
                self._last_dest = results[0]
                name = results[0].name
                label = t("special.downloaded", name=name)
                dest = results[0]
                ui_bridge.post(lambda lb=label: self._set_status("success", lb, 100))
                ui_bridge.post(lambda: self._btn_row.show())
                ui_bridge.post(lambda d=dest: self._post_actions.show(d))
            else:
                ui_bridge.post(lambda: self._set_status("error", t("special.no_result")))

        except RuntimeError as exc:
            msg = str(exc)
            ui_bridge.post(lambda m=msg: self._set_status("error", m))
        except Exception as exc:
            logger.exception("SpecialDlTab worker error")
            ui_bridge.post(lambda m=str(exc): self._set_status("error", t("special.error_prefix", msg=m)))
        finally:
            self._running = False
            ui_bridge.post(
                lambda: (
                    self._dl_btn.setEnabled(True),
                    self._dl_btn.setText(t("special.download_btn")),
                )
            )

    def _run_facebook_story(self, url: str, browser: str) -> "Path | None":
        from infrastructure.downloader.facebook_story_engine import download_story

        def _on_progress(pct: int, speed: str, status: str) -> None:
            ui_bridge.post(lambda p=pct, sp=speed, st=status: self._set_status("info", st, p, sp))

        return download_story(
            url=url,
            config=self._config,
            browser=browser,
            on_progress=_on_progress,
            timeout=45.0,
        )

    # ── UI helpers ────────────────────────────────────────────────────────────

    def _set_status(
        self,
        kind: str,
        message: str,
        pct: int = -1,
        speed: str = "",
    ) -> None:
        color_map = {
            "info": T.primary_text,
            "success": T.success_text,
            "error": T.error_text,
            "warning": T.warning_text,
        }
        dot_map = {
            "info": T.primary,
            "success": T.success,
            "error": T.error,
            "warning": T.warning_text,
        }
        self._status_dot.setStyleSheet(f"color: {dot_map.get(kind, T.text3)}; font-size: 14px;")
        self._status_lbl.setText(message)
        self._status_lbl.setStyleSheet(f"color: {color_map.get(kind, T.text2)}; font-size: 13px;")
        self._speed_lbl.setText(speed)
        if pct >= 0:
            self._progress.setValue(pct)
        if kind == "error":
            self._retry_row.show()
        elif kind in ("success", "info") and pct == 0:
            self._retry_row.hide()

    def _clear_status(self) -> None:
        self._retry_row.hide()
        self._btn_row.hide()
        self._post_actions.hide()
        self._progress.setValue(0)
        self._status_dot.setStyleSheet(f"color: {T.text3}; font-size: 14px;")
        self._status_lbl.setText(t("special.status.waiting"))
        self._status_lbl.setStyleSheet(f"color: {T.text2}; font-size: 13px;")
        self._speed_lbl.setText("")
        self._last_dest = None

    def _open_output_folder(self) -> None:
        from utils.helpers import open_folder, reveal_in_explorer

        dest = self._last_dest
        if dest and dest.exists():
            if not reveal_in_explorer(dest):
                open_folder(dest.parent)
        elif dest and dest.parent.exists():
            open_folder(dest.parent)

    def _open_preview(self) -> None:
        from utils.helpers import open_file, open_folder

        dest = self._last_dest
        if dest and dest.exists():
            open_file(dest)
        elif dest and dest.parent.exists():
            open_folder(dest.parent)

    # ── Post-download handlers ────────────────────────────────────────────────

    def _on_post_convert(self, file_path: Path, target_ext: str, encode_settings=None) -> None:
        if not hasattr(self._app, "service") or not hasattr(self._app.service, "convert_to_mp4"):
            self._post_actions.notify_convert_error(t("special.convert_unavailable"))
            return
        convert_svc = self._app.service.convert_to_mp4

        def _on_progress(pct: float) -> None:
            ui_bridge.post(lambda p=pct: self._progress.setValue(int(p)))

        def _on_done(output_path: Path) -> None:
            self._last_dest = output_path
            ui_bridge.post(lambda op=output_path: self._post_actions.notify_convert_done(op))
            ui_bridge.post(
                lambda: self._set_status("success", t("special.convert_done", name=output_path.name), 100)
            )

        def _on_error(msg: str) -> None:
            logger.error("Convert error: %s", msg)
            ui_bridge.post(lambda m=msg: self._post_actions.notify_convert_error(m))

        self._set_status("info", t("special.converting", ext=target_ext), 0)
        try:
            convert_svc(
                file_path,
                target_ext=target_ext,
                encode_settings=encode_settings,
                on_progress=_on_progress,
                on_done=_on_done,
                on_error=_on_error,
            )
        except Exception as exc:
            self._post_actions.notify_convert_error(str(exc))

    def _on_post_send(self, file_path: Path, restore_btn, specific_files=None) -> None:
        cfg = self._config
        nodes = cfg.taildrop_target_nodes
        if not isinstance(nodes, (list, tuple)) or not nodes:
            self._set_status("warning", t("queue.no_target"))
            restore_btn()
            return
        if not cfg.taildrop_enabled:
            self._set_status("warning", t("queue.taildrop_off"))
            restore_btn()
            return

        node_list_str = ", ".join(nodes)

        def _on_node_done(node: str) -> None:
            ui_bridge.post(lambda n=node: self._set_status("success", t("special.sent_to", node=n)))

        def _on_node_error(node: str, err: str) -> None:
            ui_bridge.post(
                lambda n=node, e=err: self._set_status("error", t("special.send_failed", node=n, err=e[:60]))
            )

        try:
            self._app.taildrop.send_file_to_nodes(
                file_path,
                nodes,
                on_node_done=_on_node_done,
                on_node_error=_on_node_error,
                specific_files_override=specific_files,
            )
            self._set_status("info", t("special.sending", nodes=node_list_str))
        except Exception as exc:
            self._set_status("error", t("special.send_error", err=str(exc)))
        finally:
            QTimer.singleShot(800, restore_btn)

    def _on_post_delete(self, file_path: Path) -> None:
        self._last_dest = None
        self._set_status("info", t("special.file_deleted"))
        self._post_actions.hide()
        self._btn_row.hide()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._fade_anim.stop()
        self._fade_anim.start()

    def retranslate(self) -> None:
        self._title_lbl.setText(t("special.title"))
        self._subtitle_lbl.setText(t("special.subtitle"))
        self._plat_lbl.setText(t("special.platform_label"))
        self._br_lbl.setText(t("special.browser_label"))
        self._retry_btn.setText(t("special.retry"))
        self._clear_hist_btn.setText(t("special.clear_history"))
        self._open_btn.setText(t("special.open_folder"))
        self._preview_btn.setText(t("special.view"))
        if not self._running:
            self._dl_btn.setText(t("special.download_btn"))

        key = next(
            (k for k, v in _PLATFORMS.items() if v["label"] == self._platform_combo.currentText()),
            "facebook_story",
        )
        self._guide_lbl.setText(t(f"special.guide.{key}"))
        self._url_entry.setPlaceholderText(t(f"special.url_placeholder.{key}"))
