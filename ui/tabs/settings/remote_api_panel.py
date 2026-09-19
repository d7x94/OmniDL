"""Settings panel: REMOTE API — iOS / Mobile remote control (PySide6)."""

from __future__ import annotations

import random
import secrets as _secrets
import shutil
import threading
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QWidget,
)

from ui.signals import ui_bridge
from ui.tabs.settings._base_panel import _BasePanel
from ui.themes.tokens import T
from utils.i18n import t

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = __import__("logging").getLogger(__name__)


def _pick_bindable_port(lo: int = 50000, hi: int = 65000) -> int:
    import socket

    for _ in range(30):
        port = random.randint(lo, hi)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return random.randint(lo, hi)


class RemoteApiPanel(_BasePanel):
    def __init__(self, master, app: "MainWindow") -> None:
        super().__init__(master, app)
        self._build()

    def _build(self) -> None:
        cfg = self._app.config

        sec_api = self._collapsible_section(t("settings.api.section.remote"), "api_remote", icon="🔌")
        api_card = self._card(container=sec_api)

        self._row_label(
            api_card,
            t("settings.api.description"),
            wrap=True,
        )

        # ── Toggle row ──────────────────────────────────────────────────
        self._api_switch = self._switch_row(
            api_card,
            t("settings.api.enable"),
            bool(getattr(cfg, "api_enabled", False)),
            self._on_api_toggle,
        )

        self._api_status_lbl = QLabel("")
        self._api_status_lbl.setStyleSheet(
            f"color: {T.text2}; font-size: 11px; background: transparent; padding: 2px 16px 8px;"
        )
        api_card.layout().addWidget(self._api_status_lbl)
        self._refresh_api_status_label()

        self._separator(api_card)

        # ── Token row ───────────────────────────────────────────────────
        tok_hdr = QLabel(t("settings.api.token_header"))
        tok_hdr.setStyleSheet(
            f"color: {T.text2}; font-size: 11px; font-weight: bold; background: transparent; padding: 0 16px 4px;"
        )
        api_card.layout().addWidget(tok_hdr)

        token_row = QWidget()
        token_row.setStyleSheet("background: transparent;")
        thl = QHBoxLayout(token_row)
        thl.setContentsMargins(16, 0, 16, 4)
        self._api_token_lbl = QLabel(self._masked_token())
        self._api_token_lbl.setStyleSheet(
            f"color: {T.primary_text}; font-size: 11px; font-family: monospace;"
        )
        thl.addWidget(self._api_token_lbl, 1)
        self._api_copy_btn = QPushButton(t("settings.api.copy_btn"))
        self._api_copy_btn.setFixedSize(80, 28)
        self._api_copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._api_copy_btn.setStyleSheet(
            f"background: {T.surface3}; color: {T.text2}; border-radius: 6px; border: none; font-size: 11px; padding: 0 6px;"
        )
        self._api_copy_btn.clicked.connect(self._on_api_copy_token)
        thl.addWidget(self._api_copy_btn)
        api_card.layout().addWidget(token_row)

        token_action_row = QWidget()
        token_action_row.setStyleSheet("background: transparent;")
        tahl = QHBoxLayout(token_action_row)
        tahl.setContentsMargins(16, 0, 16, 14)
        self._api_rotate_btn = QPushButton(t("settings.api.rotate_btn"))
        self._api_rotate_btn.setFixedHeight(32)
        self._api_rotate_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._api_rotate_btn.setStyleSheet(
            f"background: {T.warning_bg}; color: {T.warning_text}; border-radius: 8px; border: none; font-size: 12px;"
        )
        self._api_rotate_btn.clicked.connect(self._on_api_rotate_token)
        tahl.addWidget(self._api_rotate_btn)
        self._api_token_status = QLabel("")
        self._api_token_status.setStyleSheet(f"color: {T.text2}; font-size: 11px; background: transparent;")
        tahl.addWidget(self._api_token_status)
        tahl.addStretch()
        api_card.layout().addWidget(token_action_row)

        # ── Tailscale HTTPS Profile section ─────────────────────────────
        sec_ts = self._collapsible_section(t("settings.api.section.tailscale"), "api_tailscale", icon="🔒")
        ts_card = self._card(container=sec_ts)

        self._row_label(
            ts_card,
            t("settings.api.ts_description"),
            wrap=True,
        )

        self._ts_https_switch = self._switch_row(
            ts_card,
            t("settings.api.ts_enable"),
            bool(getattr(cfg, "api_ts_https_enabled", False)),
            self._on_ts_https_toggle,
        )

        self._ts_https_status_lbl = QLabel("")
        self._ts_https_status_lbl.setWordWrap(True)
        self._ts_https_status_lbl.setStyleSheet(
            f"color: {T.text2}; font-size: 11px; background: transparent; padding: 4px 16px 2px;"
        )
        ts_card.layout().addWidget(self._ts_https_status_lbl)

        self._ts_https_port_lbl = QLabel("")
        self._ts_https_port_lbl.setStyleSheet(
            f"color: {T.text3}; font-size: 11px; background: transparent; padding: 0 16px 4px;"
        )
        ts_card.layout().addWidget(self._ts_https_port_lbl)

        ts_tok_row = QWidget()
        ts_tok_row.setStyleSheet("background: transparent;")
        tth = QHBoxLayout(ts_tok_row)
        tth.setContentsMargins(16, 0, 16, 4)
        self._ts_https_token_lbl = QLabel(self._masked_token())
        self._ts_https_token_lbl.setStyleSheet(
            f"color: {T.primary_text}; font-size: 11px; font-family: monospace;"
        )
        tth.addWidget(self._ts_https_token_lbl, 1)
        self._ts_https_copy_btn = QPushButton(t("settings.api.copy_btn"))
        self._ts_https_copy_btn.setFixedSize(80, 28)
        self._ts_https_copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ts_https_copy_btn.setStyleSheet(
            f"background: {T.surface3}; color: {T.text2}; border-radius: 6px; border: none; font-size: 11px; padding: 0 6px;"
        )
        self._ts_https_copy_btn.clicked.connect(self._on_api_copy_token)
        tth.addWidget(self._ts_https_copy_btn)
        ts_card.layout().addWidget(ts_tok_row)

        ts_reset_row = QWidget()
        ts_reset_row.setStyleSheet("background: transparent;")
        trh = QHBoxLayout(ts_reset_row)
        trh.setContentsMargins(16, 0, 16, 14)
        self._ts_https_reset_btn = QPushButton(t("settings.api.reset_profile_btn"))
        self._ts_https_reset_btn.setFixedHeight(32)
        self._ts_https_reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ts_https_reset_btn.setStyleSheet(
            f"background: {T.warning_bg}; color: {T.warning_text}; border-radius: 8px; border: none; font-size: 12px;"
        )
        self._ts_https_reset_btn.clicked.connect(self._on_ts_https_reset)
        trh.addWidget(self._ts_https_reset_btn)
        self._ts_https_reset_status = QLabel("")
        self._ts_https_reset_status.setStyleSheet(
            f"color: {T.text2}; font-size: 11px; background: transparent;"
        )
        trh.addWidget(self._ts_https_reset_status)
        trh.addStretch()
        ts_card.layout().addWidget(ts_reset_row)

        self._refresh_ts_https_status()
        self._layout.addSpacing(20)

    # ── Handlers ──────────────────────────────────────────────────────────

    def _masked_token(self) -> str:
        tok = str(getattr(self._app.config, "api_token", "") or "")
        if not tok:
            return t("settings.api.no_token")
        if len(tok) <= 8:
            return "*" * len(tok)
        return tok[:8] + "••••••••••••••••"

    def _refresh_api_status_label(self) -> None:
        lbl = getattr(self, "_api_status_lbl", None)
        if lbl is None:
            return
        try:
            running = False
            try:
                from api.server import is_api_running

                running = is_api_running()
            except ImportError:
                pass
            cfg = self._app.config
            if running:
                port = getattr(cfg, "api_port", 7799)
                lbl.setText(t("settings.api.running", port=port))
                lbl.setStyleSheet(
                    f"color: {T.success}; font-size: 11px; background: transparent; padding: 2px 16px 8px;"
                )
            elif getattr(cfg, "api_enabled", False):
                lbl.setText(t("settings.api.enabled_not_started"))
                lbl.setStyleSheet(
                    f"color: {T.warning_text}; font-size: 11px; background: transparent; padding: 2px 16px 8px;"
                )
            else:
                lbl.setText(t("settings.api.disabled"))
                lbl.setStyleSheet(
                    f"color: {T.text3}; font-size: 11px; background: transparent; padding: 2px 16px 8px;"
                )
        except Exception:
            pass

    def _on_api_toggle(self, enabled: bool) -> None:
        cfg = self._app.config
        cfg.set("api_enabled", enabled)
        cfg.save()
        if enabled:
            try:
                from api.server import is_api_running, start_api_server
                from app.event_bus import bus as _bus

                svc = getattr(self._app, "_service", None)
                if svc is None:
                    raise RuntimeError("DownloadService reference not found on MainWindow")
                if not is_api_running():
                    start_api_server(service=svc, config=cfg, bus=_bus)
                QTimer.singleShot(400, self._refresh_api_status_label)
                self._app.toast(t("settings.api.enabled_toast"), "success")
            except ImportError:
                self._api_switch.setChecked(False)
                cfg.set("api_enabled", False)
                cfg.save()
                self._app.toast(t("settings.api.missing_deps"), "error")
            except Exception as exc:
                logger.exception("Failed to start API server from Settings: %s", exc)
                # Server never came up — leaving the switch on would persist
                # api_enabled=True and retry the broken start on next launch.
                self._api_switch.setChecked(False)
                cfg.set("api_enabled", False)
                cfg.save()
                self._refresh_api_status_label()
                self._app.toast(t("settings.api.start_error", err=f"{exc!s:.60}"), "error")
        else:
            try:
                from api.server import stop_api_server

                threading.Thread(target=stop_api_server, daemon=True, name="omnidl-api-stop").start()
            except ImportError:
                pass
            QTimer.singleShot(600, self._refresh_api_status_label)
            self._app.toast(t("settings.api.disabled_toast"), "info")

    def _on_api_copy_token(self) -> None:
        tok = str(getattr(self._app.config, "api_token", "") or "")
        if not tok:
            self._app.toast(t("settings.api.no_token_toast"), "error")
            return
        QGuiApplication.clipboard().setText(tok)
        self._app.toast(t("settings.api.token_copied"), "success")

    def _on_api_rotate_token(self) -> None:
        reply = QMessageBox.question(
            self,
            t("settings.api.rotate_dialog_title"),
            t("settings.api.rotate_confirm_msg"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        new_token = _secrets.token_urlsafe(24)
        cfg = self._app.config
        cfg.set_api_token(new_token)
        self._refresh_api_token_label()

        st = self._api_token_status
        st.setText(t("settings.api.new_token_saved"))
        # `st` as context: Qt drops the call if a panel rebuild deleted the label.
        QTimer.singleShot(3000, st, lambda: st.setText(""))

        was_running = False
        try:
            from api.server import is_api_running

            was_running = is_api_running()
        except ImportError:
            pass

        if was_running:
            self._app.toast(t("settings.api.restarting_toast"), "info")

            def _do_restart():
                try:
                    from api.server import restart_api_server
                    from app.event_bus import bus as _bus

                    svc = getattr(self._app, "_service", None)
                    if svc:
                        restart_api_server(service=svc, config=cfg, bus=_bus)
                    ui_bridge.post(self._refresh_api_status_label)
                    ui_bridge.post(lambda: self._app.toast(t("settings.api.restarted_toast"), "success"))
                except Exception as exc:
                    logger.exception("Failed to restart API after token rotation: %s", exc)
                    ui_bridge.post(
                        lambda e=exc: self._app.toast(
                            t("settings.api.restart_error", err=f"{e!s:.60}"), "error"
                        )
                    )

            threading.Thread(target=_do_restart, daemon=True, name="omnidl-api-restart").start()
        else:
            self._app.toast(t("settings.api.new_token_toast"), "success")

    def _refresh_api_token_label(self) -> None:
        masked = self._masked_token()
        lbl = getattr(self, "_api_token_lbl", None)
        if lbl:
            lbl.setText(masked)
        ts_lbl = getattr(self, "_ts_https_token_lbl", None)
        if ts_lbl:
            ts_lbl.setText(masked)

    def _refresh_ts_https_status(self) -> None:
        lbl = getattr(self, "_ts_https_status_lbl", None)
        if lbl is None:
            return
        cfg = self._app.config
        enabled = getattr(cfg, "api_ts_https_enabled", False)
        dns_name = getattr(cfg, "api_ts_https_dns_name", "") or ""
        int_port = getattr(cfg, "api_ts_https_internal_port", 0) or 0
        port_lbl = getattr(self, "_ts_https_port_lbl", None)

        if enabled and dns_name:
            lbl.setText(t("settings.api.ts_running", dns=dns_name))
            lbl.setStyleSheet(
                f"color: {T.success}; font-size: 11px; background: transparent; padding: 4px 16px 2px;"
            )
            if port_lbl:
                port_lbl.setText(t("settings.api.ts_internal_port", port=int_port))
        elif enabled and not dns_name:
            lbl.setText(t("settings.api.ts_setting_up"))
            lbl.setStyleSheet(
                f"color: {T.text2}; font-size: 11px; background: transparent; padding: 4px 16px 2px;"
            )
            if port_lbl:
                port_lbl.setText(t("settings.api.ts_internal_port_plain", port=int_port) if int_port else "")
        else:
            lbl.setText(t("settings.api.ts_off"))
            lbl.setStyleSheet(
                f"color: {T.text3}; font-size: 11px; background: transparent; padding: 4px 16px 2px;"
            )
            if port_lbl:
                port_lbl.setText("")

        tok_lbl = getattr(self, "_ts_https_token_lbl", None)
        if tok_lbl:
            tok_lbl.setText(self._masked_token())

    def _on_ts_https_toggle(self, enabled: bool) -> None:
        cfg = self._app.config

        if enabled:
            if not getattr(cfg, "api_enabled", False):
                self._ts_https_switch.setChecked(False)
                self._app.toast(t("settings.api.enable_api_first"), "error")
                return
            if not shutil.which("tailscale"):
                self._ts_https_switch.setChecked(False)
                self._app.toast(t("settings.api.no_tailscale_cli"), "error")
                return

            new_port = _pick_bindable_port()
            cfg.set("api_ts_https_enabled", True)
            cfg.set("api_ts_https_internal_port", new_port)
            cfg.save()
            self._refresh_ts_https_status()
            self._app.toast(t("settings.api.setting_up_https"), "info")

            def _enable_worker():
                try:
                    from api.tailscale_https import get_tailscale_dns_name, start_tailscale_serve

                    dns = get_tailscale_dns_name()
                    if dns:
                        cfg.set("api_ts_https_dns_name", dns)
                        cfg.save()
                    ok = start_tailscale_serve(new_port)
                    if not ok:
                        cfg.set("api_ts_https_enabled", False)
                        cfg.set("api_ts_https_internal_port", 0)
                        cfg.save()
                        try:
                            from api.server import restart_api_server
                            from app.event_bus import bus as _bus

                            svc = getattr(self._app, "_service", None)
                            if svc:
                                restart_api_server(service=svc, config=cfg, bus=_bus)
                        except Exception as exc:
                            logger.exception("HTTPS Profile rollback: API restart failed: %s", exc)
                        hint = t("settings.api.login_hint") if not dns else ""
                        ui_bridge.post(lambda: self._ts_https_switch.setChecked(False))
                        ui_bridge.post(self._refresh_ts_https_status)
                        ui_bridge.post(
                            lambda h=hint: self._app.toast(t("settings.api.serve_failed", hint=h), "error")
                        )
                        return
                    try:
                        from api.server import restart_api_server
                        from app.event_bus import bus as _bus

                        svc = getattr(self._app, "_service", None)
                        if svc:
                            restart_api_server(service=svc, config=cfg, bus=_bus)
                    except Exception as exc:
                        logger.exception("HTTPS Profile: API restart failed: %s", exc)
                        ui_bridge.post(
                            lambda e=exc: self._app.toast(
                                t("settings.api.restart_error", err=f"{e!s:.60}"), "error"
                            )
                        )
                        return
                    ui_bridge.post(self._refresh_ts_https_status)
                    if dns:
                        ui_bridge.post(
                            lambda d=dns: self._app.toast(
                                t("settings.api.https_enabled_toast", dns=d), "success"
                            )
                        )
                    else:
                        ui_bridge.post(lambda: self._app.toast(t("settings.api.no_dns_error"), "error"))
                except Exception as exc:
                    logger.exception("HTTPS Profile enable error: %s", exc)
                    ui_bridge.post(
                        lambda e=exc: self._app.toast(
                            t("settings.api.setup_error", err=f"{e!s:.60}"), "error"
                        )
                    )

            threading.Thread(target=_enable_worker, daemon=True, name="omnidl-ts-https-enable").start()

        else:
            old_port = getattr(cfg, "api_ts_https_internal_port", 0) or 0
            cfg.set("api_ts_https_enabled", False)
            cfg.set("api_ts_https_dns_name", "")
            cfg.save()
            self._refresh_ts_https_status()
            self._app.toast(t("settings.api.disabling_https"), "info")

            def _disable_worker():
                try:
                    if old_port:
                        from api.tailscale_https import stop_tailscale_serve

                        stop_tailscale_serve(old_port)
                    try:
                        from api.server import restart_api_server
                        from app.event_bus import bus as _bus

                        svc = getattr(self._app, "_service", None)
                        if svc:
                            restart_api_server(service=svc, config=cfg, bus=_bus)
                    except Exception as exc:
                        logger.exception("HTTPS Profile disable: API restart error: %s", exc)
                    ui_bridge.post(self._refresh_ts_https_status)
                    ui_bridge.post(lambda: self._app.toast(t("settings.api.https_disabled_toast"), "info"))
                except Exception as exc:
                    logger.exception("HTTPS Profile disable error: %s", exc)
                    ui_bridge.post(
                        lambda e=exc: self._app.toast(
                            t("settings.api.disable_error", err=f"{e!s:.60}"), "error"
                        )
                    )

            threading.Thread(target=_disable_worker, daemon=True, name="omnidl-ts-https-disable").start()

    def _on_ts_https_reset(self) -> None:
        cfg = self._app.config
        if not getattr(cfg, "api_ts_https_enabled", False):
            self._app.toast(t("settings.api.enable_https_first"), "error")
            return

        reply = QMessageBox.question(
            self,
            t("settings.api.reset_profile_btn"),
            t("settings.api.reset_confirm_msg"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        new_port = _pick_bindable_port()
        new_token = _secrets.token_urlsafe(24)
        cfg.set("api_ts_https_internal_port", new_port)
        cfg.set("api_ts_https_dns_name", "")
        cfg.save()
        self._refresh_ts_https_status()

        st = self._ts_https_reset_status
        st.setText(t("settings.api.resetting_status"))
        self._app.toast(t("settings.api.resetting_toast"), "info")

        def _reset_worker():
            try:
                from api.tailscale_https import (
                    get_tailscale_dns_name,
                    reset_tailscale_serve,
                    start_tailscale_serve,
                )

                reset_tailscale_serve()
                ok = start_tailscale_serve(new_port)
                if not ok:
                    cfg.set("api_ts_https_enabled", False)
                    cfg.set("api_ts_https_internal_port", 0)
                    cfg.set("api_ts_https_dns_name", "")
                    cfg.save()
                    try:
                        from api.server import restart_api_server
                        from app.event_bus import bus as _bus

                        svc = getattr(self._app, "_service", None)
                        if svc:
                            restart_api_server(service=svc, config=cfg, bus=_bus)
                    except Exception as exc:
                        logger.exception("HTTPS Profile reset rollback: API restart failed: %s", exc)
                    ui_bridge.post(lambda: self._ts_https_switch.setChecked(False))
                    ui_bridge.post(self._refresh_ts_https_status)
                    ui_bridge.post(lambda: st.setText(""))
                    ui_bridge.post(lambda: self._app.toast(t("settings.api.reset_serve_failed"), "error"))
                    return
                dns = get_tailscale_dns_name()
                cfg.set_api_token(new_token)
                if dns:
                    cfg.set("api_ts_https_dns_name", dns)
                cfg.save()
                try:
                    from api.server import restart_api_server
                    from app.event_bus import bus as _bus

                    svc = getattr(self._app, "_service", None)
                    if svc:
                        restart_api_server(service=svc, config=cfg, bus=_bus)
                except Exception as exc:
                    logger.exception("HTTPS Profile reset: API restart error: %s", exc)
                ui_bridge.post(self._refresh_ts_https_status)
                ui_bridge.post(self._refresh_api_token_label)
                ui_bridge.post(lambda: st.setText(""))
                if dns:
                    ui_bridge.post(
                        lambda d=dns: self._app.toast(
                            t("settings.api.reset_success_dns", dns=d),
                            "success",
                        )
                    )
                else:
                    ui_bridge.post(lambda: self._app.toast(t("settings.api.reset_success"), "success"))
            except Exception as exc:
                logger.exception("HTTPS Profile reset error: %s", exc)
                ui_bridge.post(
                    lambda e=exc: self._app.toast(t("settings.api.reset_error", err=f"{e!s:.60}"), "error")
                )

        threading.Thread(target=_reset_worker, daemon=True, name="omnidl-ts-https-reset").start()
