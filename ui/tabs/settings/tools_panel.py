"""
ui/tabs/settings/tools_panel.py
Settings panel: YT-DLP Engine · Gallery-DL Engine · Data & Privacy.

Module-level helpers (moved here from settings_tab.py):
  _install_ytdlp_frozen()     – PyInstaller-safe yt-dlp wheel updater
  _install_gallery_dl_frozen() – PyInstaller-safe gallery-dl wheel updater

Dependencies on MainWindow:
  self._app.config   – extra_args, config_path, reset_to_defaults
  self._app.service  – get_all_tasks(), clear_history()
  self._app.toast    – feedback toasts
  self._app.get_tab  – used to check active conversion count
  self._app.navigate_to – navigate after data clear
"""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING
from urllib.error import URLError
from urllib.request import Request, urlopen  # noqa: S310

try:
    import customtkinter as ctk
except ImportError:          # pragma: no cover
    ctk = None               # type: ignore[assignment]

from ui.themes.tokens import T
from ui.tabs.settings._base_panel import _BasePanel

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = __import__("logging").getLogger(__name__)


# ── Module-level frozen-build updaters ────────────────────────────────────
# (Moved verbatim from settings_tab.py — no logic changes.)

def _install_ytdlp_frozen() -> str:
    """
    Install / upgrade yt-dlp without pip or a standalone python.exe.
    Works inside a PyInstaller-frozen EXE.  Security: SHA-256 from PyPI JSON
    API verified before extraction.  Returns override_dir path string.
    """
    import hashlib, json, os, shutil, zipfile
    from pathlib import Path as _Path

    override_dir = _Path(os.getenv("APPDATA", str(_Path.home()))) / "OmniDL" / "site-packages"
    override_dir.mkdir(parents=True, exist_ok=True)

    pypi_url = "https://pypi.org/pypi/yt-dlp/json"
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
            wheel_url   = release["url"]
            wheel_sha256 = (release.get("digests") or {}).get("sha256")
            break
    if not wheel_url:
        raise RuntimeError(f"No universal wheel found for yt-dlp {latest_ver}")
    if not wheel_sha256:
        raise RuntimeError(f"PyPI did not provide SHA-256 for yt-dlp {latest_ver}")
    if not wheel_url.startswith("https://"):
        raise RuntimeError(f"Unexpected wheel URL scheme (not https): {wheel_url!r}")

    tmp_whl = override_dir.parent / "yt_dlp_update.whl"
    try:
        req = Request(wheel_url, headers={"User-Agent": "OmniDL-updater/1.0"})
        with urlopen(req, timeout=120) as resp, open(tmp_whl, "wb") as fout:  # nosec B310
            import shutil as _sh; _sh.copyfileobj(resp, fout)
    except URLError as exc:
        raise RuntimeError(f"Download failed: {exc}") from exc

    try:
        digest = hashlib.sha256(tmp_whl.read_bytes()).hexdigest()
    except OSError as exc:
        tmp_whl.unlink(missing_ok=True)
        raise RuntimeError(f"Could not read wheel for verification: {exc}") from exc
    if digest.lower() != wheel_sha256.lower():
        tmp_whl.unlink(missing_ok=True)
        raise RuntimeError(
            f"SHA-256 mismatch for yt-dlp {latest_ver} — download may have been tampered with."
        )

    old_tree = override_dir / "yt_dlp"
    if old_tree.exists():
        shutil.rmtree(old_tree, ignore_errors=True)
    try:
        with zipfile.ZipFile(tmp_whl) as zf:
            for member in zf.namelist():
                if member.startswith("yt_dlp/"):
                    zf.extract(member, override_dir)
    finally:
        tmp_whl.unlink(missing_ok=True)
    return str(override_dir)


