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
# with zero new dependencies. Falls back to askdirectory only on COM setup error
# (not on user cancel).
#
# Vtable offsets (IFileOpenDialog inherits IFileDialog → IModalWindow → IUnknown):
#   IUnknown:       QueryInterface=0, AddRef=1, Release=2
#   IModalWindow:   Show=3
#   IFileDialog:    SetFileTypes=4..8, SetOptions=9, GetOptions=10,
#                   SetDefaultFolder=11, SetFolder=12, GetFolder=13,
#                   GetCurrentSelection=14..18, GetResult=20, AddPlace=21..26
#   IFileOpenDialog: GetResults=27, GetSelectedItems=28
#
# FIX-BROWSE-2: ctypes.HRESULT as restype auto-raises OSError on any failure
#   HRESULT, including user-cancel (0x800704C7). That exception was swallowed
#   by the outer except, returning None and incorrectly triggering the tkinter
#   fallback — causing the dialog to reopen ("auto-opens browse again" bug).
#   Fix: use c_long as restype so all HRESULTs are plain return values.
#
# FIX-BROWSE-3: type(ctypes.byref(x)) == CArgObject which has no from_param
#   method. Passing it via [type(a) for a in args] in WINFUNCTYPE argtypes
#   raised "item N in _argtypes_ has no from_param method", crashing _com()
#   before the dialog window ever appeared, causing permanent fallback to the
#   legacy SHBrowseForFolder dialog (which has the "New Folder" rename bug).
#   Fix: detect byref/pointer objects via the _obj attribute and map them to
#   c_void_p (same wire size on both 32- and 64-bit Windows).

# Sentinel: returned by _pick_folder_win32 when the user explicitly cancels.
# Distinguishes "user cancelled" from "COM setup failed" (both would be None
# otherwise), so _browse_dir can skip the tkinter fallback on cancel.
_CANCELLED = object()


