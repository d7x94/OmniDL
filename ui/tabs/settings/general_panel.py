"""
ui/tabs/settings/general_panel.py
Settings panel: Download Location · Download Behaviour · Appearance.

Dependencies on MainWindow:
  self._app.config   – download_dir, max_concurrent, max_retries,
                       embed_thumbnail, embed_metadata, theme
  self._app.toast    – feedback toasts
"""
from __future__ import annotations

from typing import TYPE_CHECKING

try:
    import customtkinter as ctk
except ImportError:          # pragma: no cover
    ctk = None               # type: ignore[assignment]

from ui.tabs.settings._base_panel import _BasePanel
from ui.themes.tokens import THEME_NAMES, T

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = __import__("logging").getLogger(__name__)


# ── FIX-BROWSE-1: Windows IFileOpenDialog folder picker ───────────────────
# Tkinter's askdirectory uses the legacy SHBrowseForFolder dialog which has
# two bugs on Windows:
#   1. Folder rename inside the dialog silently fails (text reverts to "New Folder")
#   2. initialdir with a path that doesn't resolve → silently falls back to Documents
#
# The modern IFileOpenDialog (COM, Vista+) fixes both. We invoke it via ctypes
# with zero new dependencies. Falls back to askdirectory on any error.
#
# Vtable offsets (IFileOpenDialog inherits IFileDialog → IModalWindow → IUnknown):
#   IUnknown:       QueryInterface=0, AddRef=1, Release=2
#   IModalWindow:   Show=3
#   IFileDialog:    SetFileTypes=4..8, SetOptions=9, GetOptions=10,
#                   SetDefaultFolder=11, SetFolder=12, GetFolder=13,
#                   GetCurrentSelection=14..18, GetResult=20, AddPlace=21..26
#   IFileOpenDialog: GetResults=27, GetSelectedItems=28

