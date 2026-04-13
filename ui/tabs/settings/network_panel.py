"""
ui/tabs/settings/network_panel.py
Settings panel: Network & Authentication · Global Cookie · Per-Platform Cookies.

Dependencies on MainWindow:
  self._app.config   – proxy, use_cookies, cookies_browser, cookie_file,
                       platform_cookies, config_path, set_cookie_for_platform
  self._app.toast    – feedback toasts
"""
from __future__ import annotations

import threading
from pathlib import Path
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


def _is_netscape_cookie_file(path: "Path") -> bool:
    """Return True if *path* looks like a Netscape cookie file.

    Reads only the first line — fast and avoids loading large files.
    False on any I/O error (caller will reject the file gracefully).
    """
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            first = fh.readline()
        return "Netscape HTTP Cookie File" in first
    except OSError:
        return False


def _cookie_file_candidates(path_str: str) -> "list[Path]":
    """Return both the stored path and its .txt/.enc counterpart.

    Config may store a .txt path while only the .enc exists (and vice-versa)
    because encrypt_cookie_file renames .txt → .enc after config is saved
    (BUG BE auto-fallback).  We try both variants so deletion is complete.
    """
    p = Path(path_str)
    candidates = [p]
    if p.suffix == ".txt":
        candidates.append(p.with_suffix(".enc"))
    elif p.suffix == ".enc":
        candidates.append(p.with_suffix(".txt"))
    return candidates


# Platforms with dedicated per-platform cookie rows
_PC_PLATFORMS = [
    ("youtube",   "YouTube"),
    ("tiktok",    "TikTok"),
    ("instagram", "Instagram"),
    ("facebook",  "Facebook"),
    ("twitter",   "Twitter / X"),
    ("threads",   "Threads"),
]


