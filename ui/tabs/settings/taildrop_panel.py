"""Settings panel: TAILDROP — send completed files to iPhone via Tailscale (PySide6)."""

from __future__ import annotations

import re
import threading
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ui.signals import ui_bridge
from ui.tabs.settings._base_panel import _BasePanel
from ui.themes.tokens import T
from utils.i18n import t

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = __import__("logging").getLogger(__name__)

_NODE_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9\-\.]{0,252}[A-Za-z0-9])?$")


class TaildropPanel(_BasePanel):
    def __init__(self, master, app: "MainWindow") -> None:
        super().__init__(master, app)
        self._td_node_vars: dict[str, QCheckBox] = {}
        self._saved_nodes: list = []
        self._build()

    def _build(self) -> None:
        cfg = self._app.config

        sec_td = self._collapsible_section("TAILDROP", "tail_config", icon="📲")
        td_card = self._card(container=sec_td)

        self._row_label(
            td_card,
            t("settings.taildrop.description"),
            wrap=True,
        )

        # ── Enable toggle ────────────────────────────────────────────────
        self._td_switch = self._switch_row(
            td_card,
            t("settings.taildrop.enable"),
            bool(getattr(cfg, "taildrop_enabled", False)),
            self._on_taildrop_toggle,
        )

        self._td_avail_lbl = QLabel("")
        self._td_avail_lbl.setStyleSheet(
            f"color: {T.text3}; font-size: 11px; background: transparent; padding: 2px 16px 6px;"
        )
        td_card.layout().addWidget(self._td_avail_lbl)
        self._refresh_taildrop_avail_label()

        self._separator(td_card)

        # ── Send mode ────────────────────────────────────────────────────
        mode_hdr = QLabel(t("settings.taildrop.mode_header"))
        mode_hdr.setStyleSheet(
            f"color: {T.text2}; font-size: 11px; font-weight: bold; background: transparent; padding: 0 16px 4px;"
        )
        td_card.layout().addWidget(mode_hdr)

        mode_row = QWidget()
        mode_row.setStyleSheet("background: transparent;")
        mhl = QHBoxLayout(mode_row)
        mhl.setContentsMargins(16, 0, 16, 4)
        self._td_mode_auto_btn = QPushButton(t("settings.taildrop.mode_auto_btn"))
        self._td_mode_auto_btn.setFixedHeight(30)
        self._td_mode_auto_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._td_mode_auto_btn.clicked.connect(lambda: self._on_taildrop_mode_change("always"))
        mhl.addWidget(self._td_mode_auto_btn)
        self._td_mode_manual_btn = QPushButton(t("settings.taildrop.mode_manual_btn"))
        self._td_mode_manual_btn.setFixedHeight(30)
        self._td_mode_manual_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._td_mode_manual_btn.clicked.connect(lambda: self._on_taildrop_mode_change("ask"))
        mhl.addWidget(self._td_mode_manual_btn)
        mhl.addStretch()
        td_card.layout().addWidget(mode_row)

        self._td_mode_desc_lbl = QLabel("")
        self._td_mode_desc_lbl.setStyleSheet(
            f"color: {T.text3}; font-size: 10px; background: transparent; padding: 0 16px 10px;"
        )
        td_card.layout().addWidget(self._td_mode_desc_lbl)
        self._refresh_taildrop_mode_buttons()

        self._separator(td_card)

        # ── Target nodes ──────────────────────────────────────────────────
        nodes_hdr = QLabel(t("settings.taildrop.nodes_header"))
        nodes_hdr.setStyleSheet(
            f"color: {T.text2}; font-size: 11px; font-weight: bold; background: transparent; padding: 0 16px 4px;"
        )
        td_card.layout().addWidget(nodes_hdr)

        nodes_hint = QLabel(t("settings.taildrop.nodes_hint"))
        nodes_hint.setStyleSheet(
            f"color: {T.text3}; font-size: 10px; background: transparent; padding: 0 16px 6px;"
        )
        td_card.layout().addWidget(nodes_hint)

        # Scrollable checkbox list
        scroll_wrapper = QWidget()
        scroll_wrapper.setStyleSheet("background: transparent;")
        swl = QHBoxLayout(scroll_wrapper)
        swl.setContentsMargins(16, 0, 16, 4)

        self._nodes_scroll = QScrollArea()
        self._nodes_scroll.setWidgetResizable(True)
        self._nodes_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._nodes_scroll.setFixedHeight(100)
        self._nodes_scroll.setStyleSheet(f"background: {T.surface3}; border-radius: 6px;")

        self._td_nodes_widget = QWidget()
        self._td_nodes_widget.setStyleSheet("background: transparent;")
        self._td_nodes_layout = QVBoxLayout(self._td_nodes_widget)
        self._td_nodes_layout.setContentsMargins(4, 4, 4, 4)
        self._td_nodes_layout.setSpacing(2)
        self._td_nodes_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._nodes_scroll.setWidget(self._td_nodes_widget)
        swl.addWidget(self._nodes_scroll)
        td_card.layout().addWidget(scroll_wrapper)

        # Restore saved nodes
        self._saved_nodes = list(getattr(cfg, "taildrop_target_nodes", []) or [])
        if self._saved_nodes:
            self._render_node_checkboxes(self._saved_nodes, self._saved_nodes)
        else:
            self._render_node_checkboxes([], [])

        # Manual entry
        manual_row = QWidget()
        manual_row.setStyleSheet("background: transparent;")
        mnl = QHBoxLayout(manual_row)
        mnl.setContentsMargins(16, 0, 16, 4)
        lbl = QLabel(t("settings.taildrop.manual_label"))
        lbl.setStyleSheet(f"color: {T.text3}; font-size: 10px; background: transparent;")
        mnl.addWidget(lbl)
        self._td_node_entry = QLineEdit()
        self._td_node_entry.setPlaceholderText(t("settings.taildrop.manual_placeholder"))
        self._td_node_entry.setFixedSize(200, 30)
        mnl.addWidget(self._td_node_entry)
        add_btn = QPushButton(t("settings.taildrop.add_btn"))
        add_btn.setFixedSize(80, 30)
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.setStyleSheet(
            f"background: {T.surface3}; color: {T.text2}; border-radius: 6px; border: none; font-size: 11px; padding: 0 4px;"
        )
        add_btn.clicked.connect(self._on_taildrop_add_manual)
        mnl.addWidget(add_btn)
        mnl.addStretch()
        td_card.layout().addWidget(manual_row)

        # ── Scan peers ─────────────────────────────────────────────────────
        action_row = QWidget()
        action_row.setStyleSheet("background: transparent;")
        ahl = QHBoxLayout(action_row)
        ahl.setContentsMargins(16, 0, 16, 14)
        self._td_scan_btn = QPushButton(t("settings.taildrop.scan_btn"))
        self._td_scan_btn.setFixedHeight(32)
        self._td_scan_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._td_scan_btn.setStyleSheet(
            f"background: {T.surface3}; color: {T.text2}; border-radius: 8px; border: none; font-size: 12px;"
        )
        self._td_scan_btn.clicked.connect(self._on_taildrop_scan)
        ahl.addWidget(self._td_scan_btn)
        self._td_status_lbl = QLabel("")
        self._td_status_lbl.setStyleSheet(f"color: {T.text2}; font-size: 11px; background: transparent;")
        ahl.addWidget(self._td_status_lbl)
        ahl.addStretch()
        td_card.layout().addWidget(action_row)

        self._layout.addSpacing(20)

    # ── Handlers ──────────────────────────────────────────────────────────

    def _refresh_taildrop_avail_label(self) -> None:
        lbl = getattr(self, "_td_avail_lbl", None)
        if lbl is None:
            return
        try:
            import shutil

            if shutil.which("tailscale"):
                lbl.setText(t("settings.taildrop.cli_found"))
                lbl.setStyleSheet(
                    f"color: {T.success}; font-size: 11px; background: transparent; padding: 2px 16px 6px;"
                )
            else:
                lbl.setText(t("settings.taildrop.cli_not_found"))
                lbl.setStyleSheet(
                    f"color: {T.warning_text}; font-size: 11px; background: transparent; padding: 2px 16px 6px;"
                )
        except Exception:
            pass

    def _on_taildrop_toggle(self, enabled: bool) -> None:
        self._app.config.set("taildrop_enabled", enabled)
        self._app.config.save()
        msg = t("settings.taildrop.toggle_on") if enabled else t("settings.taildrop.toggle_off")
        self._app.toast(msg, "success" if enabled else "info")

    def _on_taildrop_mode_change(self, mode: str) -> None:
        self._app.config.set("taildrop_send_mode", mode)
        self._app.config.save()
        self._refresh_taildrop_mode_buttons()
        label = (
            t("settings.taildrop.mode_auto_label")
            if mode == "always"
            else t("settings.taildrop.mode_manual_label")
        )
        self._app.toast(t("settings.taildrop.mode_toast", label=label), "success")

    def _refresh_taildrop_mode_buttons(self) -> None:
        auto_btn = getattr(self, "_td_mode_auto_btn", None)
        man_btn = getattr(self, "_td_mode_manual_btn", None)
        desc_lbl = getattr(self, "_td_mode_desc_lbl", None)
        if auto_btn is None:
            return
        mode = str(getattr(self._app.config, "taildrop_send_mode", "ask") or "ask")
        if mode == "always":
            auto_btn.setStyleSheet(
                f"background: {T.primary_dim}; color: {T.primary_text}; border-radius: 8px; border: none; font-size: 11px; padding: 4px 12px;"
            )
            man_btn.setStyleSheet(
                f"background: {T.surface3}; color: {T.text2}; border-radius: 8px; border: none; font-size: 11px; padding: 4px 12px;"
            )
            if desc_lbl:
                desc_lbl.setText(t("settings.taildrop.mode_auto_desc"))
        else:
            auto_btn.setStyleSheet(
                f"background: {T.surface3}; color: {T.text2}; border-radius: 8px; border: none; font-size: 11px; padding: 4px 12px;"
            )
            man_btn.setStyleSheet(
                f"background: {T.primary_dim}; color: {T.primary_text}; border-radius: 8px; border: none; font-size: 11px; padding: 4px 12px;"
            )
            if desc_lbl:
                desc_lbl.setText(t("settings.taildrop.mode_manual_desc"))

    def _on_taildrop_add_manual(self) -> None:
        node = self._td_node_entry.text().strip()
        if not node:
            return
        if not _NODE_RE.match(node):
            self._app.toast(t("settings.taildrop.invalid_node"), "error")
            return
        current = list(self._td_node_vars.keys())
        if node not in current:
            current.append(node)
            selected = [n for n, cb in self._td_node_vars.items() if cb.isChecked()]
            self._render_node_checkboxes(current, selected + [node])
        if node in self._td_node_vars:
            self._td_node_vars[node].setChecked(True)
        self._td_node_entry.clear()
        self._save_selected_nodes()
        self._app.toast(t("settings.taildrop.node_added", node=node), "success")

    def _render_node_checkboxes(self, all_nodes: list, selected_nodes: list) -> None:
        while self._td_nodes_layout.count():
            item = self._td_nodes_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._td_node_vars.clear()

        if not all_nodes:
            lbl = QLabel(t("settings.taildrop.no_nodes"))
            lbl.setStyleSheet(f"color: {T.text3}; font-size: 11px; background: transparent; padding: 4px;")
            self._td_nodes_layout.addWidget(lbl)
            return

        for node in all_nodes:
            cb = QCheckBox(node)
            cb.setChecked(node in selected_nodes)
            cb.setStyleSheet(f"color: {T.text}; font-size: 12px; background: transparent; padding: 2px 4px;")
            cb.clicked.connect(self._save_selected_nodes)
            self._td_node_vars[node] = cb
            self._td_nodes_layout.addWidget(cb)

    def _save_selected_nodes(self) -> None:
        selected = [n for n, cb in self._td_node_vars.items() if cb.isChecked()]
        self._app.config.set_taildrop_target_nodes(selected)
        self._app.config.save()
        self._saved_nodes = selected

    def _on_taildrop_scan(self) -> None:
        self._td_status_lbl.setText(t("settings.taildrop.scanning"))
        self._td_status_lbl.setStyleSheet(f"color: {T.text2}; font-size: 11px; background: transparent;")
        self._td_scan_btn.setEnabled(False)

        def _worker():
            try:
                from app.event_bus import bus as _global_bus
                from app.services.taildrop_service import TaildropService

                svc = TaildropService(config=self._app.config, event_bus=_global_bus)
                nodes = svc.list_nodes()
                svc.close()
            except Exception as exc:
                nodes = []
                logger.warning("Taildrop scan error: %s", exc)

            def _update():
                self._td_scan_btn.setEnabled(True)
                if not nodes:
                    self._td_status_lbl.setText(t("settings.taildrop.no_devices_found"))
                    self._td_status_lbl.setStyleSheet(
                        f"color: {T.warning_text}; font-size: 11px; background: transparent;"
                    )
                    return
                existing_manual = [n for n in self._td_node_vars if n not in nodes]
                all_nodes = nodes + existing_manual
                currently_selected = [n for n, cb in self._td_node_vars.items() if cb.isChecked()] or list(
                    self._saved_nodes
                )
                self._render_node_checkboxes(all_nodes, currently_selected)
                self._td_status_lbl.setText(t("settings.taildrop.devices_found", count=len(nodes)))
                self._td_status_lbl.setStyleSheet(
                    f"color: {T.success}; font-size: 11px; background: transparent;"
                )

            ui_bridge.post(_update)

        threading.Thread(target=_worker, daemon=True, name="omnidl-td-scan").start()
