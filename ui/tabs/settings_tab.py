"""
ui/tabs/settings_tab.py
All user-configurable options -- themed.
"""
from __future__ import annotations

import logging
import queue
import threading
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.error import URLError
from urllib.request import Request, urlopen  # noqa: S310 -- URL validated elsewhere

try:
    import customtkinter as ctk
except ImportError:  # pragma: no cover -- only missing in headless CI/tests
    ctk = None  # type: ignore[assignment]

from ui.themes.tokens import THEME_NAMES, T

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

    _appdata = os.getenv("APPDATA", str(Path.home()))
    override_dir = Path(_appdata) / "OmniDL" / "site-packages"
    override_dir.mkdir(parents=True, exist_ok=True)

    # -- 1. Resolve latest wheel URL + digest from PyPI -------------------
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
            f"PyPI did not provide a SHA-256 digest for yt-dlp {latest_ver} -- "
            "cannot verify download integrity."
        )
    # Guard: ensure the URL returned by PyPI is https (defence-in-depth for B310)
    if not wheel_url.startswith("https://"):
        raise RuntimeError(f"Unexpected wheel URL scheme (not https): {wheel_url!r}")

    # -- 2. Download wheel -------------------------------------------------
    tmp_whl = override_dir.parent / "yt_dlp_update.whl"
    try:
        req = Request(wheel_url, headers={"User-Agent": "OmniDL-updater/1.0"})
        with (
            urlopen(req, timeout=120) as resp,  # nosec B310
            open(tmp_whl, "wb") as fout,
        ):
            shutil.copyfileobj(resp, fout)
    except URLError as exc:
        raise RuntimeError(f"Download failed: {exc}") from exc

    # -- 3. Verify SHA-256 digest ------------------------------------------
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
            f"SHA-256 mismatch for yt-dlp {latest_ver} wheel -- "
            f"expected {wheel_sha256}, got {digest}. "
            "Download may have been tampered with. Aborting update."
        )

    # -- 4. Unpack wheel into override_dir ---------------------------------
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

    # -- 5. Prepend override_dir to sys.path -------------------------------
    override_str = str(override_dir)
    if override_str not in sys.path:
        sys.path.insert(0, override_str)

    # Invalidate import caches so the freshly extracted files are found
    importlib.invalidate_caches()