def _pick_folder_win32(initial_dir: str) -> "str | None":
    """
    Use Windows IFileOpenDialog (COM, Vista+) to pick a folder.
    Unlike Tkinter's dialog, this supports in-dialog folder creation
    and rename correctly (no "New Folder" revert bug).
    Returns the selected absolute path or None on cancel / error.
    """
    import ctypes
    import ctypes.wintypes as wt
    import struct
    from pathlib import Path

    # GUIDs (GUID wire format: first 3 fields are LE, last 8 bytes are BE)
    def _guid(data32: int, data16a: int, data16b: int, *rest8: int) -> "ctypes.Array[ctypes.c_byte]":
        raw = struct.pack("<IHH", data32, data16a, data16b) + bytes(rest8)
        return (ctypes.c_byte * 16)(*raw)

    CLSID_FileOpenDialog = _guid(0xDC1C5A9C, 0xE88A, 0x4DDE, 0xA5, 0xA1, 0x60, 0xF8, 0x2A, 0x20, 0xAE, 0xF7)
    IID_IFileOpenDialog  = _guid(0xD57C7288, 0xD4AD, 0x4768, 0xBE, 0x02, 0x9D, 0x96, 0x95, 0x32, 0xD9, 0x60)
    IID_IShellItem       = _guid(0x43826D1E, 0xE718, 0x42EE, 0xBC, 0x55, 0xA1, 0xE2, 0x61, 0xC3, 0x7B, 0xFE)

    S_OK                 = 0
    CLSCTX_INPROC_SERVER = 1
    FOS_PICKFOLDERS      = 0x00000020
    FOS_FORCEFILESYSTEM  = 0x00000040
    SIGDN_FILESYSPATH    = ctypes.c_int(-2147319808)  # 0x80058000 as signed int

    ole32  = ctypes.windll.ole32
    shell32 = ctypes.windll.shell32

    # Helper: call a COM vtable method by index
    def _com(obj: ctypes.c_void_p, idx: int, restype, *args):
        vtbl_ptr = ctypes.cast(ctypes.cast(obj, ctypes.POINTER(ctypes.c_void_p))[0],
                               ctypes.POINTER(ctypes.c_void_p))
        fn_addr = vtbl_ptr[idx]
        arg_types = [ctypes.c_void_p] + [type(a) for a in args]
        proto = ctypes.WINFUNCTYPE(restype, *arg_types)
        return proto(fn_addr)(obj, *args)

    dialog = ctypes.c_void_p()
    hr = ole32.CoCreateInstance(
        CLSID_FileOpenDialog, None, CLSCTX_INPROC_SERVER,
        IID_IFileOpenDialog, ctypes.byref(dialog),
    )
    if hr != S_OK or not dialog:
        return None

    try:
        # SetOptions: pick folder + filesystem items only
        _com(dialog, 9, ctypes.HRESULT, ctypes.c_uint(FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM))

        # SetFolder: set the initial directory using IShellItem
        _init = Path(initial_dir)
        if _init.exists():
            shell_item = ctypes.c_void_p()
            hr2 = shell32.SHCreateItemFromParsingName(
                str(_init), None, IID_IShellItem, ctypes.byref(shell_item)
            )
            if hr2 == S_OK and shell_item:
                try:
                    _com(dialog, 12, ctypes.HRESULT, shell_item)  # SetFolder
                finally:
                    _com(shell_item, 2, ctypes.HRESULT)  # Release IShellItem

        # Show the dialog (hwnd=0 → no parent)
        hr3 = _com(dialog, 3, ctypes.HRESULT, wt.HWND(0))  # Show
        if hr3 != S_OK:
            return None  # user cancelled (HRESULT_FROM_WIN32 ERROR_CANCELLED)

        # GetResult → IShellItem
        result = ctypes.c_void_p()
        hr4 = _com(dialog, 20, ctypes.HRESULT, ctypes.byref(result))  # GetResult
        if hr4 != S_OK or not result:
            return None

        try:
            # GetDisplayName(SIGDN_FILESYSPATH) → PWSTR
            pwstr = ctypes.c_wchar_p()
            hr5 = _com(result, 5, ctypes.HRESULT, SIGDN_FILESYSPATH, ctypes.byref(pwstr))  # GetDisplayName
            if hr5 != S_OK or not pwstr:
                return None
            chosen = pwstr.value
            ole32.CoTaskMemFree(pwstr)
            return str(Path(chosen).resolve()) if chosen else None
        finally:
            _com(result, 2, ctypes.HRESULT)  # Release IShellItem result

    except Exception as exc:
        logger.debug("IFileOpenDialog error (non-fatal, will fallback): %s", exc)
        return None
    finally:
        _com(dialog, 2, ctypes.HRESULT)  # Release IFileOpenDialog


