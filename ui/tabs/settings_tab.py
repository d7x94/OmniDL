"""
ui/tabs/settings_tab.py
All user-configurable options — themed.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import customtkinter as ctk

from ui.themes.tokens import T

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = logging.getLogger(__name__)



def _install_ytdlp_frozen() -> None:
    """
    Install / upgrade yt-dlp without pip or a standalone python.exe.

    Works inside a PyInstaller-frozen EXE by:
    1. Fetching the latest yt-dlp wheel URL + SHA-256 from PyPI JSON API.
    2. Downloading the .whl file (which is a zip archive).
    3. Verifying the SHA-256 digest against the value published by PyPI.
    4. Unpacking verified files into %APPDATA%/OmniDL/site-packages.
    5. Prepending that directory to sys.path so the new yt_dlp package
       shadows the bundled one for the rest of this process session.

    Security note: Step 3 guards against a compromised CDN delivering a
    malicious wheel.  The digest is taken from the PyPI JSON API response
    (fetched over a separate HTTPS connection) so an attacker would need to
    compromise both the PyPI API endpoint and the CDN simultaneously.
    """
    import hashlib
    import importlib
    import json
    import os
    import shutil
    import sys
    import zipfile
    from pathlib import Path
    from urllib.error import URLError
    from urllib.request import Request, urlopen

    _appdata = os.getenv("APPDATA", str(Path.home()))
    override_dir = Path(_appdata) / "OmniDL" / "site-packages"
    override_dir.mkdir(parents=True, exist_ok=True)

    # ── 1. Resolve latest wheel URL + digest from PyPI ───────────────────
    pypi_url = "https://pypi.org/pypi/yt-dlp/json"
    try:
        req = Request(pypi_url, headers={"User-Agent": "OmniDL-updater/1.0"})
        with urlopen(req, timeout=20) as resp:  # nosec B310
            meta = json.loads(resp.read())
    except URLError as exc:
        raise RuntimeError(f"Cannot reach PyPI: {exc}") from exc

    latest_ver = meta["info"]["version"]
    # Pick the universal py3 wheel (no-deps, all platforms)
    wheel_url: str | None = None
    wheel_sha256: str | None = None
    for release in meta["releases"].get(latest_ver, []):
        fn = release["filename"]
        if fn.endswith(".whl") and "none-any" in fn:
            wheel_url = release["url"]
            wheel_sha256 = (release.get("digests") or {}).get("sha256")
            break
    if not wheel_url:
        raise RuntimeError(f"No universal wheel found for yt-dlp {latest_ver}")
    if not wheel_sha256:
        raise RuntimeError(
            f"PyPI did not provide a SHA-256 digest for yt-dlp {latest_ver} — "
            "cannot verify download integrity."
        )
    # Guard: ensure the URL returned by PyPI is https (defence-in-depth for B310)
    if not wheel_url.startswith("https://"):
        raise RuntimeError(f"Unexpected wheel URL scheme (not https): {wheel_url!r}")

    # ── 2. Download wheel ─────────────────────────────────────────────────
    tmp_whl = override_dir.parent / "yt_dlp_update.whl"
    try:
        req = Request(wheel_url, headers={"User-Agent": "OmniDL-updater/1.0"})
        with (  # nosec B310
            urlopen(req, timeout=120) as resp,
            open(tmp_whl, "wb") as fout,
        ):
            shutil.copyfileobj(resp, fout)
    except URLError as exc:
        raise RuntimeError(f"Download failed: {exc}") from exc

    # ── 3. Verify SHA-256 digest ──────────────────────────────────────────
    # Compute digest of the downloaded file and compare against the value
    # published in the PyPI JSON API.  Raises RuntimeError (which the caller
    # catches and surfaces to the user) if they do not match.
    try:
        digest = hashlib.sha256(tmp_whl.read_bytes()).hexdigest()
    except OSError as exc:
        tmp_whl.unlink(missing_ok=True)
        raise RuntimeError(
            f"Could not read downloaded wheel for verification: {exc}"
        ) from exc
    if digest.lower() != wheel_sha256.lower():
        tmp_whl.unlink(missing_ok=True)
        raise RuntimeError(
            f"SHA-256 mismatch for yt-dlp {latest_ver} wheel — "
            f"expected {wheel_sha256}, got {digest}. "
            "Download may have been tampered with. Aborting update."
        )

    # ── 4. Unpack wheel into override_dir ─────────────────────────────────
    # Remove old yt_dlp tree first to avoid stale .pyc files
    old_tree = override_dir / "yt_dlp"
    if old_tree.exists():
        shutil.rmtree(old_tree, ignore_errors=True)
    try:
        with zipfile.ZipFile(tmp_whl) as zf:
            # Only extract yt_dlp package files (skip dist-info)
            for member in zf.namelist():
                if member.startswith("yt_dlp/"):
                    zf.extract(member, override_dir)
    finally:
        tmp_whl.unlink(missing_ok=True)

    # ── 5. Prepend override_dir to sys.path ───────────────────────────────
    override_str = str(override_dir)
    if override_str not in sys.path:
        sys.path.insert(0, override_str)

    # Invalidate import caches so the freshly extracted files are found
    importlib.invalidate_caches()



class SettingsTab(ctk.CTkFrame):

    def __init__(self, master, app: "MainWindow") -> None:
        super().__init__(master, fg_color=T.bg, corner_radius=0)
        self._app = app
        self._build()
        T.register(self._on_theme)

    def _build(self) -> None:
        self._scroll = ctk.CTkScrollableFrame(
            self, fg_color="transparent",
            scrollbar_button_color=T.scrollbar,
            scrollbar_button_hover_color=T.scrollbar_hover)
        self._scroll.pack(fill="both", expand=True)
        p = self._scroll
        cfg = self._app.config

        ctk.CTkLabel(
            p, text="Settings",
            font=ctk.CTkFont(size=22, weight="bold"), text_color=T.text,
        ).pack(anchor="w", padx=28, pady=(24, 18))

        # ── Download location ─────────────────────────────────────────────
        self._section(p, "📁   DOWNLOAD LOCATION")
        loc = self._card(p)
        row = ctk.CTkFrame(loc, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=14)
        self._dir_lbl = ctk.CTkLabel(
            row, text=str(cfg.download_dir),
            font=ctk.CTkFont(size=12), text_color=T.primary_text)
        self._dir_lbl.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            row, text="Browse", width=80, height=32, corner_radius=8,
            fg_color=T.surface3, hover_color=T.border2,
            text_color=T.text2, font=ctk.CTkFont(size=12),
            command=self._browse_dir,
        ).pack(side="left", padx=(10, 0))

        # ── Download behaviour ────────────────────────────────────────────
        self._section(p, "⬇   DOWNLOAD BEHAVIOUR")
        beh = self._card(p)

        self._concurrent_var = ctk.IntVar(value=cfg.max_concurrent)
        self._slider_row(beh, "Max concurrent downloads",
                         self._concurrent_var, 1, 8,
                         lambda v: cfg.set("max_concurrent", int(v)))
        self._add_value_label(beh, self._concurrent_var)

        self._retries_var = ctk.IntVar(value=cfg.max_retries)
        self._slider_row(beh, "Max retries on failure",
                         self._retries_var, 0, 10,
                         lambda v: cfg.set("max_retries", int(v)))
        self._add_value_label(beh, self._retries_var)

        self._thumb_var = ctk.BooleanVar(value=cfg.embed_thumbnail)
        self._switch_row(beh, "Embed thumbnail", self._thumb_var,
                         lambda v: cfg.set("embed_thumbnail", v))

        self._meta_var = ctk.BooleanVar(value=cfg.embed_metadata)
        self._switch_row(beh, "Embed metadata", self._meta_var,
                         lambda v: cfg.set("embed_metadata", v))

        # ── Network & Auth ────────────────────────────────────────────────
        self._section(p, "🌐   NETWORK & AUTHENTICATION")
        net = self._card(p)

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
        self._proxy_entry.bind("<FocusOut>",
            lambda _: cfg.set("proxy", self._proxy_entry.get().strip()))

        self._cookies_var = ctk.BooleanVar(value=cfg.use_cookies)
        self._switch_row(net, "Use browser cookies", self._cookies_var,
                         lambda v: cfg.set("use_cookies", v))

        browser_row = ctk.CTkFrame(net, fg_color="transparent")
        browser_row.pack(fill="x", padx=16, pady=(4, 4))
        ctk.CTkLabel(browser_row, text="Cookie source browser",
                     font=ctk.CTkFont(size=12), text_color=T.text2).pack(side="left")
        ctk.CTkOptionMenu(
            browser_row,
            values=["chrome", "firefox", "safari", "edge", "opera", "brave"],
            command=lambda v: cfg.set("cookies_browser", v),
            width=130, corner_radius=8,
        ).pack(side="right")

        ctk.CTkLabel(
            net,
            text="⚠  Chrome/Brave locked? Export cookies to a .txt file instead:",
            font=ctk.CTkFont(size=11), text_color=T.warning_text,
        ).pack(anchor="w", padx=16, pady=(10, 2))

        cf_row = ctk.CTkFrame(net, fg_color="transparent")
        cf_row.pack(fill="x", padx=16, pady=(0, 14))

        self._cf_lbl = ctk.CTkLabel(
            cf_row, text=self._short_cookie_path(cfg.cookie_file),
            font=ctk.CTkFont(size=11), text_color=T.primary_text, anchor="w")
        self._cf_lbl.pack(side="left", fill="x", expand=True)

        ctk.CTkButton(
            cf_row, text="Browse…", width=80, height=28, corner_radius=6,
            fg_color=T.surface3, hover_color=T.border2,
            text_color=T.text2, font=ctk.CTkFont(size=11),
            command=self._browse_cookie_file,
        ).pack(side="left", padx=(8, 4))

        ctk.CTkButton(
            cf_row, text="✕ Clear", width=64, height=28, corner_radius=6,
            fg_color=T.error_bg, hover_color=T.error_bg,
            text_color=T.error, font=ctk.CTkFont(size=11),
            command=self._clear_cookie_file,
        ).pack(side="left")

        # ── Appearance ────────────────────────────────────────────────────
        self._section(p, "🎨   APPEARANCE")
        app_card = self._card(p)
        theme_row = ctk.CTkFrame(app_card, fg_color="transparent")
        theme_row.pack(fill="x", padx=16, pady=14)
        ctk.CTkLabel(theme_row, text="Theme",
                     font=ctk.CTkFont(size=12), text_color=T.text2).pack(side="left")
        ctk.CTkOptionMenu(
            theme_row, values=["dark", "light", "system"],
            command=self._change_theme, width=130, corner_radius=8,
        ).pack(side="right")

        # ── yt-dlp engine ─────────────────────────────────────────────────
        self._section(p, "⚙   YT-DLP ENGINE")
        ytdlp = self._card(p)

        ver_row = ctk.CTkFrame(ytdlp, fg_color="transparent")
        ver_row.pack(fill="x", padx=16, pady=(12, 4))
        ctk.CTkLabel(ver_row, text="Installed version",
                     font=ctk.CTkFont(size=12), text_color=T.text2).pack(side="left")
        self._ver_lbl = ctk.CTkLabel(
            ver_row, text=self._get_ytdlp_version(),
            font=ctk.CTkFont(size=12), text_color=T.primary_text)
        self._ver_lbl.pack(side="right")

        upd_row = ctk.CTkFrame(ytdlp, fg_color="transparent")
        upd_row.pack(fill="x", padx=16, pady=(4, 4))
        self._upd_btn = ctk.CTkButton(
            upd_row, text="Update yt-dlp now",
            height=32, corner_radius=8,
            fg_color=T.surface3, hover_color=T.border2,
            text_color=T.text2, font=ctk.CTkFont(size=12),
            command=self._update_ytdlp)
        self._upd_btn.pack(side="left")
        self._upd_status = ctk.CTkLabel(
            upd_row, text="", font=ctk.CTkFont(size=11), text_color=T.text2)
        self._upd_status.pack(side="left", padx=(12, 0))

        extra_row = ctk.CTkFrame(ytdlp, fg_color="transparent")
        extra_row.pack(fill="x", padx=16, pady=(4, 14))
        ctk.CTkLabel(extra_row, text="Extra yt-dlp args",
                     font=ctk.CTkFont(size=12), text_color=T.text2).pack(side="left")
        self._extra_entry = ctk.CTkEntry(
            extra_row, width=260, height=32, corner_radius=8,
            fg_color=T.input, border_color=T.border2,
            placeholder_text="e.g. --no-playlist")
        self._extra_entry.insert(0, cfg.extra_args)
        self._extra_entry.pack(side="right")
        self._extra_entry.bind("<FocusOut>",
            lambda _: cfg.set("extra_args", self._extra_entry.get().strip()))

    # ── Helpers ───────────────────────────────────────────────────────────

    def _section(self, parent, text: str) -> None:
        ctk.CTkLabel(
            parent, text=text,
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=T.text3,
        ).pack(anchor="w", padx=28, pady=(18, 5))

    def _card(self, parent) -> ctk.CTkFrame:
        card = ctk.CTkFrame(
            parent, fg_color=T.surface,
            corner_radius=10, border_width=1, border_color=T.border)
        card.pack(fill="x", padx=28, pady=(0, 4))
        return card

    def _slider_row(self, parent, label, var, lo, hi, cmd) -> None:
        def debounced(v):
            attr = f"_sa_{label.replace(' ', '_')}"
            aid = getattr(self, attr, None)
            if aid:
                self.after_cancel(aid)
            setattr(self, attr, self.after(500, lambda: cmd(v)))

        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(12, 2))
        ctk.CTkLabel(row, text=label,
                     font=ctk.CTkFont(size=12), text_color=T.text2).pack(side="left")
        ctk.CTkSlider(
            row, from_=lo, to=hi, number_of_steps=hi - lo,
            variable=var, command=debounced, width=160,
            button_color=T.primary, progress_color=T.primary,
        ).pack(side="right")

    def _add_value_label(self, parent, var: ctk.IntVar) -> ctk.CTkLabel:
        lbl = ctk.CTkLabel(
            parent, textvariable=var,
            font=ctk.CTkFont(size=11), text_color=T.primary_text)
        lbl.pack(anchor="e", padx=16, pady=(0, 4))
        return lbl

    def _switch_row(self, parent, label, var, cmd) -> None:
        def debounced_cmd(v: bool) -> None:
            attr = f"_sw_{label.replace(' ', '_')}"
            aid = getattr(self, attr, None)
            if aid:
                self.after_cancel(aid)
            setattr(self, attr, self.after(300, lambda: cmd(v)))

        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(8, 4))
        ctk.CTkLabel(row, text=label,
                     font=ctk.CTkFont(size=12), text_color=T.text2).pack(side="left")
        ctk.CTkSwitch(
            row, variable=var, text="",
            command=lambda: debounced_cmd(var.get()),
            onvalue=True, offvalue=False,
            progress_color=T.primary, button_color=T.primary_text,
        ).pack(side="right")

    def _browse_dir(self) -> None:
        import tkinter.filedialog as fd
        chosen = fd.askdirectory(
            title="Select download folder",
            initialdir=str(self._app.config.download_dir))
        if chosen:
            self._app.config.set("download_dir", chosen)
            self._dir_lbl.configure(text=chosen)

    def _browse_cookie_file(self) -> None:
        import tkinter.filedialog as fd
        chosen = fd.askopenfilename(
            title="Select cookies.txt (Netscape format)",
            filetypes=[("Cookie files", "*.txt"), ("All files", "*.*")])
        if not chosen:
            return
        if not Path(chosen).is_file():
            self._app.toast("File not found.", "error")
            return
        self._app.config.set("cookie_file", chosen)
        self._cf_lbl.configure(text=self._short_cookie_path(chosen))
        self._app.toast("Cookie file set.", "info")

    def _clear_cookie_file(self) -> None:
        self._app.config.set("cookie_file", "")
        self._cf_lbl.configure(text="No file selected")

    @staticmethod
    def _short_cookie_path(path: str) -> str:
        if not path:
            return "No file selected"
        s = str(Path(path))
        return s if len(s) <= 45 else f"…{s[-42:]}"

    def _change_theme(self, theme: str) -> None:
        self._app.config.set("theme", theme)
        ctk.set_appearance_mode(theme)
        T.set_mode(theme if theme != "system" else "dark")

    def _get_ytdlp_version(self) -> str:
        try:
            import yt_dlp
            return yt_dlp.version.__version__
        except Exception:
            return "unknown"

    def _update_ytdlp(self) -> None:
        """
        Update yt-dlp in both frozen (PyInstaller EXE) and source/dev modes.

        Frozen mode: PyInstaller bundles Python inside the EXE — there is no
        python.exe on disk to invoke pip with.  Instead we download the latest
        yt-dlp wheel directly from PyPI using only the stdlib (urllib + zipfile),
        unpack it into %APPDATA%/OmniDL/site-packages, prepend that directory to
        sys.path, and reload yt_dlp.version so the UI reflects the new version
        immediately — no restart required.

        Source/dev mode: delegate to pip as before.
        """
        import sys
        self._upd_btn.configure(state="disabled")
        self._upd_status.configure(
            text="Checking for updates…", text_color=T.primary_text
        )
        self._app.toast("Updating yt-dlp, please wait…", "info")

        def _worker():
            import importlib
            try:
                if getattr(sys, "frozen", False):
                    _install_ytdlp_frozen()
                else:
                    import subprocess
                    r = subprocess.run(
                        [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"],
                        capture_output=True, timeout=60)
                    if r.returncode != 0:
                        raise RuntimeError(r.stderr.decode(errors="replace")[:200])

                # Reload version module so label shows the new version right away.
                try:
                    import yt_dlp.version as _yv
                    importlib.reload(_yv)
                except Exception:
                    pass

                ver = self._get_ytdlp_version()
                self.after(0, lambda: self._upd_status.configure(
                    text=f"Updated → {ver}", text_color=T.success))
                self.after(0, lambda: self._ver_lbl.configure(text=ver))
                self.after(
                    0, lambda: self._app.toast(f"yt-dlp updated to {ver} ✓", "success")
                )
            except Exception as exc:
                msg = str(exc)
                self.after(0, lambda: self._upd_status.configure(
                    text=f"Error: {msg[:80]}", text_color=T.error))
                self.after(
                    0, lambda: self._app.toast(f"Update failed: {msg[:60]}", "error")
                )
            finally:
                self.after(0, lambda: self._upd_btn.configure(state="normal"))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_theme(self) -> None:
        if not self.winfo_exists():
            return
        self.configure(fg_color=T.bg)
        self._scroll.configure(scrollbar_button_color=T.scrollbar,
                               scrollbar_button_hover_color=T.scrollbar_hover)