def _install_gallery_dl_frozen() -> None:
    """
    Install / upgrade gallery-dl without pip or a standalone python.exe.

    Mirrors _install_ytdlp_frozen() exactly — same 5-step pattern:
    1. Fetch latest wheel URL + SHA-256 from PyPI JSON API (gallery-dl).
    2. Download the .whl file.
    3. Verify SHA-256 digest.
    4. Unpack gallery_dl/ package files into %APPDATA%/OmniDL/site-packages.
    5. Prepend override_dir to sys.path and invalidate import caches.

    Security: SHA-256 taken from PyPI JSON API over a separate HTTPS
    connection — same model as yt-dlp update.
    """
    import hashlib
    import importlib
    import json
    import os
    import shutil
    import sys
    import zipfile
    from pathlib import Path

    _appdata = os.getenv("APPDATA", str(Path.home()))
    override_dir = Path(_appdata) / "OmniDL" / "site-packages"
    override_dir.mkdir(parents=True, exist_ok=True)

    # -- 1. Resolve latest wheel URL + digest from PyPI --------------------
    pypi_url = "https://pypi.org/pypi/gallery-dl/json"
    try:
        req = Request(pypi_url, headers={"User-Agent": "OmniDL-updater/1.0"})
        with urlopen(req, timeout=20) as resp:  # nosec B310
            meta = json.loads(resp.read())
    except URLError as exc:
        raise RuntimeError(f"Cannot reach PyPI: {exc}") from exc

    latest_ver = meta["info"]["version"]
    wheel_url: str | None = None
    wheel_sha256: str | None = None
    for release in meta["releases"].get(latest_ver, []):
        fn = release["filename"]
        if fn.endswith(".whl") and "none-any" in fn:
            wheel_url = release["url"]
            wheel_sha256 = (release.get("digests") or {}).get("sha256")
            break
    if not wheel_url:
        raise RuntimeError(f"No universal wheel found for gallery-dl {latest_ver}")
    if not wheel_sha256:
        raise RuntimeError(
            f"PyPI did not provide a SHA-256 digest for gallery-dl {latest_ver} -- "
            "cannot verify download integrity."
        )
    if not wheel_url.startswith("https://"):
        raise RuntimeError(f"Unexpected wheel URL scheme: {wheel_url!r}")

    # -- 2. Download wheel -------------------------------------------------
    tmp_whl = override_dir.parent / "gallery_dl_update.whl"
    try:
        req = Request(wheel_url, headers={"User-Agent": "OmniDL-updater/1.0"})
        with (
            urlopen(req, timeout=120) as resp,  # nosec B310
            open(tmp_whl, "wb") as fout,
        ):
            shutil.copyfileobj(resp, fout)
    except URLError as exc:
        raise RuntimeError(f"Download failed: {exc}") from exc

    # -- 3. Verify SHA-256 -------------------------------------------------
    try:
        digest = hashlib.sha256(tmp_whl.read_bytes()).hexdigest()
    except OSError as exc:
        tmp_whl.unlink(missing_ok=True)
        raise RuntimeError(f"Could not read wheel for verification: {exc}") from exc
    if digest.lower() != wheel_sha256.lower():
        tmp_whl.unlink(missing_ok=True)
        raise RuntimeError(
            f"SHA-256 mismatch for gallery-dl {latest_ver} -- "
            "download may have been tampered with. Aborting."
        )

    # -- 4. Unpack gallery_dl/ into override_dir ---------------------------
    old_tree = override_dir / "gallery_dl"
    if old_tree.exists():
        shutil.rmtree(old_tree, ignore_errors=True)
    try:
        with zipfile.ZipFile(tmp_whl) as zf:
            for member in zf.namelist():
                if member.startswith("gallery_dl/"):
                    zf.extract(member, override_dir)
    finally:
        tmp_whl.unlink(missing_ok=True)

    # -- 5. Prepend override_dir to sys.path and reload --------------------
    override_str = str(override_dir)
    if override_str not in sys.path:
        sys.path.insert(0, override_str)
    importlib.invalidate_caches()


_BaseFrame = ctk.CTkFrame if ctk is not None else object


