"""
ui/tabs/settings/remote_api_panel.py
Settings panel: 🔌 REMOTE API — iOS / Mobile remote control.

Dependencies on MainWindow:
  self._app.config   – api_enabled, api_port, api_token, set_api_token
  self._app.service  – forwarded to start_api_server
  self._app.toast    – feedback toasts
"""
from __future__ import annotations

import random
import secrets as _secrets
import shutil
import threading
from typing import TYPE_CHECKING

try:
    import customtkinter as ctk
except ImportError:          # pragma: no cover
    ctk = None               # type: ignore[assignment]

from ui.tabs.settings._base_panel import _BasePanel
from ui.themes.tokens import T

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = __import__("logging").getLogger(__name__)


class RemoteApiPanel(_BasePanel):
    """
    Renders the 🔌 REMOTE API section and owns all related handlers.
    Fully self-contained — no cross-panel dependencies.
    """

    def __init__(self, master, app: "MainWindow") -> None:
        super().__init__(master, app)
        self._build()

    # ── Build ─────────────────────────────────────────────────────────────

    def _build(self) -> None:
        p   = self
        cfg = self._app.config

        self._section(p, "🔌   REMOTE API  (iOS / Mobile)")
        self._card_api = api_card = self._card(p)

        ctk.CTkLabel(
            api_card,
            text=(
                "Bật để điều khiển OmniDL từ xa qua mạng LAN (iPhone, Android).\n"
                "Server chạy trong luồng riêng, không ảnh hưởng download hiện tại.\n"
                "Chỉ bật khi cần — tắt khi không dùng để bảo mật thiết bị."
            ),
            font=ctk.CTkFont(size=11), text_color=T.text3,
            justify="left", anchor="w",
        ).pack(fill="x", padx=16, pady=(12, 4))

        # ── Toggle row ──────────────────────────────────────────────────
        toggle_row = ctk.CTkFrame(api_card, fg_color="transparent")
        toggle_row.pack(fill="x", padx=16, pady=(4, 0))
        ctk.CTkLabel(
            toggle_row, text="Bật Remote API",
            font=ctk.CTkFont(size=12), text_color=T.text2,
        ).pack(side="left")
        self._api_switch_var = ctk.BooleanVar(
            value=bool(getattr(cfg, "api_enabled", False))
        )
        self._api_switch = ctk.CTkSwitch(
            toggle_row, variable=self._api_switch_var, text="",
            command=self._on_api_toggle, onvalue=True, offvalue=False,
            progress_color=T.primary, button_color=T.primary_text,
        )
        self._api_switch.pack(side="right")
        self._switches.append(self._api_switch)

        self._api_status_lbl = ctk.CTkLabel(
            api_card, text="",
            font=ctk.CTkFont(size=11), text_color=T.text2, anchor="w",
        )
        self._api_status_lbl.pack(fill="x", padx=16, pady=(2, 8))
        self._refresh_api_status_label()

        ctk.CTkFrame(api_card, fg_color=T.border, height=1).pack(
            fill="x", padx=16, pady=(0, 10)
        )

        # ── Token row ───────────────────────────────────────────────────
        ctk.CTkLabel(
            api_card, text="🔑  Bearer Token",
            font=ctk.CTkFont(size=11, weight="bold"), text_color=T.text2, anchor="w",
        ).pack(fill="x", padx=16, pady=(0, 4))

        token_row = ctk.CTkFrame(api_card, fg_color="transparent")
        token_row.pack(fill="x", padx=16, pady=(0, 4))
        self._api_token_lbl = ctk.CTkLabel(
            token_row, text=self._masked_token(),
            font=ctk.CTkFont(size=11, family="Courier"),
            text_color=T.primary_text, anchor="w",
        )
        self._api_token_lbl.pack(side="left", fill="x", expand=True)
        self._api_copy_btn = ctk.CTkButton(
            token_row, text="📋 Copy", width=70, height=28, corner_radius=6,
            fg_color=T.surface3, hover_color=T.border2, text_color=T.text2,
            font=ctk.CTkFont(size=11), command=self._on_api_copy_token,
        )
        self._api_copy_btn.pack(side="right", padx=(6, 0))

        token_action_row = ctk.CTkFrame(api_card, fg_color="transparent")
        token_action_row.pack(fill="x", padx=16, pady=(0, 14))
        self._api_rotate_btn = ctk.CTkButton(
            token_action_row, text="🔄  Tạo token mới",
            height=32, corner_radius=8,
            fg_color=T.surface3, hover_color=T.border2, text_color=T.text2,
            font=ctk.CTkFont(size=12), command=self._on_api_rotate_token,
        )
        self._api_rotate_btn.pack(side="left")
        self._api_token_status = ctk.CTkLabel(
            token_action_row, text="",
            font=ctk.CTkFont(size=11), text_color=T.text2,
        )
        self._api_token_status.pack(side="left", padx=(10, 0))

        # ── Tailscale HTTPS Profile section ─────────────────────────────
        self._section(p, "🔒   TAILSCALE HTTPS PROFILE")
        self._card_ts_https = ts_card = self._card(p)

        ctk.CTkLabel(
            ts_card,
            text=(
                "Truy cap Remote API qua HTTPS tren mang Tailscale.\n"
                "OmniDL tu dong chay tailscale serve — khong can mo port tuong lua.\n"
                "Yeu cau: Tailscale da cai va dang nhap tren may nay."
            ),
            font=ctk.CTkFont(size=11), text_color=T.text3,
            justify="left", anchor="w",
        ).pack(fill="x", padx=16, pady=(12, 4))

        # Toggle row
        ts_toggle_row = ctk.CTkFrame(ts_card, fg_color="transparent")
        ts_toggle_row.pack(fill="x", padx=16, pady=(4, 0))
        ctk.CTkLabel(
            ts_toggle_row, text="Bat Tailscale HTTPS Profile",
            font=ctk.CTkFont(size=12), text_color=T.text2,
        ).pack(side="left")
        self._ts_https_var = ctk.BooleanVar(
            value=bool(getattr(cfg, "api_ts_https_enabled", False))
        )
        self._ts_https_switch = ctk.CTkSwitch(
            ts_toggle_row, variable=self._ts_https_var, text="",
            command=self._on_ts_https_toggle, onvalue=True, offvalue=False,
            progress_color=T.primary, button_color=T.primary_text,
        )
        self._ts_https_switch.pack(side="right")
        self._switches.append(self._ts_https_switch)

        # Status label
        self._ts_https_status_lbl = ctk.CTkLabel(
            ts_card, text="",
            font=ctk.CTkFont(size=11), text_color=T.text2,
            anchor="w", justify="left",
        )
        self._ts_https_status_lbl.pack(fill="x", padx=16, pady=(4, 2))

        # Internal port label
        self._ts_https_port_lbl = ctk.CTkLabel(
            ts_card, text="",
            font=ctk.CTkFont(size=11), text_color=T.text3,
            anchor="w",
        )
        self._ts_https_port_lbl.pack(fill="x", padx=16, pady=(0, 4))

        # Token row (shows the same token as the main API section)
        ts_token_row = ctk.CTkFrame(ts_card, fg_color="transparent")
        ts_token_row.pack(fill="x", padx=16, pady=(0, 4))
        self._ts_https_token_lbl = ctk.CTkLabel(
            ts_token_row, text=self._masked_token(),
            font=ctk.CTkFont(size=11, family="Courier"),
            text_color=T.primary_text, anchor="w",
        )
        self._ts_https_token_lbl.pack(side="left", fill="x", expand=True)
        self._ts_https_copy_btn = ctk.CTkButton(
            ts_token_row, text="Copy", width=70, height=28, corner_radius=6,
            fg_color=T.surface3, hover_color=T.border2, text_color=T.text2,
            font=ctk.CTkFont(size=11), command=self._on_api_copy_token,
        )
        self._ts_https_copy_btn.pack(side="right", padx=(6, 0))

        # Reset Profile button (orange warning color)
        ts_reset_row = ctk.CTkFrame(ts_card, fg_color="transparent")
        ts_reset_row.pack(fill="x", padx=16, pady=(0, 14))
        self._ts_https_reset_btn = ctk.CTkButton(
            ts_reset_row, text="Reset Profile",
            height=32, corner_radius=8,
            fg_color="#d97706", hover_color="#b45309", text_color="#ffffff",
            font=ctk.CTkFont(size=12), command=self._on_ts_https_reset,
        )
        self._ts_https_reset_btn.pack(side="left")
        self._ts_https_reset_status = ctk.CTkLabel(
            ts_reset_row, text="",
            font=ctk.CTkFont(size=11), text_color=T.text2,
        )
        self._ts_https_reset_status.pack(side="left", padx=(10, 0))

        self._refresh_ts_https_status()

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
        if lbl is None or not lbl.winfo_exists():
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
                lbl.configure(
                    text=f"🟢  Đang chạy  —  http://<IP LAN>:{port}",
                    text_color=T.success if hasattr(T, "success") else "#22c55e",
                )
            elif getattr(cfg, "api_enabled", False):
                lbl.configure(
                    text="⚠️  Đã bật nhưng chưa khởi động (thiếu fastapi/uvicorn?)",
                    text_color=T.warning_text if hasattr(T, "warning_text") else T.text2,
                )
            else:
                lbl.configure(text="⚫  Đã tắt", text_color=T.text3)
        except Exception:
            pass

    def _on_api_toggle(self) -> None:
        enabled = self._api_switch_var.get()
        cfg = self._app.config
        cfg.set("api_enabled", enabled)
        cfg.save()
        if enabled:
            try:
                from api.server import is_api_running, start_api_server
                from app.event_bus import bus as _bus
                svc = getattr(self._app, "service", None) or getattr(self._app, "_service", None)
                if svc is None:
                    raise RuntimeError("DownloadService reference not found on MainWindow")
                if not is_api_running():
                    start_api_server(service=svc, config=cfg, bus=_bus)
                self.after(400, self._refresh_api_status_label)
                self._app.toast("✅  Remote API đã bật.", "success")
            except ImportError:
                self._api_switch_var.set(False)
                cfg.set("api_enabled", False)
                cfg.save()
                self._app.toast(
                    "⚠️  Cần cài fastapi & uvicorn trước: pip install -r requirements-api.txt",
                    "error",
                )
            except Exception as exc:
                logger.exception("Failed to start API server from Settings: %s", exc)
                self._app.toast(f"Lỗi khởi động API: {exc!s:.60}", "error")
        else:
            try:
                from api.server import stop_api_server
                threading.Thread(
                    target=stop_api_server, daemon=True, name="omnidl-api-stop"
                ).start()
            except ImportError:
                pass
            self.after(600, self._refresh_api_status_label)
            self._app.toast("⚫  Remote API đã tắt.", "info")

    def _on_api_copy_token(self) -> None:
        tok = str(getattr(self._app.config, "api_token", "") or "")
        if not tok:
            self._app.toast("Chưa có token. Hãy bật Remote API trước.", "error")
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(tok)
            self._app.toast("✅  Token đã sao chép vào clipboard.", "success")
        except Exception as exc:
            logger.warning("Clipboard copy failed: %s", exc)
            self._app.toast(f"Không thể copy: {exc!s:.50}", "error")

    def _on_api_rotate_token(self) -> None:
        import secrets as _sec
        new_token = _sec.token_urlsafe(24)
        cfg = self._app.config
        cfg.set_api_token(new_token)

        lbl = getattr(self, "_api_token_lbl", None)
        if lbl and lbl.winfo_exists():
            lbl.configure(text=self._masked_token())

        st = getattr(self, "_api_token_status", None)
        if st and st.winfo_exists():
            st.configure(text="✅  Token mới đã lưu", text_color=T.text2)
            self.after(3000, lambda: st.configure(text="") if st.winfo_exists() else None)

        was_running = False
        try:
            from api.server import is_api_running
            was_running = is_api_running()
        except ImportError:
            pass

        if was_running:
            self._app.toast("🔄  Token mới đã tạo — đang khởi động lại server…", "info")

            def _do_restart() -> None:
                try:
                    from api.server import restart_api_server
                    from app.event_bus import bus as _bus
                    svc = getattr(self._app, "service", None) or getattr(self._app, "_service", None)
                    if svc:
                        restart_api_server(service=svc, config=cfg, bus=_bus)
                    self._ui_queue.put(self._refresh_api_status_label)
                    self._ui_queue.put(lambda: self._app.toast(
                        "✅  Server đã khởi động lại với token mới.", "success"
                    ))
                except Exception as exc:
                    logger.exception("Failed to restart API after token rotation: %s", exc)
                    self._ui_queue.put(lambda e=exc: self._app.toast(
                        f"Lỗi restart API: {e!s:.60}", "error"
                    ))

            threading.Thread(target=_do_restart, daemon=True, name="omnidl-api-restart").start()
        else:
            self._app.toast("✅  Token mới đã tạo. Copy và cập nhật trên thiết bị.", "success")

    # ── Tailscale HTTPS Profile helpers ───────────────────────────────────

    def _refresh_ts_https_status(self) -> None:
        """Update Tailscale HTTPS section labels from current config. UI thread only."""
        lbl = getattr(self, "_ts_https_status_lbl", None)
        if lbl is None or not lbl.winfo_exists():
            return
        cfg = self._app.config
        enabled  = getattr(cfg, "api_ts_https_enabled", False)
        dns_name = getattr(cfg, "api_ts_https_dns_name", "") or ""
        int_port = getattr(cfg, "api_ts_https_internal_port", 0) or 0
        port_lbl = getattr(self, "_ts_https_port_lbl", None)

        if enabled and dns_name:
            lbl.configure(
                text=f"Remote API dang chay tai:\nhttps://{dns_name}",
                text_color="#22c55e",
            )
            if port_lbl and port_lbl.winfo_exists():
                port_lbl.configure(text=f"Port noi bo (ngau nhien): {int_port}")
        elif enabled and not dns_name:
            lbl.configure(
                text="Dang thiet lap... (kiem tra Tailscale da ket noi chua)",
                text_color=T.text2,
            )
            if port_lbl and port_lbl.winfo_exists():
                port_lbl.configure(text=f"Port noi bo: {int_port}" if int_port else "")
        else:
            lbl.configure(text="Da tat", text_color=T.text3)
            if port_lbl and port_lbl.winfo_exists():
                port_lbl.configure(text="")

        tok_lbl = getattr(self, "_ts_https_token_lbl", None)
        if tok_lbl and tok_lbl.winfo_exists():
            tok_lbl.configure(text=self._masked_token())

    def _on_ts_https_toggle(self) -> None:
        enabled = self._ts_https_var.get()
        cfg = self._app.config

        if enabled:
            if not getattr(cfg, "api_enabled", False):
                self._ts_https_var.set(False)
                self._app.toast("Bat Remote API truoc khi dung Tailscale HTTPS Profile.", "error")
                return
            if not shutil.which("tailscale"):
                self._ts_https_var.set(False)
                self._app.toast("Khong tim thay tailscale CLI — cai Tailscale tren may nay.", "error")
                return

            new_port = random.randint(50000, 65000)
            cfg.set("api_ts_https_enabled", True)
            cfg.set("api_ts_https_internal_port", new_port)
            cfg.save()
            self._refresh_ts_https_status()
            self._app.toast("Dang thiet lap Tailscale HTTPS Profile...", "info")

            def _enable_worker() -> None:
                try:
                    from api.tailscale_https import (
                        get_tailscale_dns_name,
                        start_tailscale_serve,
                    )
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
                        hint = " Tailscale chua dang nhap? Chay 'tailscale login' roi thu lai." if not dns else ""
                        self._ui_queue.put(lambda: self._ts_https_var.set(False))
                        self._ui_queue.put(self._refresh_ts_https_status)
                        self._ui_queue.put(lambda h=hint: self._app.toast(
                            f"tailscale serve that bai.{h}", "error"
                        ))
                        return
                    try:
                        from api.server import restart_api_server
                        from app.event_bus import bus as _bus
                        svc = getattr(self._app, "service", None) or getattr(self._app, "_service", None)
                        if svc:
                            restart_api_server(service=svc, config=cfg, bus=_bus)
                    except Exception as exc:
                        logger.exception("HTTPS Profile: API restart failed: %s", exc)
                        self._ui_queue.put(lambda e=exc: self._app.toast(
                            f"Loi restart API: {e!s:.60}", "error"
                        ))
                        return
                    self._ui_queue.put(self._refresh_ts_https_status)
                    if dns:
                        self._ui_queue.put(lambda d=dns: self._app.toast(
                            f"HTTPS Profile da bat: https://{d}", "success"
                        ))
                    else:
                        self._ui_queue.put(lambda: self._app.toast(
                            "serve da bat nhung khong lay duoc DNS name. Kiem tra Tailscale da dang nhap.", "error"
                        ))
                except Exception as exc:
                    logger.exception("HTTPS Profile enable error: %s", exc)
                    self._ui_queue.put(lambda e=exc: self._app.toast(
                        f"Loi thiet lap HTTPS Profile: {e!s:.60}", "error"
                    ))

            threading.Thread(target=_enable_worker, daemon=True, name="omnidl-ts-https-enable").start()

        else:
            old_port = getattr(cfg, "api_ts_https_internal_port", 0) or 0
            cfg.set("api_ts_https_enabled", False)
            cfg.set("api_ts_https_dns_name", "")
            cfg.save()
            self._refresh_ts_https_status()
            self._app.toast("Dang tat Tailscale HTTPS Profile...", "info")

            def _disable_worker() -> None:
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
                    self._ui_queue.put(self._refresh_ts_https_status)
                    self._ui_queue.put(lambda: self._app.toast("Tailscale HTTPS Profile da tat.", "info"))
                except Exception as exc:
                    logger.exception("HTTPS Profile disable error: %s", exc)
                    self._ui_queue.put(lambda e=exc: self._app.toast(
                        f"Loi tat HTTPS Profile: {e!s:.60}", "error"
                    ))

            threading.Thread(target=_disable_worker, daemon=True, name="omnidl-ts-https-disable").start()

    def _on_ts_https_reset(self) -> None:
        """Reset Profile: new random port + new token, tailscale serve reset, restart API."""
        cfg = self._app.config
        if not getattr(cfg, "api_ts_https_enabled", False):
            self._app.toast("Hay bat HTTPS Profile truoc khi reset.", "error")
            return

        new_port  = random.randint(50000, 65000)
        new_token = _secrets.token_urlsafe(24)

        cfg.set("api_ts_https_internal_port", new_port)
        cfg.set("api_ts_https_dns_name", "")
        cfg.set_api_token(new_token)
        cfg.save()
        self._refresh_ts_https_status()

        st = getattr(self, "_ts_https_reset_status", None)
        if st and st.winfo_exists():
            st.configure(text="Dang reset...", text_color=T.text2)

        self._app.toast("Dang reset Tailscale HTTPS Profile...", "info")

        def _reset_worker() -> None:
            try:
                from api.tailscale_https import (
                    get_tailscale_dns_name,
                    reset_tailscale_serve,
                    start_tailscale_serve,
                )
                reset_tailscale_serve()
                ok = start_tailscale_serve(new_port)
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
                self._ui_queue.put(self._refresh_ts_https_status)
                self._ui_queue.put(lambda: (
                    getattr(self, "_ts_https_reset_status", None) and
                    self._ts_https_reset_status.winfo_exists() and
                    self._ts_https_reset_status.configure(text="")
                ))
                if ok:
                    self._ui_queue.put(lambda: self._app.toast(
                        "Profile da reset. Token moi da tao — cap nhat tren thiet bi.", "success"
                    ))
                else:
                    self._ui_queue.put(lambda: self._app.toast(
                        "tailscale serve that bai khi reset. Kiem tra Tailscale da dang nhap.", "error"
                    ))
            except Exception as exc:
                logger.exception("HTTPS Profile reset error: %s", exc)
                self._ui_queue.put(lambda e=exc: self._app.toast(
                    f"Loi reset Profile: {e!s:.60}", "error"
                ))

        threading.Thread(target=_reset_worker, daemon=True, name="omnidl-ts-https-reset").start()

    # ── Theme refresh ─────────────────────────────────────────────────────

    def _on_theme(self) -> None:
        if not self.winfo_exists():
            return
        w = getattr(self, "_card_api", None)
        if w and w.winfo_exists():
            w.configure(fg_color=T.surface, border_color=T.border)
        for lbl in self._section_labels:
            if lbl.winfo_exists():
                lbl.configure(text_color=T.text3)
        for sw in self._switches:
            if sw.winfo_exists():
                sw.configure(progress_color=T.primary)
        w = getattr(self, "_api_token_lbl", None)
        if w and w.winfo_exists():
            w.configure(text_color=T.primary_text)
        w = getattr(self, "_api_status_lbl", None)
        if w and w.winfo_exists():
            self._refresh_api_status_label()
        w = getattr(self, "_api_token_status", None)
        if w and w.winfo_exists():
            w.configure(text_color=T.text2)
        for attr in ("_api_copy_btn", "_api_rotate_btn"):
            w = getattr(self, attr, None)
            if w and w.winfo_exists():
                w.configure(fg_color=T.surface3, hover_color=T.border2, text_color=T.text2)
        # Tailscale HTTPS Profile section
        w = getattr(self, "_card_ts_https", None)
        if w and w.winfo_exists():
            w.configure(fg_color=T.surface, border_color=T.border)
        w = getattr(self, "_ts_https_token_lbl", None)
        if w and w.winfo_exists():
            w.configure(text_color=T.primary_text)
        w = getattr(self, "_ts_https_copy_btn", None)
        if w and w.winfo_exists():
            w.configure(fg_color=T.surface3, hover_color=T.border2, text_color=T.text2)
        w = getattr(self, "_ts_https_reset_status", None)
        if w and w.winfo_exists():
            w.configure(text_color=T.text2)
        # Reset button keeps orange regardless of theme — intentional warning color
        self._refresh_ts_https_status()
