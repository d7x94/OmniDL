"""
ui/tabs/settings/remote_api_panel.py
Settings panel: 🔌 REMOTE API — iOS / Mobile remote control.

Dependencies on MainWindow:
  self._app.config   – api_enabled, api_port, api_token, set_api_token
  self._app.service  – forwarded to start_api_server
  self._app.toast    – feedback toasts
"""
from __future__ import annotations

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