def _pick_folder_win32(initial_dir: str) -> "str | None | object":
    """
    Use Windows IFileOpenDialog (COM, Vista+) to pick a folder.
    Unlike Tkinter's dialog, this supports in-dialog folder creation
    and rename correctly (no "New Folder" revert bug).

    Returns:
      str         - selected absolute path
      _CANCELLED  - user cancelled the dialog (caller must NOT open tkinter)
      None        - COM setup/CoCreateInstance failed (caller may fallback)
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
    # HRESULT_FROM_WIN32(ERROR_CANCELLED) — returned by Show() when user cancels
    HRESULT_CANCELLED    = 0x800704C7
    CLSCTX_INPROC_SERVER = 1
    FOS_PICKFOLDERS      = 0x00000020
    FOS_FORCEFILESYSTEM  = 0x00000040
    SIGDN_FILESYSPATH    = ctypes.c_int(-2147319808)  # 0x80058000 as signed int

    ole32   = ctypes.windll.ole32
    shell32 = ctypes.windll.shell32

    def _com(obj: ctypes.c_void_p, idx: int, *args):
        """
        Call COM vtable slot idx on obj. Returns raw HRESULT as Python int.

        FIX-BROWSE-2: Uses c_long (not HRESULT) so failure HRESULTs — including
        user-cancel 0x800704C7 — are returned as plain values, not raised as
        OSError. Callers check the value explicitly.

        FIX-BROWSE-3: ctypes.byref() produces a CArgObject with no from_param.
        Detect it via the _obj attribute and substitute c_void_p (same pointer
        width on both 32- and 64-bit Windows).
        """
        vtbl_ptr = ctypes.cast(
            ctypes.cast(obj, ctypes.POINTER(ctypes.c_void_p))[0],
            ctypes.POINTER(ctypes.c_void_p))
        fn_addr = vtbl_ptr[idx]

        def _argtype(a):
            # byref()/pointer wrappers expose ._obj; use c_void_p as the type
            return ctypes.c_void_p if hasattr(a, '_obj') else type(a)

        arg_types = [ctypes.c_void_p] + [_argtype(a) for a in args]
        proto = ctypes.WINFUNCTYPE(ctypes.c_long, *arg_types)  # c_long, not HRESULT
        return proto(fn_addr)(obj, *args)

    # CoInitialize is required before CoCreateInstance.
    # Tkinter may have already initialized COM (OleInitialize → APARTMENTTHREADED),
    # in which case CoInitialize returns S_FALSE (1) — still needs matching
    # CoUninitialize.  If COM was initialized with a different threading model,
    # it returns RPC_E_CHANGED_MODE (0x80010106) and we must NOT call CoUninitialize.
    co_hr = ole32.CoInitialize(None)
    _co_needs_uninit = co_hr in (S_OK, 1)  # S_OK or S_FALSE

    dialog = ctypes.c_void_p()
    hr = ole32.CoCreateInstance(
        CLSID_FileOpenDialog, None, CLSCTX_INPROC_SERVER,
        IID_IFileOpenDialog, ctypes.byref(dialog),
    )
    if hr != S_OK or not dialog:
        if _co_needs_uninit:
            ole32.CoUninitialize()
        return None  # COM setup failed → caller may fallback to tkinter

    try:
        # SetOptions: pick folder + filesystem items only
        _com(dialog, 9, ctypes.c_uint(FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM))

        # SetFolder: set the initial directory using IShellItem
        _init = Path(initial_dir)
        if _init.exists():
            shell_item = ctypes.c_void_p()
            hr2 = shell32.SHCreateItemFromParsingName(
                str(_init), None, IID_IShellItem, ctypes.byref(shell_item)
            )
            if hr2 == S_OK and shell_item:
                try:
                    _com(dialog, 12, shell_item)  # SetFolder
                finally:
                    _com(shell_item, 2)  # Release IShellItem

        # Show the dialog (hwnd=0 → no parent)
        hr3 = _com(dialog, 3, wt.HWND(0))  # Show

        # FIX-BROWSE-2: normalise to unsigned 32-bit before comparing to
        # HRESULT_CANCELLED (0x800704C7); c_long on Windows is signed 32-bit
        # so the cancel HRESULT appears negative.
        hr3_u = hr3 & 0xFFFFFFFF
        if hr3_u == HRESULT_CANCELLED:
            return _CANCELLED  # user cancelled — caller must NOT open tkinter
        if hr3_u != S_OK:
            return None  # other Show() error

        # GetResult → IShellItem
        # IFileDialog vtable (counting from IUnknown=0):
        #   0-2: IUnknown, 3: Show, 4-19: IFileDialog methods,
        #   20: GetResult, 21: AddPlace, 22: SetDefaultExtension,
        #   23: Close, 24: SetClientGuid, 25: ClearClientData, 26: SetFilter
        # IFileOpenDialog: 27: GetResults, 28: GetSelectedItems
        result = ctypes.c_void_p()
        hr4 = _com(dialog, 20, ctypes.byref(result))  # GetResult — FIX-BROWSE-3
        if hr4 != S_OK or not result:
            return None

        try:
            # GetDisplayName(SIGDN_FILESYSPATH) → PWSTR
            pwstr = ctypes.c_wchar_p()
            # FIX-BROWSE-3: byref(pwstr) arg type fixed by _argtype() in _com
            hr5 = _com(result, 5, SIGDN_FILESYSPATH, ctypes.byref(pwstr))  # GetDisplayName
            if hr5 != S_OK or not pwstr:
                return None
            chosen = pwstr.value
            ole32.CoTaskMemFree(pwstr)
            return str(Path(chosen).resolve()) if chosen else None
        finally:
            _com(result, 2)  # Release IShellItem result

    except Exception as exc:
        logger.debug("IFileOpenDialog error (non-fatal, will fallback): %s", exc)
        return None  # unexpected error → caller may fallback to tkinter
    finally:
        _com(dialog, 2)  # Release IFileOpenDialog
        if _co_needs_uninit:
            ole32.CoUninitialize()


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
        FIX-BROWSE-1/2/3: Open folder picker.
        On Windows: use IFileOpenDialog (modern COM dialog, Vista+) which
        correctly supports in-dialog folder creation and rename — the legacy
        SHBrowseForFolder dialog (used by tkinter) silently reverts renames.
        Other platforms: use tkinter.filedialog.askdirectory.

        FIX-BROWSE-2: _pick_folder_win32 now returns the _CANCELLED sentinel
        when the user explicitly cancels. We must NOT open the tkinter fallback
        in that case — doing so caused the "dialog auto-opens a second time" bug.

        FIX-BROWSE-3: byref() args in the COM vtable caller no longer crash
        ctypes, so the COM dialog is now reached reliably on Windows.
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
                result = _pick_folder_win32(initialdir)
            except Exception as exc:
                logger.debug("_pick_folder_win32 raised (fallback): %s", exc)
                result = None

            if result is _CANCELLED:
                return  # FIX-BROWSE-2: user cancelled — do NOT open tkinter
            chosen = result  # None → COM setup failed, fall through to tkinter

        # Fallback: Tkinter dialog (macOS, Linux, or Windows COM setup failure)
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
