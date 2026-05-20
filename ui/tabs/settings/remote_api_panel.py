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
    QPushButton,
    QWidget,
)

from ui.signals import ui_bridge
from ui.tabs.settings._base_panel import _BasePanel
from ui.themes.tokens import T

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

        self._section(None, "🔌   REMOTE API  (iOS / Mobile)")
        api_card = self._card()

        self._row_label(
            api_card,
            "Bật để điều khiển OmniDL từ xa qua mạng LAN (iPhone, Android).\n"
            "Server chạy trong luồng riêng, không ảnh hưởng download hiện tại.\n"
            "Chỉ bật khi cần — tắt khi không dùng để bảo mật thiết bị.",
            wrap=True,
        )

        # ── Toggle row ──────────────────────────────────────────────────
        self._api_switch = self._switch_row(
            api_card,
            "Bật Remote API",
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
        tok_hdr = QLabel("🔑  Bearer Token")
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
        self._api_copy_btn = QPushButton("📋 Copy")
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
        self._api_rotate_btn = QPushButton("🔄  Tạo token mới")
        self._api_rotate_btn.setFixedHeight(32)
        self._api_rotate_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._api_rotate_btn.setStyleSheet(
            f"background: {T.surface3}; color: {T.text2}; border-radius: 8px; border: none; font-size: 12px;"
        )
        self._api_rotate_btn.clicked.connect(self._on_api_rotate_token)
        tahl.addWidget(self._api_rotate_btn)
        self._api_token_status = QLabel("")
        self._api_token_status.setStyleSheet(f"color: {T.text2}; font-size: 11px; background: transparent;")
        tahl.addWidget(self._api_token_status)
        tahl.addStretch()
        api_card.layout().addWidget(token_action_row)

        # ── Tailscale HTTPS Profile section ─────────────────────────────
        self._section(None, "🔒   TAILSCALE HTTPS PROFILE")
        ts_card = self._card()

        self._row_label(
            ts_card,
            "Truy cập Remote API qua HTTPS trên mạng Tailscale.\n"
            "OmniDL tự động chạy tailscale serve — không cần mở port tường lửa.\n"
            "Yêu cầu: Tailscale đã cài và đang nhập trên máy này.",
            wrap=True,
        )

        self._ts_https_switch = self._switch_row(
            ts_card,
            "Bật Tailscale HTTPS Profile",
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
        self._ts_https_copy_btn = QPushButton("Copy")
        self._ts_https_copy_btn.setFixedSize(70, 28)
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
        self._ts_https_reset_btn = QPushButton("Reset Profile")
        self._ts_https_reset_btn.setFixedHeight(32)
        self._ts_https_reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ts_https_reset_btn.setStyleSheet(
            "background: #d97706; color: #ffffff; border-radius: 8px; border: none; font-size: 12px;"
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
            return "(chưa có token — bật API để tạo tự động)"
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
                lbl.setText(f"🟢  Đang chạy  —  http://<IP LAN>:{port}")
                lbl.setStyleSheet(
                    f"color: {T.success}; font-size: 11px; background: transparent; padding: 2px 16px 8px;"
                )
            elif getattr(cfg, "api_enabled", False):
                lbl.setText("⚠️  Đã bật nhưng chưa khởi động (thiếu fastapi/uvicorn?)")
                lbl.setStyleSheet(
                    f"color: {T.warning_text}; font-size: 11px; background: transparent; padding: 2px 16px 8px;"
                )
            else:
                lbl.setText("⚫  Đã tắt")
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
                self._app.toast("✅  Remote API đã bật.", "success")
            except ImportError:
                self._api_switch.setChecked(False)
                cfg.set("api_enabled", False)
                cfg.save()
                self._app.toast(
                    "⚠️  Cần cài fastapi & uvicorn trước: pip install -r requirements-api.txt", "error"
                )
            except Exception as exc:
                logger.exception("Failed to start API server from Settings: %s", exc)
                self._app.toast(f"Lỗi khởi động API: {exc!s:.60}", "error")
        else:
            try:
                from api.server import stop_api_server

                threading.Thread(target=stop_api_server, daemon=True, name="omnidl-api-stop").start()
            except ImportError:
                pass
            QTimer.singleShot(600, self._refresh_api_status_label)
            self._app.toast("⚫  Remote API đã tắt.", "info")

    def _on_api_copy_token(self) -> None:
        tok = str(getattr(self._app.config, "api_token", "") or "")
        if not tok:
            self._app.toast("Chưa có token. Hãy bật Remote API trước.", "error")
            return
        QGuiApplication.clipboard().setText(tok)
        self._app.toast("✅  Token đã sao chép vào clipboard.", "success")

    def _on_api_rotate_token(self) -> None:
        import secrets as _sec

        new_token = _sec.token_urlsafe(24)
        cfg = self._app.config
        cfg.set_api_token(new_token)
        self._refresh_api_token_label()

        st = self._api_token_status
        st.setText("✅  Token mới đã lưu")
        QTimer.singleShot(3000, lambda: st.setText(""))

        was_running = False
        try:
            from api.server import is_api_running

            was_running = is_api_running()
        except ImportError:
            pass

        if was_running:
            self._app.toast("🔄  Token mới đã tạo — đang khởi động lại server…", "info")

            def _do_restart():
                try:
                    from api.server import restart_api_server
                    from app.event_bus import bus as _bus

                    svc = getattr(self._app, "_service", None)
                    if svc:
                        restart_api_server(service=svc, config=cfg, bus=_bus)
                    ui_bridge.post(self._refresh_api_status_label)
                    ui_bridge.post(
                        lambda: self._app.toast("✅  Server đã khởi động lại với token mới.", "success")
                    )
                except Exception as exc:
                    logger.exception("Failed to restart API after token rotation: %s", exc)
                    ui_bridge.post(lambda e=exc: self._app.toast(f"Lỗi restart API: {e!s:.60}", "error"))

            threading.Thread(target=_do_restart, daemon=True, name="omnidl-api-restart").start()
        else:
            self._app.toast("✅  Token mới đã tạo. Copy và cập nhật trên thiết bị.", "success")

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
            lbl.setText(f"Remote API đang chạy tại:\nhttps://{dns_name}")
            lbl.setStyleSheet(
                "color: #22c55e; font-size: 11px; background: transparent; padding: 4px 16px 2px;"
            )
            if port_lbl:
                port_lbl.setText(f"Port nội bộ (ngẫu nhiên): {int_port}")
        elif enabled and not dns_name:
            lbl.setText("Đang thiết lập... (kiểm tra Tailscale đã kết nối chưa)")
            lbl.setStyleSheet(
                f"color: {T.text2}; font-size: 11px; background: transparent; padding: 4px 16px 2px;"
            )
            if port_lbl:
                port_lbl.setText(f"Port nội bộ: {int_port}" if int_port else "")
        else:
            lbl.setText("Đã tắt")
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
                self._app.toast("Bật Remote API trước khi dùng Tailscale HTTPS Profile.", "error")
                return
            if not shutil.which("tailscale"):
                self._ts_https_switch.setChecked(False)
                self._app.toast("Không tìm thấy tailscale CLI — cài Tailscale trên máy này.", "error")
                return

            new_port = _pick_bindable_port()
            cfg.set("api_ts_https_enabled", True)
            cfg.set("api_ts_https_internal_port", new_port)
            cfg.save()
            self._refresh_ts_https_status()
            self._app.toast("Đang thiết lập Tailscale HTTPS Profile...", "info")

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

                            svc = getattr(self._app, "service", None) or getattr(self._app, "_service", None)
                            if svc:
                                restart_api_server(service=svc, config=cfg, bus=_bus)
                        except Exception as exc:
                            logger.exception("HTTPS Profile rollback: API restart failed: %s", exc)
                        hint = (
                            " Tailscale chưa đăng nhập? Chạy 'tailscale login' rồi thử lại."
                            if not dns
                            else ""
                        )
                        ui_bridge.post(lambda: self._ts_https_switch.setChecked(False))
                        ui_bridge.post(self._refresh_ts_https_status)
                        ui_bridge.post(
                            lambda h=hint: self._app.toast(f"tailscale serve thất bại.{h}", "error")
                        )
                        return
                    try:
                        from api.server import restart_api_server
                        from app.event_bus import bus as _bus

                        svc = getattr(self._app, "service", None) or getattr(self._app, "_service", None)
                        if svc:
                            restart_api_server(service=svc, config=cfg, bus=_bus)
                    except Exception as exc:
                        logger.exception("HTTPS Profile: API restart failed: %s", exc)
                        ui_bridge.post(lambda e=exc: self._app.toast(f"Lỗi restart API: {e!s:.60}", "error"))
                        return
                    ui_bridge.post(self._refresh_ts_https_status)
                    if dns:
                        ui_bridge.post(
                            lambda d=dns: self._app.toast(f"HTTPS Profile đã bật: https://{d}", "success")
                        )
                    else:
                        ui_bridge.post(
                            lambda: self._app.toast(
                                "serve đã bật nhưng không lấy được DNS name. Kiểm tra Tailscale đã đăng nhập.",
                                "error",
                            )
                        )
                except Exception as exc:
                    logger.exception("HTTPS Profile enable error: %s", exc)
                    ui_bridge.post(
                        lambda e=exc: self._app.toast(f"Lỗi thiết lập HTTPS Profile: {e!s:.60}", "error")
                    )

            threading.Thread(target=_enable_worker, daemon=True, name="omnidl-ts-https-enable").start()

        else:
            old_port = getattr(cfg, "api_ts_https_internal_port", 0) or 0
            cfg.set("api_ts_https_enabled", False)
            cfg.set("api_ts_https_dns_name", "")
            cfg.save()
            self._refresh_ts_https_status()
            self._app.toast("Đang tắt Tailscale HTTPS Profile...", "info")

            def _disable_worker():
                try:
                    if old_port:
                        from api.tailscale_https import stop_tailscale_serve

                        stop_tailscale_serve(old_port)
                    try:
                        from api.server import restart_api_server
                        from app.event_bus import bus as _bus

                        svc = getattr(self._app, "service", None) or getattr(self._app, "_service", None)
                        if svc:
                            restart_api_server(service=svc, config=cfg, bus=_bus)
                    except Exception as exc:
                        logger.exception("HTTPS Profile disable: API restart error: %s", exc)
                    ui_bridge.post(self._refresh_ts_https_status)
                    ui_bridge.post(lambda: self._app.toast("Tailscale HTTPS Profile đã tắt.", "info"))
                except Exception as exc:
                    logger.exception("HTTPS Profile disable error: %s", exc)
                    ui_bridge.post(
                        lambda e=exc: self._app.toast(f"Lỗi tắt HTTPS Profile: {e!s:.60}", "error")
                    )

            threading.Thread(target=_disable_worker, daemon=True, name="omnidl-ts-https-disable").start()

    def _on_ts_https_reset(self) -> None:
        cfg = self._app.config
        if not getattr(cfg, "api_ts_https_enabled", False):
            self._app.toast("Hãy bật HTTPS Profile trước khi reset.", "error")
            return

        new_port = _pick_bindable_port()
        new_token = _secrets.token_urlsafe(24)
        cfg.set("api_ts_https_internal_port", new_port)
        cfg.set("api_ts_https_dns_name", "")
        cfg.set_api_token(new_token)
        cfg.save()
        self._refresh_ts_https_status()
        self._refresh_api_token_label()

        st = self._ts_https_reset_status
        st.setText("Đang reset...")
        self._app.toast("Đang reset Tailscale HTTPS Profile...", "info")

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

                        svc = getattr(self._app, "service", None) or getattr(self._app, "_service", None)
                        if svc:
                            restart_api_server(service=svc, config=cfg, bus=_bus)
                    except Exception as exc:
                        logger.exception("HTTPS Profile reset rollback: API restart failed: %s", exc)
                    ui_bridge.post(lambda: self._ts_https_switch.setChecked(False))
                    ui_bridge.post(self._refresh_ts_https_status)
                    ui_bridge.post(lambda: st.setText(""))
                    ui_bridge.post(
                        lambda: self._app.toast(
                            "tailscale serve thất bại khi reset. Kiểm tra Tailscale đã đăng nhập.", "error"
                        )
                    )
                    return
                dns = get_tailscale_dns_name()
                if dns:
                    cfg.set("api_ts_https_dns_name", dns)
                    cfg.save()
                try:
                    from api.server import restart_api_server
                    from app.event_bus import bus as _bus

                    svc = getattr(self._app, "service", None) or getattr(self._app, "_service", None)
                    if svc:
                        restart_api_server(service=svc, config=cfg, bus=_bus)
                except Exception as exc:
                    logger.exception("HTTPS Profile reset: API restart error: %s", exc)
                ui_bridge.post(self._refresh_ts_https_status)
                ui_bridge.post(lambda: st.setText(""))
                if dns:
                    ui_bridge.post(
                        lambda d=dns: self._app.toast(
                            f"Profile đã reset: https://{d}. Token mới đã tạo - cập nhật trên thiết bị.",
                            "success",
                        )
                    )
                else:
                    ui_bridge.post(
                        lambda: self._app.toast(
                            "Profile đã reset. Token mới đã tạo - cập nhật trên thiết bị.", "success"
                        )
                    )
            except Exception as exc:
                logger.exception("HTTPS Profile reset error: %s", exc)
                ui_bridge.post(lambda e=exc: self._app.toast(f"Lỗi reset Profile: {e!s:.60}", "error"))

        threading.Thread(target=_reset_worker, daemon=True, name="omnidl-ts-https-reset").start()