class GeneralPanel(_BasePanel):
    """
    Renders the three purely-general settings sections:
      📁 Download Location
      ⚙  Download Behaviour
      🎨 Appearance
    No background workers — all handlers run synchronously on the UI thread.
    """

    def __init__(self, master, app: "MainWindow") -> None:
        super().__init__(master, app)
        self._build()

    # ── Build ─────────────────────────────────────────────────────────────

    def _build(self) -> None:
        p   = self
        cfg = self._app.config

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
        current_theme = self._app.config.theme
        if current_theme in THEME_NAMES:
            self._theme_om.set(current_theme)
        self._theme_om.pack(side="right")

        # -- Debug logging -------------------------------------------------
        self._section(p, "🐛   DEVELOPER")
        self._card_dev = dev = self._card(p)

        self._debug_var = ctk.BooleanVar(value=cfg.debug_logging)
        self._switch_row(dev, "Debug Logging", self._debug_var,
                         self._on_debug_toggle)

        self._debug_hint = ctk.CTkLabel(
            dev,
            text="Writes detailed trace to omnidl_debug.log  •  Restart not required",
            font=ctk.CTkFont(size=11),
            text_color=T.text3,
            anchor="e",
        )
        self._debug_hint.pack(anchor="e", padx=16, pady=(0, 10))

    # ── Handlers ──────────────────────────────────────────────────────────

    def _browse_dir(self) -> None:
        """
        FIX-BROWSE-1: Open folder picker.
        On Windows: use IFileOpenDialog (modern COM dialog) which correctly
        supports in-dialog folder creation and rename.
        Other platforms: fall back to tkinter.filedialog.askdirectory.
        Always normalize the returned path and verify the folder exists
        before updating config, preventing the 'opens at Documents' bug
        caused by a stale/non-existent initialdir.
        """
        import sys
        from pathlib import Path

        # Determine safe initialdir — fall back to home if stored path is gone
        current_dir = Path(str(self._app.config.download_dir))
        initialdir = str(current_dir) if current_dir.exists() else str(Path.home())

        chosen: "str | None" = None

        # Try the modern Windows COM dialog first
        if sys.platform == "win32":
            try:
                chosen = _pick_folder_win32(initialdir)
            except Exception as exc:
                logger.debug("_pick_folder_win32 raised (fallback): %s", exc)
                chosen = None

        # Fallback: Tkinter dialog (macOS, Linux, or Windows COM failure)
        if chosen is None:
            import tkinter.filedialog as fd
            chosen = fd.askdirectory(
                title="Select download folder",
                initialdir=initialdir,
            )

        if not chosen:
            return  # user cancelled

        # Normalize to OS-native absolute path (fixes mixed-separator bug)
        chosen_path = Path(chosen).resolve()

        # Safety: create the folder if the user typed a new path that
        # doesn't exist yet (edge case with fallback dialog)
        try:
            chosen_path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("Cannot create download dir %s: %s", chosen_path, exc)
            self._app.toast(f"Không thể tạo thư mục: {exc}", "error")
            return

        chosen_str = str(chosen_path)
        self._app.config.set("download_dir", chosen_str)
        self._dir_lbl.configure(text=chosen_str)

    def _change_theme(self, theme: str) -> None:
        self._app.config.set("theme", theme)
        T.set_mode(theme)                        # update palette + fire all _on_theme callbacks
        ctk.set_appearance_mode(T.ctk_base)     # map custom theme → "dark"/"light" for CTk

    def _on_debug_toggle(self, enabled: bool) -> None:
        """Enable or disable debug logging live — no restart required."""
        self._app.config.set("debug_logging", enabled)
        from utils.logger import apply_debug_logging
        apply_debug_logging(enabled)
        msg = "Debug logging ON — writing to omnidl_debug.log" if enabled else "Debug logging OFF"
        logger.info(msg)
        if hasattr(self._app, "toast"):
            self._app.toast(msg)

    # ── Theme refresh ─────────────────────────────────────────────────────

    def _on_theme(self) -> None:
        if not self.winfo_exists():
            return
        for attr in ("_card_loc", "_card_beh", "_card_app", "_card_dev"):
            card = getattr(self, attr, None)
            if card and card.winfo_exists():
                card.configure(fg_color=T.surface, border_color=T.border)
        for lbl in self._section_labels:
            if lbl.winfo_exists():
                lbl.configure(text_color=T.text3)
        for lbl in self._row_labels:
            if lbl.winfo_exists():
                lbl.configure(text_color=T.text2)
        for sl in self._sliders:
            if sl.winfo_exists():
                sl.configure(button_color=T.primary, progress_color=T.primary)
        for sw in self._switches:
            if sw.winfo_exists():
                sw.configure(progress_color=T.primary)
        w = getattr(self, "_dir_lbl", None)
        if w and w.winfo_exists():
            w.configure(text_color=T.primary_text)
        w = getattr(self, "_browse_dir_btn", None)
        if w and w.winfo_exists():
            w.configure(fg_color=T.surface3, hover_color=T.border2, text_color=T.text2)
        w = getattr(self, "_concurrent_hint", None)
        if w and w.winfo_exists():
            w.configure(text_color=T.warning_text)
        w = getattr(self, "_theme_om", None)
        if w and w.winfo_exists():
            w.configure(fg_color=T.surface3, button_color=T.border2, text_color=T.text2)
        w = getattr(self, "_debug_hint", None)
        if w and w.winfo_exists():
            w.configure(text_color=T.text3)