class SettingsTab(_BaseFrame):  # type: ignore[misc]

    def __init__(self, master, app: "MainWindow") -> None:
        super().__init__(master, fg_color=T.bg, corner_radius=0)
        self._app = app
        # Thread-safe queue for yt-dlp/gallery-dl update workers (Python 3.14)
        self._ui_queue: queue.Queue = queue.Queue()
        self._build()
        self._drain_ui_queue()
        T.register(self._on_theme)

    def _drain_ui_queue(self) -> None:
        """Drain _ui_queue every 150 ms on the UI thread (Python 3.14 safe)."""
        if not self.winfo_exists():
            return
        try:
            while True:
                fn = self._ui_queue.get_nowait()
                try:
                    fn()
                except Exception as exc:
                    import logging as _log
                    _log.getLogger(__name__).warning(
                        "settings_tab _ui_queue raised: %s", exc)
        except queue.Empty:
            pass
        self.after(150, self._drain_ui_queue)

    def _build(self) -> None:
        self._section_labels: list = []
        self._row_labels: list = []
        self._sliders: list = []
        self._switches: list = []
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

        # -- Download location ---------------------------------------------
        self._section(p, "📁   DOWNLOAD LOCATION")
        self._card_loc = loc = self._card(p)
        row = ctk.CTkFrame(loc, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=14)
        self._dir_lbl = ctk.CTkLabel(
            row, text=str(cfg.download_dir),
            font=ctk.CTkFont(size=12), text_color=T.primary_text)
        self._dir_lbl.pack(side="left", fill="x", expand=True)
        self._browse_dir_btn = ctk.CTkButton(
            row, text="Browse", width=80, height=32, corner_radius=8,
            fg_color=T.surface3, hover_color=T.border2,
            text_color=T.text2, font=ctk.CTkFont(size=12),
            command=self._browse_dir,
        )
        self._browse_dir_btn.pack(side="left", padx=(10, 0))

        # -- Download behaviour --------------------------------------------
        self._section(p, "⚙   DOWNLOAD BEHAVIOUR")
        self._card_beh = beh = self._card(p)

        self._concurrent_var = ctk.IntVar(value=cfg.max_concurrent)
        self._slider_row(beh, "Max concurrent downloads",
                         self._concurrent_var, 1, 8,
                         lambda v: cfg.set("max_concurrent", int(v)))
        self._add_value_label(beh, self._concurrent_var)
        self._concurrent_hint = ctk.CTkLabel(
            beh,
            text="⚠  Changes take effect after restarting the app.",
            font=ctk.CTkFont(size=11),
            text_color=T.warning_text,
            anchor="e",
        )
        self._concurrent_hint.pack(anchor="e", padx=16, pady=(0, 8))

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
        self._section(p, "🍪   PER-PLATFORM COOKIES")
        self._card_cookies = pc_card = self._card(p)

        ctk.CTkLabel(
            pc_card,
            text=(
                "Mỗi nền tảng có thể dùng cookie riêng — "
                "ưu tiên hơn cookie chung bên trên.\n"
                "Để trống nếu không cần, hoặc dùng cookie chung làm fallback."
            ),
            font=ctk.CTkFont(size=11),
            text_color=T.text3,
            justify="left",
            wraplength=460,
        ).pack(anchor="w", padx=16, pady=(10, 6))

        # Widget refs for _on_theme(): lists indexed by platform slot
        self._pc_lbls:        list = []   # CTkLabel showing path
        self._pc_browse_btns: list = []   # 📂 Browse buttons
        self._pc_clear_btns:  list = []   # 🗑 Clear buttons

        _PC_PLATFORMS = [
            ("tiktok",     "TikTok"),
            ("instagram",  "Instagram"),
            ("facebook",   "Facebook"),
            ("twitter",    "Twitter / X"),
            ("threads",    "Threads"),
        ]
        cfg = self._app.config
        for key, label in _PC_PLATFORMS:
            row = ctk.CTkFrame(pc_card, fg_color="transparent")
            row.pack(fill="x", padx=16, pady=(0, 6))

            ctk.CTkLabel(
                row, text=label, width=100,
                font=ctk.CTkFont(size=12), text_color=T.text2,
                anchor="w",
            ).pack(side="left")

            path_lbl = ctk.CTkLabel(
                row,
                text=self._short_cookie_path(cfg.get_cookie_for_platform(key)),
                font=ctk.CTkFont(size=11), text_color=T.primary_text,
                anchor="w",
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

        ctk.CTkFrame(pc_card, fg_color=T.border, height=1).pack(
            fill="x", padx=16, pady=(6, 10)
        )

        # -- Appearance ----------------------------------------------------
        self._section(p, "🎨   APPEARANCE")
        self._card_app = app_card = self._card(p)
        theme_row = ctk.CTkFrame(app_card, fg_color="transparent")
        theme_row.pack(fill="x", padx=16, pady=14)
        ctk.CTkLabel(theme_row, text="Theme",
                     font=ctk.CTkFont(size=12), text_color=T.text2).pack(side="left")
        self._theme_om = ctk.CTkOptionMenu(
            theme_row, values=THEME_NAMES,
            command=self._change_theme, width=140, corner_radius=8,
        )
        # Set the dropdown to show the currently active theme
        current_theme = self._app.config.theme
        if current_theme in THEME_NAMES:
            self._theme_om.set(current_theme)
        self._theme_om.pack(side="right")

        # -- yt-dlp engine -------------------------------------------------
        self._section(p, "🔧   YT-DLP ENGINE")
        self._card_ytdlp = ytdlp = self._card(p)

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

        # -- gallery-dl engine ---------------------------------------------
        self._section(p, "🖼   GALLERY-DL ENGINE")
        self._card_gallery_dl = gdl = self._card(p)

        gdl_ver_row = ctk.CTkFrame(gdl, fg_color="transparent")
        gdl_ver_row.pack(fill="x", padx=16, pady=(12, 4))
        ctk.CTkLabel(gdl_ver_row, text="Installed version",
                     font=ctk.CTkFont(size=12), text_color=T.text2).pack(side="left")
        self._gdl_ver_lbl = ctk.CTkLabel(
            gdl_ver_row, text=self._get_gallery_dl_version(),
            font=ctk.CTkFont(size=12), text_color=T.primary_text)
        self._gdl_ver_lbl.pack(side="right")

        gdl_upd_row = ctk.CTkFrame(gdl, fg_color="transparent")
        gdl_upd_row.pack(fill="x", padx=16, pady=(4, 14))
        self._gdl_upd_btn = ctk.CTkButton(
            gdl_upd_row, text="Update gallery-dl now",
            height=32, corner_radius=8,
            fg_color=T.surface3, hover_color=T.border2,
            text_color=T.text2, font=ctk.CTkFont(size=12),
            command=self._update_gallery_dl)
        self._gdl_upd_btn.pack(side="left")
        self._gdl_upd_status = ctk.CTkLabel(
            gdl_upd_row, text="", font=ctk.CTkFont(size=11), text_color=T.text2)
        self._gdl_upd_status.pack(side="left", padx=(12, 0))

        # -- Data & Privacy ------------------------------------------------
        self._section(p, "🗑   DATA & PRIVACY")
        self._card_data = data_card = self._card(p)

        # Description row
        desc_row = ctk.CTkFrame(data_card, fg_color="transparent")
        desc_row.pack(fill="x", padx=16, pady=(12, 8))
        self._data_desc_lbl = ctk.CTkLabel(
            desc_row,
            text=(
                "Xóa toàn bộ dữ liệu ứng dụng: lịch sử tải, thiết lập cấu hình\n"
                "và đường dẫn cookie file. File cookie trên đĩa không bị xóa.\n"
                "Không thể thực hiện khi đang có download/conversion đang chạy."
            ),
            font=ctk.CTkFont(size=11),
            text_color=T.text3,
            justify="left",
            anchor="w",
        )
        self._data_desc_lbl.pack(fill="x")

        # Action row
        action_row = ctk.CTkFrame(data_card, fg_color="transparent")
        action_row.pack(fill="x", padx=16, pady=(0, 14))
        self._clear_data_btn = ctk.CTkButton(
            action_row,
            text="🗑  Xóa tất cả dữ liệu",
            height=34, corner_radius=8,
            fg_color=T.error_bg, hover_color=T.error,
            text_color=T.error,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._clear_all_data,
        )
        self._clear_data_btn.pack(side="left")
        self._clear_data_status = ctk.CTkLabel(
            action_row, text="",
            font=ctk.CTkFont(size=11), text_color=T.text2,
        )
        self._clear_data_status.pack(side="left", padx=(12, 0))

    # -- Helpers -----------------------------------------------------------

    def _section(self, parent, text: str) -> None:
        lbl = ctk.CTkLabel(
            parent, text=text,
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=T.text3,
        )
        lbl.pack(anchor="w", padx=28, pady=(18, 5))
        self._section_labels.append(lbl)

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
        lbl = ctk.CTkLabel(row, text=label,
                     font=ctk.CTkFont(size=12), text_color=T.text2)
        lbl.pack(side="left")
        self._row_labels.append(lbl)
        sl = ctk.CTkSlider(
            row, from_=lo, to=hi, number_of_steps=hi - lo,
            variable=var, command=debounced, width=160,
            button_color=T.primary, progress_color=T.primary,
        )
        sl.pack(side="right")
        self._sliders.append(sl)

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
        lbl = ctk.CTkLabel(row, text=label,
                     font=ctk.CTkFont(size=12), text_color=T.text2)
        lbl.pack(side="left")
        self._row_labels.append(lbl)
        sw = ctk.CTkSwitch(
            row, variable=var, text="",
            command=lambda: debounced_cmd(var.get()),
            onvalue=True, offvalue=False,
            progress_color=T.primary, button_color=T.primary_text,
        )
        sw.pack(side="right")
        self._switches.append(sw)

    def _browse_dir(self) -> None:
        import tkinter.filedialog as fd
        chosen = fd.askdirectory(
            title="Select download folder",
            initialdir=str(self._app.config.download_dir))
        if chosen:
            self._app.config.set("download_dir", chosen)
            self._dir_lbl.configure(text=chosen)

    def _on_proxy_focusout(self, _event=None) -> None:
        """Validate proxy URL on FocusOut before saving to config.

        Accepts http://, https://, socks4://, socks5:// schemes and empty
        string (clears proxy).  Invalid values show a toast and are NOT saved.
        """
        val = self._proxy_entry.get().strip()
        if not val:
            # Empty = clear proxy — always valid
            self._app.config.set("proxy", "")
            return
        _valid_schemes = ("http://", "https://", "socks4://", "socks5://")
        if any(val.lower().startswith(s) for s in _valid_schemes):
            self._app.config.set("proxy", val)
            self._proxy_entry.configure(border_color=T.border2)
        else:
            self._proxy_entry.configure(border_color=T.error)
            self.after(1500, lambda: self._proxy_entry.configure(
                border_color=T.border2))
            self._app.toast(
                "Proxy không hợp lệ — phải bắt đầu bằng http://, https://, socks4://, hoặc socks5://",
                "error",
            )

    def _browse_cookie_file(self) -> None:
        import shutil
        import tkinter.filedialog as fd

        chosen = fd.askopenfilename(
            title="Select cookies.txt (Netscape format)",
            filetypes=[("Cookie files", "*.txt"), ("All files", "*.*")])
        if not chosen:
            return
        src = Path(chosen)
        if not src.is_file():
            self._app.toast("File not found.", "error")
            return

        # Auto-copy into the safe directory so _validate_cookie_path always
        # accepts it — regardless of where the user picked the file from.
        safe_dir = self._app.config.config_path.parent / "cookies"
        safe_dir.mkdir(parents=True, exist_ok=True)
        dest = safe_dir / src.name
        try:
            shutil.copy2(src, dest)
        except OSError as exc:
            logger.warning("Failed to copy cookie file: %s", exc)
            self._app.toast(f"Cannot copy cookie file: {exc}", "error")
            return

        self._app.config.set("cookie_file", str(dest))
        self._cf_lbl.configure(text=self._short_cookie_path(str(dest)))
        self._app.toast("Cookie file đã được sao chép vào thư mục an toàn.", "info")

    def _clear_cookie_file(self) -> None:
        self._app.config.set("cookie_file", "")
        self._cf_lbl.configure(text="No file selected")

    def _browse_platform_cookie(self, platform_key: str, path_lbl: "ctk.CTkLabel") -> None:
        """Browse for a per-platform cookie file and save it.

        Reuses the same copy-to-safe-dir + CWE-22 pattern as
        _browse_cookie_file() so security guarantees are identical.
        """
        import shutil
        import tkinter.filedialog as fd

        platform_name = {
            "tiktok": "TikTok", "instagram": "Instagram",
            "facebook": "Facebook", "twitter": "Twitter/X",
            "threads": "Threads",
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

        # Copy to safe directory — _validate_cookie_path_raw enforces same root.
        safe_dir = self._app.config.config_path.parent / "cookies"
        safe_dir.mkdir(parents=True, exist_ok=True)
        # Prefix filename with platform key to avoid collisions between
        # same-named cookie files from different platforms.
        dest_name = f"{platform_key}_{src_path.name}"
        dest = safe_dir / dest_name
        try:
            shutil.copy2(src_path, dest)
        except OSError as exc:
            logger.warning("Failed to copy platform cookie file: %s", exc)
            self._app.toast(f"Không thể sao chép cookie file: {exc}", "error")
            return

        self._app.config.set_cookie_for_platform(platform_key, str(dest))
        path_lbl.configure(text=self._short_cookie_path(str(dest)))
        self._app.toast(
            f"Cookie {platform_name} đã được lưu vào thư mục an toàn.", "info"
        )

    def _clear_platform_cookie(self, platform_key: str, path_lbl: "ctk.CTkLabel") -> None:
        """Clear the per-platform cookie path from config."""
        self._app.config.set_cookie_for_platform(platform_key, "")
        path_lbl.configure(text="No file selected")

    @staticmethod
    def _short_cookie_path(path: str) -> str:
        if not path:
            return "No file selected"
        s = str(Path(path))
        return s if len(s) <= 45 else f"…{s[-42:]}"

    def _change_theme(self, theme: str) -> None:
        self._app.config.set("theme", theme)
        T.set_mode(theme)                        # update palette + fire all _on_theme callbacks
        ctk.set_appearance_mode(T.ctk_base)     # map custom theme → "dark"/"light" for CTk

    def _get_ytdlp_version(self) -> str:
        try:
            import yt_dlp
            return yt_dlp.version.__version__
        except Exception:
            return "unknown"

    def _update_ytdlp(self) -> None:
        """
        Update yt-dlp in both frozen (PyInstaller EXE) and source/dev modes.

        Frozen mode: PyInstaller bundles Python inside the EXE -- there is no
        python.exe on disk to invoke pip with.  Instead we download the latest
        yt-dlp wheel directly from PyPI using only the stdlib (urllib + zipfile),
        unpack it into %APPDATA%/OmniDL/site-packages, prepend that directory to
        sys.path, and reload yt_dlp.version so the UI reflects the new version
        immediately -- no restart required.

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
                self._ui_queue.put(lambda: self._upd_status.configure(
                    text=f"Updated → {ver}", text_color=T.success))
                self._ui_queue.put(lambda: self._ver_lbl.configure(text=ver))
                self._ui_queue.put(lambda: self._app.toast(
                        f"yt-dlp updated to {ver}", "success"
                    )
                )
            except Exception as exc:
                msg = str(exc)
                self._ui_queue.put(lambda: self._upd_status.configure(
                    text=f"Error: {msg[:80]}", text_color=T.error))
                self._ui_queue.put(lambda: self._app.toast(f"Update failed: {msg[:60]}", "error")
                )
            finally:
                self._ui_queue.put(lambda: self._upd_btn.configure(state="normal"))

        threading.Thread(target=_worker, daemon=True).start()

    def _get_gallery_dl_version(self) -> str:
        try:
            import gallery_dl
            return getattr(gallery_dl, "__version__", "installed")
        except Exception:
            return "not installed"

    def _update_gallery_dl(self) -> None:
        """
        Update gallery-dl in both frozen and source/dev modes.

        Frozen mode: same PyPI wheel approach as yt-dlp — downloads the
        latest wheel, verifies SHA-256, unpacks gallery_dl/ into
        %APPDATA%/OmniDL/site-packages, prepends to sys.path.

        Source/dev mode: delegates to pip.
        """
        import sys
        self._gdl_upd_btn.configure(state="disabled")
        self._gdl_upd_status.configure(
            text="Checking for updates…", text_color=T.primary_text
        )
        self._app.toast("Updating gallery-dl, please wait…", "info")

        def _worker():
            import importlib
            try:
                if getattr(sys, "frozen", False):
                    _install_gallery_dl_frozen()
                else:
                    import subprocess
                    r = subprocess.run(
                        [sys.executable, "-m", "pip", "install",
                         "--upgrade", "gallery-dl"],
                        capture_output=True, timeout=60)
                    if r.returncode != 0:
                        raise RuntimeError(r.stderr.decode(errors="replace")[:200])

                # Reload so the version label reflects the new version immediately
                try:
                    import gallery_dl as _gdl
                    importlib.reload(_gdl)
                except Exception:
                    pass

                ver = self._get_gallery_dl_version()
                self._ui_queue.put(lambda: self._gdl_upd_status.configure(
                    text=f"Updated → {ver}", text_color=T.success))
                self._ui_queue.put(lambda: self._gdl_ver_lbl.configure(text=ver))
                self._ui_queue.put(lambda: self._app.toast(
                    f"gallery-dl updated to {ver}", "success"))
            except Exception as exc:
                msg = str(exc)
                self._ui_queue.put(lambda: self._gdl_upd_status.configure(
                    text=f"Error: {msg[:80]}", text_color=T.error))
                self._ui_queue.put(lambda: self._app.toast(
                    f"Update failed: {msg[:60]}", "error"))
            finally:
                self._ui_queue.put(lambda: self._gdl_upd_btn.configure(state="normal"))

        threading.Thread(target=_worker, daemon=True).start()

    def _clear_all_data(self) -> None:
        """Clear all app data: download history, config, and cookie path reference.

        Safety checks (mirrors _on_close):
          1. Blocked when any download or conversion is active — resetting
             config mid-download causes unpredictable retry/timeout behaviour.
          2. Requires explicit confirmation dialog before proceeding.

        What is cleared:
          • Download history (JSONL file + in-memory list)
          • All config settings → factory defaults
          • Cookie file path reference in config (file on disk NOT deleted)

        What is NOT cleared:
          • Downloaded files in the download folder
          • Cookie .txt files in %APPDATA%/OmniDL/cookies/
          • yt-dlp / gallery-dl packages in site-packages
        """
        import tkinter.messagebox as mb

        # --- Guard: no active tasks ------------------------------------------
        active_tasks = [
            t for t in self._app.service.get_all_tasks()
            if t.status.name in ("QUEUED", "DOWNLOADING", "PROCESSING")
        ]
        convert_tab = self._app.get_tab("convert")
        active_cv = getattr(convert_tab, "_active_count", 0)

        if active_tasks or active_cv:
            parts = []
            if active_tasks:
                parts.append(f"{len(active_tasks)} download đang chạy")
            if active_cv:
                parts.append(f"{active_cv} conversion đang chạy")
            self._app.toast(
                "Không thể xóa dữ liệu khi " + " và ".join(parts) + ".",
                "error",
            )
            return

        # --- Confirmation dialog ---------------------------------------------
        confirmed = mb.askyesno(
            "OmniDL — Xác nhận xóa dữ liệu",
            (
                "Thao tác này sẽ:\n"
                "  • Xóa toàn bộ lịch sử tải\n"
                "  • Đặt lại tất cả thiết lập về mặc định\n"
                "  • Xóa đường dẫn cookie file khỏi cấu hình\n\n"
                "File cookie và file đã tải sẽ không bị ảnh hưởng.\n\n"
                "Tiếp tục?"
            ),
            icon="warning",
        )
        if not confirmed:
            return

        # --- Execute clear ---------------------------------------------------
        try:
            # 1. Clear download history (thread-safe via HistoryRepository lock)
            self._app.service.clear_history()

            # 2. Reset all config to factory defaults (thread-safe via ConfigManager lock)
            self._app.config.reset_to_defaults()

            # 3. Update Settings UI labels that display config-derived values.
            #    IntVar/BooleanVar/StringVar set during _build() are NOT
            #    live-bound to config — update the two labels that show
            #    path strings explicitly.  Sliders and switches use default
            #    values that match the reset config (3 concurrent, 3 retries,
            #    etc.) so they remain visually consistent after reset.
            cfg = self._app.config
            if hasattr(self, "_dir_lbl") and self._dir_lbl.winfo_exists():
                self._dir_lbl.configure(text=str(cfg.download_dir))
            if hasattr(self, "_cf_lbl") and self._cf_lbl.winfo_exists():
                self._cf_lbl.configure(text="No file selected")

            # 4. Navigate to home so History tab does not show stale entries.
            #    navigate_to("home") calls HomeTab.refresh() which resets the
            #    analysis card to its welcome state.
            self._app.navigate_to("home")

            # 5. Update status label and show toast
            if hasattr(self, "_clear_data_status") and self._clear_data_status.winfo_exists():
                self._clear_data_status.configure(
                    text="✓ Đã xóa", text_color=T.success
                )
                # Fade the status label after 4 s
                self.after(4000, lambda: (
                    self._clear_data_status.configure(text="", text_color=T.text2)
                    if self._clear_data_status.winfo_exists() else None
                ))

            self._app.toast(
                "Đã xóa toàn bộ dữ liệu. Khởi động lại app để áp dụng đầy đủ.",
                "success",
            )
            logger.info("User cleared all app data (history + config reset)")

        except Exception as exc:
            msg = str(exc)
            logger.error("Clear all data failed: %s", msg)
            if hasattr(self, "_clear_data_status") and self._clear_data_status.winfo_exists():
                self._clear_data_status.configure(
                    text=f"Lỗi: {msg[:60]}", text_color=T.error
                )
            self._app.toast(f"Xóa dữ liệu thất bại: {msg[:60]}", "error")

    def _on_theme(self) -> None:
        """Refresh every CTk widget in SettingsTab after a palette change.

        Uses explicit stored widget refs instead of winfo_children() tree
        walking because winfo_children() returns internal Tk widgets (Frame,
        Canvas, Label), not CTk wrapper objects — type(w).__name__ would be
        'Frame' not 'CTkFrame', breaking all isinstance/name checks.
        """
        if not self.winfo_exists():
            return
        self.configure(fg_color=T.bg)
        self._scroll.configure(
            fg_color="transparent",
            scrollbar_button_color=T.scrollbar,
            scrollbar_button_hover_color=T.scrollbar_hover,
        )
        # Card frames
        for attr in ("_card_loc", "_card_beh", "_card_net", "_card_cookies",
                     "_card_app", "_card_ytdlp", "_card_gallery_dl", "_card_data"):
            card = getattr(self, attr, None)
            if card and card.winfo_exists():
                card.configure(fg_color=T.surface, border_color=T.border)
        # Section header labels
        for lbl in getattr(self, "_section_labels", []):
            if lbl.winfo_exists():
                lbl.configure(text_color=T.text3)
        # Labels that show paths / values
        for attr in ("_dir_lbl", "_cf_lbl", "_ver_lbl", "_gdl_ver_lbl"):
            w = getattr(self, attr, None)
            if w and w.winfo_exists():
                w.configure(text_color=T.primary_text)
        # Per-platform cookie path labels
        for lbl in getattr(self, "_pc_lbls", []):
            if lbl.winfo_exists():
                lbl.configure(text_color=T.primary_text)
        # Status / update labels
        for attr in ("_upd_status", "_gdl_upd_status"):
            w = getattr(self, attr, None)
            if w and w.winfo_exists():
                w.configure(text_color=T.text2)
        # Browse / action buttons
        for attr in ("_browse_dir_btn", "_browse_cf_btn", "_upd_btn", "_gdl_upd_btn"):
            w = getattr(self, attr, None)
            if w and w.winfo_exists():
                w.configure(fg_color=T.surface3, hover_color=T.border2,
                            text_color=T.text2)
        # Per-platform cookie browse buttons
        for btn in getattr(self, "_pc_browse_btns", []):
            if btn.winfo_exists():
                btn.configure(fg_color=T.surface3, hover_color=T.border2,
                              text_color=T.text2)
        # Per-platform cookie clear buttons
        for btn in getattr(self, "_pc_clear_btns", []):
            if btn.winfo_exists():
                btn.configure(fg_color=T.error_bg, hover_color=T.error_bg,
                              text_color=T.error)
        # Clear cookie button
        w = getattr(self, "_clear_cf_btn", None)
        if w and w.winfo_exists():
            w.configure(fg_color=T.error_bg, hover_color=T.error_bg,
                        text_color=T.error)
        # Clear all data button
        w = getattr(self, "_clear_data_btn", None)
        if w and w.winfo_exists():
            w.configure(fg_color=T.error_bg, hover_color=T.error,
                        text_color=T.error)
        # Data & Privacy description label
        w = getattr(self, "_data_desc_lbl", None)
        if w and w.winfo_exists():
            w.configure(text_color=T.text3)
        # Clear data status label
        w = getattr(self, "_clear_data_status", None)
        if w and w.winfo_exists():
            w.configure(text_color=T.text2)
        # Concurrent downloads restart hint
        w = getattr(self, "_concurrent_hint", None)
        if w and w.winfo_exists():
            w.configure(text_color=T.warning_text)
        # OptionMenus
        for attr in ("_theme_om", "_browser_om"):
            w = getattr(self, attr, None)
            if w and w.winfo_exists():
                w.configure(fg_color=T.surface3, button_color=T.border2,
                            text_color=T.text2)
        # Row labels (slider/switch descriptors)
        for lbl in getattr(self, "_row_labels", []):
            if lbl.winfo_exists():
                lbl.configure(text_color=T.text2)
        # Sliders
        for sl in getattr(self, "_sliders", []):
            if sl.winfo_exists():
                sl.configure(button_color=T.primary, progress_color=T.primary)
        # Switches
        for sw in getattr(self, "_switches", []):
            if sw.winfo_exists():
                sw.configure(progress_color=T.primary)
        # Entry fields
        for attr in ("_proxy_entry", "_extra_entry"):
            w = getattr(self, attr, None)
            if w and w.winfo_exists():
                w.configure(fg_color=T.input, border_color=T.border2)