def _install_gallery_dl_frozen() -> str:
    """
    Install / upgrade gallery-dl without pip.  Same 5-step pattern as
    _install_ytdlp_frozen().  Security: SHA-256 verified before extraction.
    """
    import hashlib, json, os, shutil, zipfile
    from pathlib import Path as _Path

    override_dir = _Path(os.getenv("APPDATA", str(_Path.home()))) / "OmniDL" / "site-packages"
    override_dir.mkdir(parents=True, exist_ok=True)

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
            wheel_url    = release["url"]
            wheel_sha256 = (release.get("digests") or {}).get("sha256")
            break
    if not wheel_url:
        raise RuntimeError(f"No universal wheel found for gallery-dl {latest_ver}")
    if not wheel_sha256:
        raise RuntimeError(f"PyPI did not provide SHA-256 for gallery-dl {latest_ver}")
    if not wheel_url.startswith("https://"):
        raise RuntimeError(f"Unexpected wheel URL scheme: {wheel_url!r}")

    tmp_whl = override_dir.parent / "gallery_dl_update.whl"
    try:
        req = Request(wheel_url, headers={"User-Agent": "OmniDL-updater/1.0"})
        with urlopen(req, timeout=120) as resp, open(tmp_whl, "wb") as fout:  # nosec B310
            import shutil as _sh; _sh.copyfileobj(resp, fout)
    except URLError as exc:
        raise RuntimeError(f"Download failed: {exc}") from exc

    try:
        digest = hashlib.sha256(tmp_whl.read_bytes()).hexdigest()
    except OSError as exc:
        tmp_whl.unlink(missing_ok=True)
        raise RuntimeError(f"Could not read wheel for verification: {exc}") from exc
    if digest.lower() != wheel_sha256.lower():
        tmp_whl.unlink(missing_ok=True)
        raise RuntimeError(
            f"SHA-256 mismatch for gallery-dl {latest_ver} — download may have been tampered with."
        )

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
    return str(override_dir)


# ── Panel class ───────────────────────────────────────────────────────────

