"""
ui/tabs/settings/taildrop_panel.py
Settings panel: 📲 TAILDROP — send completed files to iPhone via Tailscale.

Dependencies on MainWindow:
  self._app.config  – taildrop_enabled, taildrop_target_node, taildrop_send_mode
  self._app.toast   – feedback toasts

Background worker: _on_taildrop_scan() uses a plain daemon thread (no _ui_queue
needed — result is posted directly via self.after(0, ...) on the Tk widget).
"""
from __future__ import annotations

import re
import threading
from typing import TYPE_CHECKING

try:
    import customtkinter as ctk
except ImportError:          # pragma: no cover
    ctk = None               # type: ignore[assignment]

from ui.themes.tokens import T
from ui.tabs.settings._base_panel import _BasePanel

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = __import__("logging").getLogger(__name__)

_NODE_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9\-\.]{0,252}[A-Za-z0-9])?$")


class TaildropPanel(_BasePanel):
    """
    Renders the 📲 TAILDROP section and owns all related handlers.
    No dependency on any other settings panel — fully self-contained.
    """

    def __init__(self, master, app: "MainWindow") -> None:
        super().__init__(master, app)
        self._build()

    # ── Build ─────────────────────────────────────────────────────────────

    def _build(self) -> None:
        p   = self
        cfg = self._app.config

        self._section(p, "📲   TAILDROP  (Gửi file → iPhone qua Tailscale)")
        self._card_taildrop = td_card = self._card(p)

        ctk.CTkLabel(
            td_card,
            text=(
                "Sau khi tải xong, tự động gửi file sang iPhone qua Taildrop (Tailscale).\n"
                "Yêu cầu: Tailscale CLI trên PC và Taildrop bật trên iPhone.\n"
                "File xuất hiện trong ứng dụng Files của iOS."
            ),
            font=ctk.CTkFont(size=11), text_color=T.text3,
            justify="left", anchor="w",
        ).pack(fill="x", padx=16, pady=(12, 4))

        # ── Enable toggle ────────────────────────────────────────────────
        td_toggle_row = ctk.CTkFrame(td_card, fg_color="transparent")
        td_toggle_row.pack(fill="x", padx=16, pady=(4, 0))
        ctk.CTkLabel(
            td_toggle_row, text="Bật Taildrop",
            font=ctk.CTkFont(size=12), text_color=T.text2,
        ).pack(side="left")
        self._td_switch_var = ctk.BooleanVar(
            value=bool(getattr(cfg, "taildrop_enabled", False))
        )
        self._td_switch = ctk.CTkSwitch(
            td_toggle_row, variable=self._td_switch_var, text="",
            command=self._on_taildrop_toggle, onvalue=True, offvalue=False,
            progress_color=T.primary, button_color=T.primary_text,
        )
        self._td_switch.pack(side="right")
        self._switches.append(self._td_switch)

        self._td_avail_lbl = ctk.CTkLabel(
            td_card, text="",
            font=ctk.CTkFont(size=11), text_color=T.text3, anchor="w",
        )
        self._td_avail_lbl.pack(fill="x", padx=16, pady=(2, 6))
        self._refresh_taildrop_avail_label()

        ctk.CTkFrame(td_card, fg_color=T.border, height=1).pack(
            fill="x", padx=16, pady=(0, 10)
        )

        # ── Send mode ────────────────────────────────────────────────────
        ctk.CTkLabel(
            td_card, text="📤  Chế độ gửi file",
            font=ctk.CTkFont(size=11, weight="bold"), text_color=T.text2, anchor="w",
        ).pack(fill="x", padx=16, pady=(0, 4))

        td_mode_row = ctk.CTkFrame(td_card, fg_color="transparent")
        td_mode_row.pack(fill="x", padx=16, pady=(0, 4))
        self._td_mode_auto_btn = ctk.CTkButton(
            td_mode_row, text="🔄  Tự động",
            height=30, corner_radius=8, font=ctk.CTkFont(size=11),
            command=lambda: self._on_taildrop_mode_change("always"),
        )
        self._td_mode_auto_btn.pack(side="left", padx=(0, 6))
        self._td_mode_manual_btn = ctk.CTkButton(
            td_mode_row, text="🖱️  Thủ công",
            height=30, corner_radius=8, font=ctk.CTkFont(size=11),
            command=lambda: self._on_taildrop_mode_change("ask"),
        )
        self._td_mode_manual_btn.pack(side="left")
        self._td_mode_desc_lbl = ctk.CTkLabel(
            td_card, text="",
            font=ctk.CTkFont(size=10), text_color=T.text3, anchor="w",
        )
        self._td_mode_desc_lbl.pack(fill="x", padx=16, pady=(0, 10))
        self._refresh_taildrop_mode_buttons()

        ctk.CTkFrame(td_card, fg_color=T.border, height=1).pack(
            fill="x", padx=16, pady=(0, 10)
        )

        # ── Target node row ──────────────────────────────────────────────
        ctk.CTkLabel(
            td_card, text="📱  Thiết bị đích (Tailscale node name hoặc IP)",
            font=ctk.CTkFont(size=11, weight="bold"), text_color=T.text2, anchor="w",
        ).pack(fill="x", padx=16, pady=(0, 4))

        td_node_row = ctk.CTkFrame(td_card, fg_color="transparent")
        td_node_row.pack(fill="x", padx=16, pady=(0, 4))
        self._td_node_entry = ctk.CTkEntry(
            td_node_row, placeholder_text="vd: iphone  hoặc  100.64.x.x",
            font=ctk.CTkFont(size=12), height=32,
        )
        self._td_node_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        saved_node = str(getattr(cfg, "taildrop_target_node", "") or "")
        if saved_node:
            self._td_node_entry.insert(0, saved_node)
        self._td_node_save_btn = ctk.CTkButton(
            td_node_row, text="💾 Lưu", width=70, height=32, corner_radius=6,
            fg_color=T.surface3, hover_color=T.border2, text_color=T.text2,
            font=ctk.CTkFont(size=11), command=self._on_taildrop_save_node,
        )
        self._td_node_save_btn.pack(side="right")

        # ── Scan peers ────────────────────────────────────────────────────
        td_action_row = ctk.CTkFrame(td_card, fg_color="transparent")
        td_action_row.pack(fill="x", padx=16, pady=(0, 14))
        self._td_scan_btn = ctk.CTkButton(
            td_action_row, text="🔍  Tìm thiết bị Tailscale",
            height=32, corner_radius=8,
            fg_color=T.surface3, hover_color=T.border2, text_color=T.text2,
            font=ctk.CTkFont(size=12), command=self._on_taildrop_scan,
        )
        self._td_scan_btn.pack(side="left")
        self._td_status_lbl = ctk.CTkLabel(
            td_action_row, text="",
            font=ctk.CTkFont(size=11), text_color=T.text2,
        )
        self._td_status_lbl.pack(side="left", padx=(10, 0))

    # ── Handlers ──────────────────────────────────────────────────────────

    def _refresh_taildrop_avail_label(self) -> None:
        """Show whether tailscale CLI is available on this PC."""
        lbl = getattr(self, "_td_avail_lbl", None)
        if lbl is None or not lbl.winfo_exists():
            return
        try:
            import shutil
            ok = shutil.which("tailscale") is not None
            if ok:
                lbl.configure(text="✅  tailscale CLI phát hiện trên PATH", text_color="#22c55e")
            else:
                lbl.configure(
                    text="⚠️  Không tìm thấy tailscale CLI — cài Tailscale trên PC này",
                    text_color=T.warning_text if hasattr(T, "warning_text") else T.text2,
                )
        except Exception:
            pass

    def _on_taildrop_toggle(self) -> None:
        enabled = self._td_switch_var.get()
        self._app.config.set("taildrop_enabled", enabled)
        self._app.config.save()
        state = "bật" if enabled else "tắt"
        self._app.toast(f"📲  Taildrop {state}.", "success" if enabled else "info")

    def _on_taildrop_mode_change(self, mode: str) -> None:
        self._app.config.set("taildrop_send_mode", mode)
        self._app.config.save()
        self._refresh_taildrop_mode_buttons()
        label = "Tự động — gửi ngay sau mỗi lần tải xong" if mode == "always" \
                else "Thủ công — chỉ gửi khi bạn yêu cầu"
        self._app.toast(f"📤  Chế độ Taildrop: {label}.", "success")

    def _refresh_taildrop_mode_buttons(self) -> None:
        """Update active/inactive visual on the 2 Send Mode buttons.
        Guards winfo_exists() — safe to call any time, including at build.
        """
        auto_btn = getattr(self, "_td_mode_auto_btn",   None)
        man_btn  = getattr(self, "_td_mode_manual_btn", None)
        desc_lbl = getattr(self, "_td_mode_desc_lbl",   None)
        if auto_btn is None or not auto_btn.winfo_exists():
            return
        mode         = str(getattr(self._app.config, "taildrop_send_mode", "ask") or "ask")
        active_fg,   active_txt   = T.primary,  T.primary_text
        inactive_fg, inactive_txt = T.surface3, T.text2
        if mode == "always":
            auto_btn.configure(fg_color=active_fg,   text_color=active_txt)
            man_btn.configure( fg_color=inactive_fg, text_color=inactive_txt)
            if desc_lbl and desc_lbl.winfo_exists():
                desc_lbl.configure(text="⚡ File sẽ tự động gửi sang iPhone ngay sau mỗi lần tải xong.")
        else:
            auto_btn.configure(fg_color=inactive_fg, text_color=inactive_txt)
            man_btn.configure( fg_color=active_fg,   text_color=active_txt)
            if desc_lbl and desc_lbl.winfo_exists():
                desc_lbl.configure(text="🖱️ File chỉ được gửi khi bạn nhấn Transfer thủ công trong Remote UI.")

    def _on_taildrop_save_node(self) -> None:
        node = self._td_node_entry.get().strip()
        if node and not _NODE_RE.match(node):
            self._app.toast(
                "❌  Tên node không hợp lệ — chỉ chứa chữ, số, dấu gạch ngang, dấu chấm.",
                "error",
            )
            return
        self._app.config.set("taildrop_target_node", node)
        self._app.config.save()
        if node:
            self._app.toast(f"💾  Đã lưu node: {node}", "success")
        else:
            self._app.toast("🗑  Đã xoá node đích.", "info")

    def _on_taildrop_scan(self) -> None:
        """Scan for online Tailscale peers and auto-fill the node entry."""
        lbl = self._td_status_lbl
        if not lbl.winfo_exists():
            return
        lbl.configure(text="⏳  Đang quét...", text_color=T.text2)
        self._td_scan_btn.configure(state="disabled")

        def _worker():
            try:
                from app.event_bus import bus as _global_bus
                from app.services.taildrop_service import TaildropService
                svc   = TaildropService(config=self._app.config, event_bus=_global_bus)
                nodes = svc.list_nodes()
                svc.close()
            except Exception as exc:
                nodes = []
                logger.warning("Taildrop scan error: %s", exc)

            def _update():
                try:
                    if not lbl.winfo_exists():
                        return
                    self._td_scan_btn.configure(state="normal")
                    if not nodes:
                        lbl.configure(
                            text=(
                                "⚠️  Không tìm thấy thiết bị nào khác online.\n"
                                "→ Mở app Tailscale trên iPhone và đảm bảo đang kết nối."
                            ),
                            text_color=T.warning_text if hasattr(T, "warning_text") else T.text2,
                        )
                        return
                    if len(nodes) == 1:
                        self._td_node_entry.delete(0, "end")
                        self._td_node_entry.insert(0, nodes[0])
                        lbl.configure(text=f"✅  1 peer: {nodes[0]} — đã điền tự động.", text_color="#22c55e")
                    else:
                        names = ", ".join(nodes[:5])
                        lbl.configure(
                            text=f"✅  {len(nodes)} peers: {names} — nhập tên vào ô trên.",
                            text_color="#22c55e",
                        )
                except Exception:
                    pass

            self.after(0, _update)

        threading.Thread(target=_worker, daemon=True, name="omnidl-td-scan").start()

    # ── Theme refresh ─────────────────────────────────────────────────────

    def _on_theme(self) -> None:
        if not self.winfo_exists():
            return
        w = getattr(self, "_card_taildrop", None)
        if w and w.winfo_exists():
            w.configure(fg_color=T.surface, border_color=T.border)
        for lbl in self._section_labels:
            if lbl.winfo_exists(): lbl.configure(text_color=T.text3)
        for sw in self._switches:
            if sw.winfo_exists(): sw.configure(progress_color=T.primary)
        for attr in ("_td_node_save_btn", "_td_scan_btn"):
            w = getattr(self, attr, None)
            if w and w.winfo_exists():
                w.configure(fg_color=T.surface3, hover_color=T.border2, text_color=T.text2)
        self._refresh_taildrop_mode_buttons()