class NetworkPanel(_BasePanel):
    """
    Renders:
      🔒 Network & Authentication   (proxy, browser, global cookie)
      🍪 Per-Platform Cookies

    Background workers:
      _extract_global_cookies()   – yt-dlp browser extraction (daemon thread)
      _extract_global_cdp()       – CDP extraction (daemon thread)
      _extract_platform_cookie()  – per-platform yt-dlp (daemon thread)
      _extract_platform_cdp()     – per-platform CDP (daemon thread)
    All worker → UI updates go through self._ui_queue.
    """

    def __init__(self, master, app: "MainWindow") -> None:
        super().__init__(master, app)
        self._build()

    # ── Build ─────────────────────────────────────────────────────────────

    def _build(self) -> None:
        p   = self
        cfg = self._app.config

        # -- Network & Auth ------------------------------------------------
        self._section(p, "🔒   NETWORK & AUTHENTICATION")
        self._card_net = net = self._card(p)

        proxy_row = ctk.CTkFrame(net, fg_color="transparent")
        proxy_row.pack(fill="x", padx=16, pady=(12, 4))
        ctk.CTkLabel(proxy_row, text="Proxy URL",
                     font=ctk.CTkFont(size=12), text_color=T.text2).pack(side="left")
        self._proxy_entry = ctk.CTkEntry(
            proxy_row, width=260, height=32, corner_radius=8,
            fg_color=T.input, border_color=T.border2,
            placeholder_text="http://host:port")
        self._proxy_entry.insert(0, cfg.proxy)
        self._proxy_entry.pack(side="right")
        self._proxy_entry.bind("<FocusOut>", self._on_proxy_focusout)

        self._cookies_var = ctk.BooleanVar(value=cfg.use_cookies)
        self._switch_row(net, "Use browser cookies", self._cookies_var,
                         lambda v: cfg.set("use_cookies", v))

        browser_row = ctk.CTkFrame(net, fg_color="transparent")
        browser_row.pack(fill="x", padx=16, pady=(4, 4))
        ctk.CTkLabel(browser_row, text="Cookie source browser",
                     font=ctk.CTkFont(size=12), text_color=T.text2).pack(side="left")
        self._browser_var = ctk.StringVar(value=cfg.cookies_browser)
        self._browser_om = ctk.CTkOptionMenu(
            browser_row,
            variable=self._browser_var,
            values=["chrome", "firefox", "safari", "edge", "opera", "brave"],
            command=lambda v: cfg.set("cookies_browser", v),
            width=130, corner_radius=8,
        )
        self._browser_om.pack(side="right")

        # Cookie fallback section label + warning
        ctk.CTkLabel(
            net,
            text=(
                "🌐  Cookie fallback — cho YouTube, Twitch, Vimeo...  "
                "(dùng khi nền tảng chưa có trong bảng Per-Platform bên dưới)"
            ),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=T.text2, anchor="w",
        ).pack(anchor="w", padx=16, pady=(12, 2))

        ctk.CTkLabel(
            net,
            text=(
                "⚠  File này chứa toàn bộ cookies của trình duyệt (Google, email, banking...).\n"
                "   Ưu tiên dùng bảng Per-Platform bên dưới để bảo mật hơn."
            ),
            font=ctk.CTkFont(size=11), text_color=T.warning_text,
            anchor="w", justify="left", wraplength=480,
        ).pack(anchor="w", padx=16, pady=(0, 6))

        extract_row = ctk.CTkFrame(net, fg_color="transparent")
        extract_row.pack(fill="x", padx=16, pady=(0, 4))
        self._extract_global_btn = ctk.CTkButton(
            extract_row, text="🔄  Firefox / Edge / Opera",
            height=30, corner_radius=8,
            fg_color=T.surface3, hover_color=T.border2,
            text_color=T.text2, font=ctk.CTkFont(size=12),
            command=self._extract_global_cookies,
        )
        self._extract_global_btn.pack(side="left")

        self._extract_cdp_btn = ctk.CTkButton(
            extract_row, text="🦁  Brave / Chrome 127+",
            height=30, corner_radius=8,
            fg_color=T.surface3, hover_color=T.border2,
            text_color=T.text2, font=ctk.CTkFont(size=12),
            command=self._extract_global_cdp,
        )
        self._extract_cdp_btn.pack(side="left", padx=(8, 0))
        self._extract_global_status = ctk.CTkLabel(
            extract_row, text="",
            font=ctk.CTkFont(size=11), text_color=T.text2,
        )
        self._extract_global_status.pack(side="left", padx=(10, 0))

        self._extract_global_hint = ctk.CTkLabel(
            net,
            text=(
                "🔄 = yt-dlp đọc trực tiếp (cần đóng Brave/Chrome trước).  "
                "🦁 = CDP — không cần đóng trình duyệt, Brave 127+ an toàn."
            ),
            font=ctk.CTkFont(size=11), text_color=T.text3,
            anchor="w", wraplength=500,
        )
        self._extract_global_hint.pack(anchor="w", padx=16, pady=(0, 4))

        ctk.CTkLabel(
            net, text="📁  Hoặc import file .txt thủ công:",
            font=ctk.CTkFont(size=11), text_color=T.text3,
        ).pack(anchor="w", padx=16, pady=(6, 2))

        cf_row = ctk.CTkFrame(net, fg_color="transparent")
        cf_row.pack(fill="x", padx=16, pady=(0, 14))
        self._cf_lbl = ctk.CTkLabel(
            cf_row, text=self._short_cookie_path(cfg.cookie_file),
            font=ctk.CTkFont(size=11), text_color=T.primary_text, anchor="w")
        self._cf_lbl.pack(side="left", fill="x", expand=True)
        self._browse_cf_btn = ctk.CTkButton(
            cf_row, text="Browse…", width=80, height=28, corner_radius=6,
            fg_color=T.surface3, hover_color=T.border2,
            text_color=T.text2, font=ctk.CTkFont(size=11),
            command=self._browse_cookie_file,
        )
        self._browse_cf_btn.pack(side="left", padx=(8, 4))
        self._clear_cf_btn = ctk.CTkButton(
            cf_row, text="🗑 Clear", width=64, height=28, corner_radius=6,
            fg_color=T.error_bg, hover_color=T.error_bg,
            text_color=T.error, font=ctk.CTkFont(size=11),
            command=self._clear_cookie_file,
        )
        self._clear_cf_btn.pack(side="left")

        # -- Per-platform cookies ------------------------------------------
        self._section(p, "🍪   PER-PLATFORM COOKIES  ✅ Khuyến nghị — bảo mật hơn")
        self._card_cookies = pc_card = self._card(p)

        ctk.CTkLabel(
            pc_card,
            text=(
                "✅ Ưu tiên dùng bảng này — mỗi file chỉ chứa cookies của đúng nền tảng đó.\n"
                "File TikTok không có cookies Google/email, file Instagram không có cookies banking.\n"
                "Nếu nền tảng có hàng riêng ở đây → KHÔNG cần dùng Cookie fallback bên trên."
            ),
            font=ctk.CTkFont(size=11), text_color=T.text3,
            justify="left", wraplength=480,
        ).pack(anchor="w", padx=16, pady=(10, 6))

        self._pc_lbls:         list = []
        self._pc_browse_btns:  list = []
        self._pc_clear_btns:   list = []
        self._pc_extract_btns: list = []

        for key, label in _PC_PLATFORMS:
            row = ctk.CTkFrame(pc_card, fg_color="transparent")
            row.pack(fill="x", padx=16, pady=(0, 6))
            ctk.CTkLabel(
                row, text=label, width=100,
                font=ctk.CTkFont(size=12), text_color=T.text2, anchor="w",
            ).pack(side="left")
            path_lbl = ctk.CTkLabel(
                row, text=self._short_cookie_path(cfg.get_cookie_for_platform(key)),
                font=ctk.CTkFont(size=11), text_color=T.primary_text, anchor="w",
            )
            path_lbl.pack(side="left", fill="x", expand=True, padx=(4, 0))
            self._pc_lbls.append(path_lbl)

            clear_btn = ctk.CTkButton(
                row, text="🗑", width=32, height=28, corner_radius=6,
                fg_color=T.error_bg, hover_color=T.error_bg,
                text_color=T.error, font=ctk.CTkFont(size=11),
                command=lambda k=key, lbl=path_lbl: self._clear_platform_cookie(k, lbl),
            )
            clear_btn.pack(side="right", padx=(4, 0))
            self._pc_clear_btns.append(clear_btn)

            browse_btn = ctk.CTkButton(
                row, text="📂", width=40, height=28, corner_radius=6,
                fg_color=T.surface3, hover_color=T.border2,
                text_color=T.text2, font=ctk.CTkFont(size=11),
                command=lambda k=key, lbl=path_lbl: self._browse_platform_cookie(k, lbl),
            )
            browse_btn.pack(side="right", padx=(4, 0))
            self._pc_browse_btns.append(browse_btn)

            extract_btn = ctk.CTkButton(
                row, text="🔄", width=40, height=28, corner_radius=6,
                fg_color=T.surface3, hover_color=T.border2,
                text_color=T.text2, font=ctk.CTkFont(size=11),
                command=lambda k=key, lbl=path_lbl: self._extract_platform_cookie(k, lbl),
            )
            extract_btn.pack(side="right", padx=(4, 0))
            self._pc_extract_btns.append(extract_btn)

            cdp_btn = ctk.CTkButton(
                row, text="🦁", width=40, height=28, corner_radius=6,
                fg_color=T.surface3, hover_color=T.border2,
                text_color=T.text2, font=ctk.CTkFont(size=11),
                command=lambda k=key, lbl=path_lbl: self._extract_platform_cdp(k, lbl),
            )
            cdp_btn.pack(side="right", padx=(4, 0))
            self._pc_extract_btns.append(cdp_btn)

        ctk.CTkFrame(pc_card, fg_color=T.border, height=1).pack(
            fill="x", padx=16, pady=(6, 6)
        )
        self._pc_extract_status = ctk.CTkLabel(
            pc_card, text="",
            font=ctk.CTkFont(size=11), text_color=T.text2, anchor="w",
        )
        self._pc_extract_status.pack(anchor="w", padx=16, pady=(0, 4))
        ctk.CTkLabel(
            pc_card,
            text="🔄 = yt-dlp (Firefox/Opera).  🦁 = CDP (Brave/Chrome 127+, không cần đóng trình duyệt).",
            font=ctk.CTkFont(size=11), text_color=T.text3, justify="left",
        ).pack(anchor="w", padx=16, pady=(0, 10))

    # ── Handlers — Network ────────────────────────────────────────────────

    def _on_proxy_focusout(self, _event=None) -> None:
        """Validate proxy URL on FocusOut before saving to config (CWE-20)."""
        val = self._proxy_entry.get().strip()
        if not val:
            self._app.config.set("proxy", "")
            return
        _valid_schemes = ("http://", "https://", "socks4://", "socks5://")
        if any(val.lower().startswith(s) for s in _valid_schemes):
            self._app.config.set("proxy", val)
            self._proxy_entry.configure(border_color=T.border2)
        else:
            self._proxy_entry.configure(border_color=T.error)
            self.after(1500, lambda: self._proxy_entry.configure(border_color=T.border2))
            self._app.toast(
                "Proxy không hợp lệ — phải bắt đầu bằng http://, https://, socks4://, hoặc socks5://",
                "error",
            )

    # ── Handlers — Global Cookie ──────────────────────────────────────────

    def _browse_cookie_file(self) -> None:
        import shutil
        import tkinter.filedialog as fd

        from infrastructure.downloader.cookie_storage import encrypt_cookie_file
        chosen = fd.askopenfilename(
            title="Select cookies.txt (Netscape format)",
            filetypes=[("Cookie files", "*.txt"), ("All files", "*.*")])
        if not chosen:
            return
        src = Path(chosen)
        if not src.is_file():
            self._app.toast("File not found.", "error")
            return
        if not _is_netscape_cookie_file(src):
            self._app.toast(
                "File không phải định dạng Netscape cookie.\n"
                "Hãy chọn file cookies.txt được export từ trình duyệt hoặc tiện ích Cookie-Editor.",
                "error",
            )
            return
        # Capture BEFORE writing so we can clean up the old file afterward.
        old_path_str = self._app.config.get("cookie_file", "")
        safe_dir = self._app.config.config_path.parent / "cookies"
        safe_dir.mkdir(parents=True, exist_ok=True)
        dest = safe_dir / src.name
        try:
            shutil.copy2(src, dest)
        except OSError as exc:
            logger.warning("Failed to copy cookie file: %s", exc)
            self._app.toast(f"Cannot copy cookie file: {exc}", "error")
            return
        # BUG BM: encrypt immediately after copy — plaintext MUST NOT persist
        # on disk. encrypt_cookie_file() renames .txt → .enc atomically (DPAPI
        # on Windows, Fernet+Keychain on macOS, chmod 0o600 fallback on Linux).
        dest = encrypt_cookie_file(dest)
        self._app.config.set("cookie_file", str(dest))
        self._cf_lbl.configure(text=self._short_cookie_path(str(dest)))
        self._app.toast("Cookie file đã được mã hóa và lưu vào thư mục an toàn.", "info")
        # Delete the file that was active before this browse replaced it.
        # Different acquisition methods produce different filenames, so the old
        # file is never overwritten — it must be explicitly removed.
        self._delete_old_cookie_if_replaced(old_path_str, dest)

    def _clear_cookie_file(self) -> None:
        # BUG BN: read the stored path BEFORE clearing config so we can delete
        # the file on disk.  Clearing only the config entry leaves an orphaned
        # .enc file — a data-minimisation violation because session credentials
        # remain on disk after the user explicitly asked to remove them.
        old_path_str = self._app.config.get("cookie_file", "")

        # Always clear config first (fast, must always happen).
        self._app.config.set("cookie_file", "")
        self._cf_lbl.configure(text="No file selected")

        # Delete the physical file — CWE-22: only allow paths inside safe_dir.
        if old_path_str:
            safe_dir = self._app.config.config_path.parent.resolve()
            for candidate in _cookie_file_candidates(old_path_str):
                try:
                    resolved = candidate.resolve()
                    if safe_dir not in resolved.parents and resolved != safe_dir:
                        logger.warning(
                            "_clear_cookie_file: rejected path outside safe dir: %s",
                            candidate,
                        )
                        continue
                    if resolved.is_file():
                        resolved.unlink()
                        logger.info(
                            "Deleted global cookie file on clear: %s", resolved.name
                        )
                except OSError as exc:
                    logger.warning(
                        "_clear_cookie_file: could not delete %s — %s", candidate, exc
                    )

    def _extract_global_cdp(self) -> None:
        """Extract all cookies via CDP (Brave/Chrome 127+ App-Bound safe)."""
        import tkinter.messagebox as mb
        confirmed = mb.askyesno(
            "OmniDL — Xác nhận lấy toàn bộ cookies (CDP)",
            (
                "⚠ Thao tác này lấy TẤT CẢ cookies của Brave/Chrome,\n"
                "bao gồm cả Google, email, banking...\n\n"
                "Cookies sẽ được mã hóa DPAPI và chỉ lưu trên máy này.\n"
                "Một port ngẫu nhiên trên localhost sẽ được mở trong ~10 giây.\n\n"
                "➡ Khuyến nghị: Dùng nút 🦁 ở từng platform bên dưới\n"
                "   để chỉ lấy đúng cookies cần thiết (an toàn hơn).\n\n"
                "Tiếp tục lấy toàn bộ?"
            ),
            icon="warning",
        )
        if not confirmed:
            return
        browser = self._browser_var.get()
        if browser not in ("brave", "chrome", "chromium", "edge"):
            self._app.toast(
                f"CDP chỉ hỗ trợ Brave/Chrome/Edge. Trình duyệt hiện tại: {browser}.\n"
                "Dùng nút 🔄 cho Firefox/Opera/Safari.",
                "error",
            )
            return
        safe_dir    = self._app.config.config_path.parent / "cookies"
        output_path = safe_dir / f"{browser}_cdp_cookies.txt"
        btn    = self._extract_cdp_btn
        status = self._extract_global_status
        # Capture before thread starts (UI thread reads config safely).
        old_path_str = self._app.config.get("cookie_file", "")

        def _worker() -> None:
            try:
                from infrastructure.downloader.cookie_extractor import extract_via_cdp
                count, error = extract_via_cdp(output_path, platform_key=None, browser=browser)
            except Exception as exc:
                err_msg = str(exc)
                self._ui_queue.put(lambda e=err_msg: (
                    status.configure(text=f"❌ {e.splitlines()[0][:70]}", text_color=T.error),
                    self._app.toast(f"CDP thất bại: {e.splitlines()[0][:60]}", "error"),
                ))
                self._ui_queue.put(lambda: btn.configure(state="normal"))
                return
            else:
                path_str = self._resolve_saved_cookie_path(output_path)
                self._app.config.set("cookie_file", path_str)
                # Delete whichever file was active before this extraction
                # replaced it — different methods produce different filenames
                # so the old file is never overwritten in place.
                self._delete_old_cookie_if_replaced(old_path_str, Path(path_str))
                self._ui_queue.put(lambda c=count, ps=path_str: (
                    self._cf_lbl.configure(text=self._short_cookie_path(ps)),
                    status.configure(text=f"✓ {c} cookies đã lưu (CDP)", text_color=T.success),
                    self._app.toast(f"CDP: đã lấy {c} cookies từ {browser.title()}.", "success"),
                ))
                self._ui_queue.put(lambda: self.after(
                    6000,
                    lambda: status.configure(text="", text_color=T.text2)
                    if status.winfo_exists() else None,
                ))
            self._ui_queue.put(lambda: btn.configure(state="normal"))

        btn.configure(state="disabled")
        status.configure(text=f"Đang khởi động {browser.title()} (CDP)…", text_color=T.text2)
        threading.Thread(target=_worker, daemon=True, name="omnidl-cdp-extract").start()

    def _extract_global_cookies(self) -> None:
        """Extract all cookies from the selected browser (global cookie fallback).
        Runs in a daemon thread — all UI updates via _ui_queue (Python 3.14 safe).
        """
        import tkinter.messagebox as mb
        confirmed = mb.askyesno(
            "OmniDL — Xác nhận lấy toàn bộ cookies",
            (
                "⚠ Thao tác này lấy TẤT CẢ cookies của trình duyệt,\n"
                "bao gồm cả Google, email, banking...\n\n"
                "Cookies sẽ được mã hóa DPAPI và chỉ lưu trên máy này.\n\n"
                "➡ Khuyến nghị: Dùng nút 🔄 / 🦁 ở từng platform bên dưới\n"
                "   để chỉ lấy đúng cookies cần thiết (an toàn hơn).\n\n"
                "Tiếp tục lấy toàn bộ?"
            ),
            icon="warning",
        )
        if not confirmed:
            return
        browser     = self._browser_var.get()
        safe_dir    = self._app.config.config_path.parent / "cookies"
        output_path = safe_dir / f"{browser}_global_cookies.txt"
        btn    = self._extract_global_btn
        status = self._extract_global_status
        # Capture before thread starts (UI thread reads config safely).
        old_path_str = self._app.config.get("cookie_file", "")

        def _worker() -> None:
            try:
                from infrastructure.downloader.cookie_extractor import extract_browser_cookies
                count, error = extract_browser_cookies(browser, output_path, platform_key=None)
            except Exception as exc:
                error = str(exc)
                count = 0
            if error:
                self._ui_queue.put(lambda e=error: (
                    status.configure(text=f"❌ {e.splitlines()[0][:70]}", text_color=T.error),
                    self._app.toast(f"Lấy cookies thất bại: {e.splitlines()[0][:60]}", "error"),
                ))
            else:
                enc_candidate = output_path.parent / (output_path.stem + ".enc")
                final_path    = enc_candidate if enc_candidate.exists() else output_path
                path_str      = str(final_path)
                self._app.config.set("cookie_file", path_str)
                # Delete whichever file was active before this extraction replaced it.
                self._delete_old_cookie_if_replaced(old_path_str, final_path)
                enc_note = " 🔒 (mã hóa DPAPI)" if path_str.endswith(".enc") else ""
                self._ui_queue.put(lambda c=count, ps=path_str, n=enc_note: (
                    self._cf_lbl.configure(text=self._short_cookie_path(ps)),
                    status.configure(text=f"✓ {c} cookies đã lưu{n}", text_color=T.success),
                    self._app.toast(f"Đã lấy {c} cookies từ {browser}{n}.", "success"),
                ))
                self._ui_queue.put(lambda: self.after(
                    6000,
                    lambda: status.configure(text="", text_color=T.text2)
                    if status.winfo_exists() else None,
                ))
            self._ui_queue.put(lambda: btn.configure(state="normal"))

        btn.configure(state="disabled")
        status.configure(text=f"Đang đọc cookies từ {browser}…", text_color=T.text2)
        threading.Thread(target=_worker, daemon=True, name="omnidl-cookie-extract").start()

    # ── Handlers — Per-Platform Cookies ───────────────────────────────────

    def _browse_platform_cookie(self, platform_key: str, path_lbl: "ctk.CTkLabel") -> None:
        import shutil
        import tkinter.filedialog as fd
        platform_name = {
            "tiktok": "TikTok", "instagram": "Instagram",
            "facebook": "Facebook", "twitter": "Twitter/X", "threads": "Threads",
        }.get(platform_key, platform_key.title())
        chosen = fd.askopenfilename(
            title=f"Chọn cookie file cho {platform_name} (Netscape format)",
            filetypes=[("Cookie files", "*.txt"), ("All files", "*.*")],
        )
        if not chosen:
            return
        src_path = Path(chosen)
        if not src_path.is_file():
            self._app.toast("File không tìm thấy.", "error")
            return
        if not _is_netscape_cookie_file(src_path):
            self._app.toast(
                f"File không phải định dạng Netscape cookie.\n"
                f"Hãy chọn file cookies.txt được export từ trình duyệt hoặc tiện ích Cookie-Editor.",
                "error",
            )
            return
        # Capture BEFORE writing so we can clean up the old file afterward.
        old_path_str = self._app.config.get_cookie_for_platform(platform_key)
        safe_dir  = self._app.config.config_path.parent / "cookies"
        safe_dir.mkdir(parents=True, exist_ok=True)
        dest_name = f"{platform_key}_{src_path.name}"
        dest      = safe_dir / dest_name
        try:
            shutil.copy2(src_path, dest)
        except OSError as exc:
            logger.warning("Failed to copy platform cookie file: %s", exc)
            self._app.toast(f"Không thể sao chép cookie file: {exc}", "error")
            return
        # BUG BM: encrypt immediately after copy (same as global cookie path).
        from infrastructure.downloader.cookie_storage import encrypt_cookie_file
        dest = encrypt_cookie_file(dest)
        self._app.config.set_cookie_for_platform(platform_key, str(dest))
        path_lbl.configure(text=self._short_cookie_path(str(dest)))
        self._app.toast(f"Cookie {platform_name} đã được mã hóa và lưu vào thư mục an toàn.", "info")
        # Delete the file that was active before this browse replaced it.
        self._delete_old_cookie_if_replaced(old_path_str, dest)

    def _extract_platform_cookie(self, platform_key: str, path_lbl: "ctk.CTkLabel") -> None:
        """Extract per-platform cookies from browser (yt-dlp path).
        Daemon thread; all UI via _ui_queue.
        """
        browser       = self._browser_var.get()
        platform_name = {
            "tiktok": "TikTok", "instagram": "Instagram",
            "facebook": "Facebook", "twitter": "Twitter/X", "threads": "Threads",
        }.get(platform_key, platform_key.title())
        safe_dir    = self._app.config.config_path.parent / "cookies"
        output_path = safe_dir / f"{platform_key}_{browser}_cookies.txt"
        status      = self._pc_extract_status
        # Capture before thread starts (UI thread reads config safely).
        old_path_str = self._app.config.get_cookie_for_platform(platform_key)
        for btn in self._pc_extract_btns:
            if btn.winfo_exists():
                btn.configure(state="disabled")

        def _worker() -> None:
            try:
                from infrastructure.downloader.cookie_extractor import extract_browser_cookies
                count, error = extract_browser_cookies(browser, output_path, platform_key=platform_key)
            except Exception as exc:
                error = str(exc)
                count = 0
            if error:
                self._ui_queue.put(lambda e=error: (
                    status.configure(
                        text=f"❌ {platform_name}: {e.splitlines()[0][:65]}",
                        text_color=T.error,
                    ),
                    self._app.toast(
                        f"Lấy cookies {platform_name} thất bại: {e.splitlines()[0][:55]}",
                        "error",
                    ),
                ))
            else:
                path_str = self._resolve_saved_cookie_path(output_path)
                self._app.config.set_cookie_for_platform(platform_key, path_str)
                # Delete whichever file was active before this extraction replaced it.
                self._delete_old_cookie_if_replaced(old_path_str, Path(path_str))
                self._ui_queue.put(lambda c=count, ps=path_str, pn=platform_name: (
                    path_lbl.configure(text=self._short_cookie_path(ps)),
                    status.configure(text=f"✓ {pn}: {c} cookies đã lưu", text_color=T.success),
                    self._app.toast(f"Đã lấy {c} cookies {pn} từ {browser}.", "success"),
                ))
                self._ui_queue.put(lambda: self.after(
                    6000,
                    lambda: status.configure(text="", text_color=T.text2)
                    if status.winfo_exists() else None,
                ))
            self._ui_queue.put(lambda: [
                btn.configure(state="normal")
                for btn in self._pc_extract_btns if btn.winfo_exists()
            ])

        status.configure(text=f"Đang đọc cookies {platform_name} từ {browser}…", text_color=T.text2)
        threading.Thread(
            target=_worker, daemon=True,
            name=f"omnidl-cookie-extract-{platform_key}",
        ).start()

    def _extract_platform_cdp(self, platform_key: str, path_lbl: "ctk.CTkLabel") -> None:
        """Extract per-platform cookies via CDP (Brave/Chrome 127+ safe)."""
        browser = self._browser_var.get()
        if browser not in ("brave", "chrome", "chromium", "edge"):
            self._app.toast(f"CDP chỉ hỗ trợ Brave/Chrome/Edge. Dùng 🔄 cho {browser}.", "error")
            return
        platform_name = {
            "tiktok": "TikTok", "instagram": "Instagram",
            "facebook": "Facebook", "twitter": "Twitter/X", "threads": "Threads",
        }.get(platform_key, platform_key.title())
        safe_dir    = self._app.config.config_path.parent / "cookies"
        output_path = safe_dir / f"{platform_key}_{browser}_cdp_cookies.txt"
        status      = self._pc_extract_status
        # Capture before thread starts (UI thread reads config safely).
        old_path_str = self._app.config.get_cookie_for_platform(platform_key)
        for btn in self._pc_extract_btns:
            if btn.winfo_exists():
                btn.configure(state="disabled")

        def _worker() -> None:
            try:
                from infrastructure.downloader.cookie_extractor import extract_via_cdp
                count, error = extract_via_cdp(output_path, platform_key=platform_key, browser=browser)
            except Exception as exc:
                err_msg = str(exc)
                self._ui_queue.put(lambda e=err_msg, pn=platform_name: (
                    status.configure(text=f"❌ {pn} CDP: {e.splitlines()[0][:60]}", text_color=T.error),
                    self._app.toast(f"CDP {pn} thất bại: {e.splitlines()[0][:50]}", "error"),
                ))
                self._ui_queue.put(lambda: [
                    btn.configure(state="normal")
                    for btn in self._pc_extract_btns if btn.winfo_exists()
                ])
                return
            else:
                path_str = self._resolve_saved_cookie_path(output_path)
                self._app.config.set_cookie_for_platform(platform_key, path_str)
                # Delete whichever file was active before this extraction replaced it.
                self._delete_old_cookie_if_replaced(old_path_str, Path(path_str))
                self._ui_queue.put(lambda c=count, ps=path_str, pn=platform_name: (
                    path_lbl.configure(text=self._short_cookie_path(ps)),
                    status.configure(text=f"✓ {pn}: {c} cookies (CDP)", text_color=T.success),
                    self._app.toast(f"CDP: đã lấy {c} cookies {pn}.", "success"),
                ))
                self._ui_queue.put(lambda: self.after(
                    6000,
                    lambda: status.configure(text="", text_color=T.text2)
                    if status.winfo_exists() else None,
                ))
            self._ui_queue.put(lambda: [
                btn.configure(state="normal")
                for btn in self._pc_extract_btns if btn.winfo_exists()
            ])

        status.configure(
            text=f"Đang khởi động {browser.title()} để lấy cookies {platform_name}…",
            text_color=T.text2,
        )
        threading.Thread(
            target=_worker, daemon=True,
            name=f"omnidl-cdp-extract-{platform_key}",
        ).start()

    def _delete_old_cookie_if_replaced(
        self, old_path_str: str, new_path: Path
    ) -> None:
        """Delete the previously-stored cookie file when it is superseded by a
        file written via a *different* acquisition method (browse vs yt-dlp vs CDP).

        Each method produces a distinct filename, so switching methods orphans the
        old file on disk — a data-minimisation violation because session credentials
        persist even though the user never explicitly kept them.

        Rules:
        • No-op when old_path_str is empty (no prior cookie).
        • No-op when old and new paths resolve to the SAME file (same-method
          re-extraction that overwrites in place — nothing to clean up).
        • CWE-22 guard: only delete files inside config_path.parent (safe dir).
        • Deletes both .txt and .enc candidates via _cookie_file_candidates() so
          both halves of a partial BUG-BE pair are removed.
        • Safe to call from ANY thread — does only file I/O and logging,
          never touches UI widgets.
        """
        if not old_path_str:
            return
        safe_dir = self._app.config.config_path.parent.resolve()
        try:
            new_resolved = new_path.resolve()
        except OSError:
            new_resolved = new_path  # best-effort fallback

        for candidate in _cookie_file_candidates(old_path_str):
            try:
                resolved = candidate.resolve()
                if resolved == new_resolved:
                    continue  # same file just re-encrypted — do NOT delete
                if safe_dir not in resolved.parents and resolved != safe_dir:
                    logger.warning(
                        "_delete_old_cookie_if_replaced: rejected path outside "
                        "safe dir: %s",
                        candidate,
                    )
                    continue
                if resolved.is_file():
                    resolved.unlink()
                    logger.info(
                        "Deleted replaced cookie file: %s", resolved.name
                    )
            except OSError as exc:
                logger.warning(
                    "_delete_old_cookie_if_replaced: could not delete %s — %s",
                    candidate, exc,
                )

    def _clear_platform_cookie(self, platform_key: str, path_lbl: "ctk.CTkLabel") -> None:
        # Read the stored path BEFORE clearing config, so we can delete the
        # file on disk.  Clearing only the config entry leaves an orphaned
        # .enc file that persists indefinitely — a data-minimisation violation
        # because session credentials remain on disk after the user explicitly
        # asked to delete them.
        old_path_str = self._app.config.get_cookie_for_platform(platform_key)

        # Clear config first (fast, must always happen)
        self._app.config.set_cookie_for_platform(platform_key, "")
        path_lbl.configure(text="No file selected")

        # Delete the physical file — CWE-22: only allow paths inside safe_dir
        if old_path_str:
            safe_dir = self._app.config.config_path.parent.resolve()
            for candidate in _cookie_file_candidates(old_path_str):
                try:
                    resolved = candidate.resolve()
                    if safe_dir not in resolved.parents and resolved != safe_dir:
                        logger.warning(
                            "_clear_platform_cookie: rejected path outside safe dir: %s",
                            candidate,
                        )
                        continue
                    if resolved.is_file():
                        resolved.unlink()
                        logger.info(
                            "Deleted platform cookie file on clear: %s", resolved.name
                        )
                except OSError as exc:
                    logger.warning(
                        "_clear_platform_cookie: could not delete %s — %s", candidate, exc
                    )

    # ── Static helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _short_cookie_path(path: str) -> str:
        if not path:
            return "No file selected"
        s = str(Path(path))
        return s if len(s) <= 45 else f"…{s[-42:]}"

    @staticmethod
    def _resolve_saved_cookie_path(output_path: Path) -> str:
        """Return the actual path to save in config after extraction.
        Prefers .enc (encrypted) over .txt when both may exist.
        Prevents CWE-22 rejection caused by saving a .txt path when only .enc exists.
        """
        enc = output_path.with_suffix(".enc")
        if enc.exists():
            return str(enc)
        return str(output_path)

    # ── Theme refresh ─────────────────────────────────────────────────────

    def _on_theme(self) -> None:
        if not self.winfo_exists():
            return
        for attr in ("_card_net", "_card_cookies"):
            card = getattr(self, attr, None)
            if card and card.winfo_exists():
                card.configure(fg_color=T.surface, border_color=T.border)
        for lbl in self._section_labels:
            if lbl.winfo_exists():
                lbl.configure(text_color=T.text3)
        for lbl in self._row_labels:
            if lbl.winfo_exists():
                lbl.configure(text_color=T.text2)
        for sw in self._switches:
            if sw.winfo_exists():
                sw.configure(progress_color=T.primary)
        for attr in ("_cf_lbl",):
            w = getattr(self, attr, None)
            if w and w.winfo_exists():
                w.configure(text_color=T.primary_text)
        for lbl in self._pc_lbls:
            if lbl.winfo_exists():
                lbl.configure(text_color=T.primary_text)
        for btn in self._pc_extract_btns:
            if btn.winfo_exists():
                btn.configure(fg_color=T.surface3, hover_color=T.border2, text_color=T.text2)
        for btn in self._pc_browse_btns:
            if btn.winfo_exists():
                btn.configure(fg_color=T.surface3, hover_color=T.border2, text_color=T.text2)
        for btn in self._pc_clear_btns:
            if btn.winfo_exists():
                btn.configure(fg_color=T.error_bg, hover_color=T.error_bg, text_color=T.error)
        for attr in ("_extract_global_btn", "_extract_cdp_btn", "_browse_cf_btn"):
            w = getattr(self, attr, None)
            if w and w.winfo_exists():
                w.configure(fg_color=T.surface3, hover_color=T.border2, text_color=T.text2)
        w = getattr(self, "_clear_cf_btn", None)
        if w and w.winfo_exists():
            w.configure(fg_color=T.error_bg, hover_color=T.error_bg, text_color=T.error)
        w = getattr(self, "_extract_global_hint", None)
        if w and w.winfo_exists():
            w.configure(text_color=T.warning_text)
        for attr in ("_extract_global_status", "_pc_extract_status"):
            w = getattr(self, attr, None)
            if w and w.winfo_exists():
                w.configure(text_color=T.text2)
        w = getattr(self, "_browser_om", None)
        if w and w.winfo_exists():
            w.configure(fg_color=T.surface3, button_color=T.border2, text_color=T.text2)
        w = getattr(self, "_proxy_entry", None)
        if w and w.winfo_exists():
            w.configure(fg_color=T.input, border_color=T.border2)