class ToolsPanel(_BasePanel):
    """
    Renders:
      🔧 YT-DLP Engine  (version + update + keyring + extra args)
      🖼  Gallery-DL Engine
      🗑  Data & Privacy
    """

    def __init__(self, master, app: "MainWindow") -> None:
        super().__init__(master, app)
        self._build()

    # ── Build ─────────────────────────────────────────────────────────────

    def _build(self) -> None:
        p   = self
        cfg = self._app.config

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

        keyring_row = ctk.CTkFrame(ytdlp, fg_color="transparent")
        keyring_row.pack(fill="x", padx=16, pady=(4, 4))
        self._keyring_btn = ctk.CTkButton(
            keyring_row, text="Cài keyring (hỗ trợ Brave/Chrome 127+)",
            height=32, corner_radius=8,
            fg_color=T.surface3, hover_color=T.border2,
            text_color=T.text2, font=ctk.CTkFont(size=12),
            command=self._install_keyring,
        )
        self._keyring_btn.pack(side="left")
        self._keyring_status = ctk.CTkLabel(
            keyring_row, text=self._keyring_installed_text(),
            font=ctk.CTkFont(size=11), text_color=T.text2,
        )
        self._keyring_status.pack(side="left", padx=(12, 0))
        ctk.CTkLabel(
            ytdlp, text="⚠  Cần thiết nếu Brave/Chrome báo lỗi DPAPI khi lấy cookies.",
            font=ctk.CTkFont(size=11), text_color=T.warning_text,
        ).pack(anchor="w", padx=16, pady=(0, 4))

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

        desc_row = ctk.CTkFrame(data_card, fg_color="transparent")
        desc_row.pack(fill="x", padx=16, pady=(12, 8))
        self._data_desc_lbl = ctk.CTkLabel(
            desc_row,
            text=(
                "Xóa toàn bộ dữ liệu ứng dụng: lịch sử tải, thiết lập cấu hình\n"
                "và đường dẫn cookie file. File cookie trên đĩa không bị xóa.\n"
                "Không thể thực hiện khi đang có download/conversion đang chạy."
            ),
            font=ctk.CTkFont(size=11), text_color=T.text3,
            justify="left", anchor="w",
        )
        self._data_desc_lbl.pack(fill="x")

        action_row = ctk.CTkFrame(data_card, fg_color="transparent")
        action_row.pack(fill="x", padx=16, pady=(0, 14))
        self._clear_data_btn = ctk.CTkButton(
            action_row, text="🗑  Xóa tất cả dữ liệu",
            height=34, corner_radius=8,
            fg_color=T.error_bg, hover_color=T.error,
            text_color=T.error, font=ctk.CTkFont(size=12, weight="bold"),
            command=self._clear_all_data,
        )
        self._clear_data_btn.pack(side="left")
        self._clear_data_status = ctk.CTkLabel(
            action_row, text="",
            font=ctk.CTkFont(size=11), text_color=T.text2,
        )
        self._clear_data_status.pack(side="left", padx=(12, 0))

    # ── YT-DLP handlers ───────────────────────────────────────────────────

    def _get_ytdlp_version(self) -> str:
        try:
            import yt_dlp
            return yt_dlp.version.__version__
        except Exception:
            return "unknown"

    def _update_ytdlp(self) -> None:
        import sys
        self._upd_btn.configure(state="disabled")
        self._upd_status.configure(text="Checking for updates…", text_color=T.primary_text)
        self._app.toast("Updating yt-dlp, please wait…", "info")

        def _worker():
            import importlib
            try:
                if getattr(sys, "frozen", False):
                    override_str = _install_ytdlp_frozen()
                else:
                    override_str = None
                    import subprocess
                    r = subprocess.run(
                        [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"],
                        capture_output=True, timeout=60)
                    if r.returncode != 0:
                        raise RuntimeError(r.stderr.decode(errors="replace")[:200])

                def _apply_on_ui(override=override_str):
                    if override and override not in sys.path:
                        sys.path.insert(0, override)
                    importlib.invalidate_caches()
                    try:
                        import yt_dlp.version as _yv; importlib.reload(_yv)
                    except Exception:
                        pass
                    ver = self._get_ytdlp_version()
                    self._upd_status.configure(text=f"Updated → {ver}", text_color=T.success)
                    self._ver_lbl.configure(text=ver)
                    self._app.toast(f"yt-dlp updated to {ver}", "success")

                self._ui_queue.put(_apply_on_ui)
            except Exception as exc:
                msg = str(exc)
                self._ui_queue.put(lambda: self._upd_status.configure(
                    text=f"Error: {msg[:80]}", text_color=T.error))
                self._ui_queue.put(lambda: self._app.toast(f"Update failed: {msg[:60]}", "error"))
            finally:
                self._ui_queue.put(lambda: self._upd_btn.configure(state="normal"))

        threading.Thread(target=_worker, daemon=True, name="omnidl-ytdlp-update").start()

    @staticmethod
    def _keyring_installed_text() -> str:
        try:
            import keyring as _kr
            ver = getattr(_kr, "__version__", "installed")
            return f"✓ keyring {ver}"
        except ImportError:
            return "⚠ Chưa cài — cần cho Brave/Chrome 127+"

    def _install_keyring(self) -> None:
        import sys
        self._keyring_btn.configure(state="disabled")
        self._keyring_status.configure(text="Đang cài keyring…", text_color=T.primary_text)

        def _worker() -> None:
            import importlib, subprocess
            try:
                r = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "--upgrade", "keyring"],
                    capture_output=True, timeout=120,
                )
                if r.returncode != 0:
                    raise RuntimeError(r.stderr.decode(errors="replace")[:200])
                importlib.invalidate_caches()
                status_text = self._keyring_installed_text()
                self._ui_queue.put(lambda t=status_text: (
                    self._keyring_status.configure(text=t, text_color=T.success),
                    self._app.toast("keyring đã cài. Thử lại lấy cookies từ Brave/Chrome.", "success"),
                ))
            except Exception as exc:
                msg = str(exc)[:80]
                self._ui_queue.put(lambda m=msg: (
                    self._keyring_status.configure(text=f"Lỗi: {m}", text_color=T.error),
                    self._app.toast(f"Cài keyring thất bại: {m}", "error"),
                ))
            finally:
                self._ui_queue.put(lambda: self._keyring_btn.configure(state="normal"))

        threading.Thread(target=_worker, daemon=True, name="omnidl-install-keyring").start()

    # ── Gallery-DL handlers ───────────────────────────────────────────────

    def _get_gallery_dl_version(self) -> str:
        try:
            import gallery_dl
            return getattr(gallery_dl, "__version__", "installed")
        except Exception:
            return "not installed"

    def _update_gallery_dl(self) -> None:
        import sys
        self._gdl_upd_btn.configure(state="disabled")
        self._gdl_upd_status.configure(text="Checking for updates…", text_color=T.primary_text)
        self._app.toast("Updating gallery-dl, please wait…", "info")

        def _worker():
            import importlib
            try:
                if getattr(sys, "frozen", False):
                    override_str = _install_gallery_dl_frozen()
                else:
                    override_str = None
                    import subprocess
                    r = subprocess.run(
                        [sys.executable, "-m", "pip", "install", "--upgrade", "gallery-dl"],
                        capture_output=True, timeout=60)
                    if r.returncode != 0:
                        raise RuntimeError(r.stderr.decode(errors="replace")[:200])

                def _apply_on_ui(override=override_str):
                    if override and override not in sys.path:
                        sys.path.insert(0, override)
                    importlib.invalidate_caches()
                    try:
                        import gallery_dl as _gdl; importlib.reload(_gdl)
                    except Exception:
                        pass
                    ver = self._get_gallery_dl_version()
                    self._gdl_upd_status.configure(text=f"Updated → {ver}", text_color=T.success)
                    self._gdl_ver_lbl.configure(text=ver)
                    self._app.toast(f"gallery-dl updated to {ver}", "success")

                self._ui_queue.put(_apply_on_ui)
            except Exception as exc:
                msg = str(exc)
                self._ui_queue.put(lambda: self._gdl_upd_status.configure(
                    text=f"Error: {msg[:80]}", text_color=T.error))
                self._ui_queue.put(lambda: self._app.toast(f"Update failed: {msg[:60]}", "error"))
            finally:
                self._ui_queue.put(lambda: self._gdl_upd_btn.configure(state="normal"))

        threading.Thread(target=_worker, daemon=True, name="omnidl-gdl-update").start()

    # ── Data & Privacy handler ────────────────────────────────────────────

    def _clear_all_data(self) -> None:
        import tkinter.messagebox as mb
        # Guard: no active tasks
        active_tasks = [
            t for t in self._app.service.get_all_tasks()
            if t.status.name in ("QUEUED", "DOWNLOADING", "PROCESSING")
        ]
        convert_tab = self._app.get_tab("convert")
        active_cv   = getattr(convert_tab, "_active_count", 0)
        if active_tasks or active_cv:
            parts = []
            if active_tasks: parts.append(f"{len(active_tasks)} download đang chạy")
            if active_cv:    parts.append(f"{active_cv} conversion đang chạy")
            self._app.toast("Không thể xóa dữ liệu khi " + " và ".join(parts) + ".", "error")
            return

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

        try:
            self._app.service.clear_history()
            self._app.config.reset_to_defaults()
            self._app.navigate_to("home")
            if hasattr(self, "_clear_data_status") and self._clear_data_status.winfo_exists():
                self._clear_data_status.configure(text="✓ Đã xóa", text_color=T.success)
                self.after(4000, lambda: (
                    self._clear_data_status.configure(text="", text_color=T.text2)
                    if self._clear_data_status.winfo_exists() else None
                ))
            self._app.toast("Đã xóa toàn bộ dữ liệu. Khởi động lại app để áp dụng đầy đủ.", "success")
            logger.info("User cleared all app data (history + config reset)")
        except Exception as exc:
            msg = str(exc)
            logger.error("Clear all data failed: %s", msg)
            if hasattr(self, "_clear_data_status") and self._clear_data_status.winfo_exists():
                self._clear_data_status.configure(text=f"Lỗi: {msg[:60]}", text_color=T.error)
            self._app.toast(f"Xóa dữ liệu thất bại: {msg[:60]}", "error")

    # ── Theme refresh ─────────────────────────────────────────────────────

    def _on_theme(self) -> None:
        if not self.winfo_exists():
            return
        for attr in ("_card_ytdlp", "_card_gallery_dl", "_card_data"):
            card = getattr(self, attr, None)
            if card and card.winfo_exists():
                card.configure(fg_color=T.surface, border_color=T.border)
        for lbl in self._section_labels:
            if lbl.winfo_exists(): lbl.configure(text_color=T.text3)
        for lbl in self._row_labels:
            if lbl.winfo_exists(): lbl.configure(text_color=T.text2)
        for attr in ("_ver_lbl", "_gdl_ver_lbl"):
            w = getattr(self, attr, None)
            if w and w.winfo_exists(): w.configure(text_color=T.primary_text)
        for attr in ("_upd_status", "_gdl_upd_status", "_keyring_status"):
            w = getattr(self, attr, None)
            if w and w.winfo_exists(): w.configure(text_color=T.text2)
        for attr in ("_upd_btn", "_gdl_upd_btn", "_keyring_btn"):
            w = getattr(self, attr, None)
            if w and w.winfo_exists():
                w.configure(fg_color=T.surface3, hover_color=T.border2, text_color=T.text2)
        w = getattr(self, "_extra_entry", None)
        if w and w.winfo_exists(): w.configure(fg_color=T.input, border_color=T.border2)
        w = getattr(self, "_data_desc_lbl", None)
        if w and w.winfo_exists(): w.configure(text_color=T.text3)
        w = getattr(self, "_clear_data_btn", None)
        if w and w.winfo_exists():
            w.configure(fg_color=T.error_bg, hover_color=T.error, text_color=T.error)
        w = getattr(self, "_clear_data_status", None)
        if w and w.winfo_exists(): w.configure(text_color=T.text2)
