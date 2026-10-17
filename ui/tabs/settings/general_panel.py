"""Settings panel: Download Location · Behaviour · Appearance (PySide6)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

from ui.tabs.settings._base_panel import _BasePanel
from ui.themes.tokens import THEME_NAMES, T
from utils.i18n import LANGUAGES, t

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = __import__("logging").getLogger(__name__)


# ── Windows COM folder picker (unchanged logic from CTk version) ──────────────
_CANCELLED = object()


def _pick_folder_win32(initial_dir: str):
    import ctypes
    import ctypes.wintypes as wt
    import struct
    from pathlib import Path

    def _guid(data32, data16a, data16b, *rest8):
        raw = struct.pack("<IHH", data32, data16a, data16b) + bytes(rest8)
        return (ctypes.c_byte * 16)(*raw)

    CLSID_FileOpenDialog = _guid(0xDC1C5A9C, 0xE88A, 0x4DDE, 0xA5, 0xA1, 0x60, 0xF8, 0x2A, 0x20, 0xAE, 0xF7)
    IID_IFileOpenDialog = _guid(0xD57C7288, 0xD4AD, 0x4768, 0xBE, 0x02, 0x9D, 0x96, 0x95, 0x32, 0xD9, 0x60)
    IID_IShellItem = _guid(0x43826D1E, 0xE718, 0x42EE, 0xBC, 0x55, 0xA1, 0xE2, 0x61, 0xC3, 0x7B, 0xFE)

    S_OK = 0
    HRESULT_CANCELLED = 0x800704C7
    CLSCTX_INPROC_SERVER = 1
    FOS_PICKFOLDERS = 0x00000020
    FOS_FORCEFILESYSTEM = 0x00000040
    SIGDN_FILESYSPATH = ctypes.c_int(-2147123200)

    ole32 = ctypes.windll.ole32
    shell32 = ctypes.windll.shell32

    def _com(obj, idx, *args):
        vtbl_ptr = ctypes.cast(
            ctypes.cast(obj, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p)
        )
        fn_addr = vtbl_ptr[idx]

        def _argtype(a):
            return ctypes.c_void_p if hasattr(a, "_obj") else type(a)

        arg_types = [ctypes.c_void_p] + [_argtype(a) for a in args]
        proto = ctypes.WINFUNCTYPE(ctypes.c_long, *arg_types)
        return proto(fn_addr)(obj, *args)

    co_hr = ole32.CoInitialize(None)
    _co_needs_uninit = co_hr in (S_OK, 1)

    dialog = ctypes.c_void_p()
    hr = ole32.CoCreateInstance(
        CLSID_FileOpenDialog,
        None,
        CLSCTX_INPROC_SERVER,
        IID_IFileOpenDialog,
        ctypes.byref(dialog),
    )
    if hr != S_OK or not dialog:
        if _co_needs_uninit:
            ole32.CoUninitialize()
        return None

    try:
        _com(dialog, 9, ctypes.c_uint(FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM))
        _init = Path(initial_dir)
        if _init.exists():
            shell_item = ctypes.c_void_p()
            hr2 = shell32.SHCreateItemFromParsingName(
                str(_init), None, IID_IShellItem, ctypes.byref(shell_item)
            )
            if hr2 == S_OK and shell_item:
                try:
                    _com(dialog, 12, shell_item)
                finally:
                    _com(shell_item, 2)

        hr3 = _com(dialog, 3, wt.HWND(0))
        hr3_u = hr3 & 0xFFFFFFFF
        if hr3_u == HRESULT_CANCELLED:
            return _CANCELLED
        if hr3_u != S_OK:
            return None

        result = ctypes.c_void_p()
        hr4 = _com(dialog, 20, ctypes.byref(result))
        if hr4 != S_OK or not result:
            return None

        try:
            pwstr = ctypes.c_wchar_p()
            hr5 = _com(result, 5, SIGDN_FILESYSPATH, ctypes.byref(pwstr))
            if hr5 != S_OK or not pwstr:
                return None
            chosen = pwstr.value
            ole32.CoTaskMemFree(pwstr)
            return str(Path(chosen).resolve()) if chosen else None
        finally:
            _com(result, 2)
    except Exception as exc:
        logger.debug("IFileOpenDialog error (non-fatal): %s", exc)
        return None
    finally:
        _com(dialog, 2)
        if _co_needs_uninit:
            ole32.CoUninitialize()


def _resolve_com_rename(stale_path):
    import time

    parent = stale_path.parent
    if not parent.is_dir():
        return None
    try:
        now = time.time()
        candidates = [
            d
            for d in parent.iterdir()
            if d.is_dir() and d.name != stale_path.name and (now - d.stat().st_ctime) < 30
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda d: d.stat().st_ctime)
    except OSError:
        return None


_DIALOG_PREWARMED = False


def _prewarm_file_dialog_win32() -> None:
    import sys as _sys

    global _DIALOG_PREWARMED
    if _DIALOG_PREWARMED or _sys.platform != "win32":
        return
    _DIALOG_PREWARMED = True
    try:
        import ctypes
        import struct

        def _guid(d32, d16a, d16b, *b8):
            raw = struct.pack("<IHH", d32, d16a, d16b) + bytes(b8)
            return (ctypes.c_byte * 16)(*raw)

        CLSID = _guid(0xDC1C5A9C, 0xE88A, 0x4DDE, 0xA5, 0xA1, 0x60, 0xF8, 0x2A, 0x20, 0xAE, 0xF7)
        IID = _guid(0xD57C7288, 0xD4AD, 0x4768, 0xBE, 0x02, 0x9D, 0x96, 0x95, 0x32, 0xD9, 0x60)
        S_OK = 0
        ole32 = ctypes.windll.ole32
        co_hr = ole32.CoInitialize(None)
        _needs_uninit = co_hr in (S_OK, 1)
        try:
            dialog = ctypes.c_void_p()
            hr = ole32.CoCreateInstance(CLSID, None, 1, IID, ctypes.byref(dialog))
            if hr == S_OK and dialog:
                vtbl = ctypes.cast(
                    ctypes.cast(dialog, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p)
                )
                ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(vtbl[2])(dialog)
            shell32 = ctypes.windll.shell32
            shell32.SHChangeNotify(ctypes.c_long(0x08000000), ctypes.c_uint(0x00001000), None, None)
        finally:
            if _needs_uninit:
                ole32.CoUninitialize()
    except Exception:
        pass


class GeneralPanel(_BasePanel):
    def __init__(self, master, app: "MainWindow") -> None:
        super().__init__(master, app)
        _prewarm_file_dialog_win32()
        self._build()

    def _build(self) -> None:
        cfg = self._app.config

        # -- Download location ---------------------------------------------
        sec_loc = self._collapsible_section(t("settings.section.location"), "gen_location", icon="📁")
        loc = self._card(container=sec_loc)

        row = QWidget()
        row.setStyleSheet("background: transparent;")
        hl = QHBoxLayout(row)
        hl.setContentsMargins(20, 14, 20, 14)
        self._dir_lbl = QLabel(str(cfg.download_dir))
        self._dir_lbl.setStyleSheet(f"color: {T.primary_text}; font-size: 13px; background: transparent;")
        hl.addWidget(self._dir_lbl, 1)
        self._browse_dir_btn = QPushButton(t("settings.browse"))
        self._browse_dir_btn.setFixedSize(100, 32)
        self._browse_dir_btn.setObjectName("primary")
        self._browse_dir_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._browse_dir_btn.clicked.connect(self._browse_dir)
        hl.addWidget(self._browse_dir_btn)
        loc.layout().addWidget(row)

        # -- Download behaviour --------------------------------------------
        sec_beh = self._collapsible_section(t("settings.section.behaviour"), "gen_behaviour", icon="⬇️")
        beh = self._card(container=sec_beh)

        self._slider_row(
            beh,
            t("settings.max_concurrent"),
            cfg.max_concurrent,
            1,
            8,
            lambda v: cfg.set("max_concurrent", int(v)),
        )
        self._hint(beh, t("settings.restart_hint"), T.warning_text)

        self._slider_row(
            beh, t("settings.max_retries"), cfg.max_retries, 0, 10, lambda v: cfg.set("max_retries", int(v))
        )

        self._switch_row(
            beh, t("settings.embed_thumbnail"), cfg.embed_thumbnail, lambda v: cfg.set("embed_thumbnail", v)
        )
        self._switch_row(
            beh, t("settings.embed_metadata"), cfg.embed_metadata, lambda v: cfg.set("embed_metadata", v)
        )

        # -- Appearance ----------------------------------------------------
        sec_app = self._collapsible_section(t("settings.section.appearance"), "gen_appearance", icon="🎨")
        app_card = self._card(container=sec_app)

        theme_row = QWidget()
        theme_row.setStyleSheet("background: transparent;")
        thl = QHBoxLayout(theme_row)
        thl.setContentsMargins(20, 14, 20, 14)
        theme_lbl = QLabel(t("settings.theme"))
        theme_lbl.setStyleSheet(f"color: {T.text}; font-size: 13px; background: transparent;")
        thl.addWidget(theme_lbl)
        thl.addStretch()
        self._theme_combo = QComboBox()
        self._theme_combo.addItems(THEME_NAMES)
        self._theme_combo.setFixedWidth(140)
        current_theme = self._app.config.theme
        if current_theme in THEME_NAMES:
            self._theme_combo.setCurrentText(current_theme)
        self._theme_combo.currentTextChanged.connect(self._change_theme)
        thl.addWidget(self._theme_combo)
        app_card.layout().addWidget(theme_row)

        lang_row = QWidget()
        lang_row.setStyleSheet("background: transparent;")
        lhl = QHBoxLayout(lang_row)
        lhl.setContentsMargins(20, 14, 20, 14)
        lang_lbl = QLabel(t("settings.language"))
        lang_lbl.setStyleSheet(f"color: {T.text}; font-size: 13px; background: transparent;")
        lhl.addWidget(lang_lbl)
        lhl.addStretch()
        self._lang_combo = QComboBox()
        self._lang_codes = list(LANGUAGES)
        for code in self._lang_codes:
            self._lang_combo.addItem(LANGUAGES[code], code)
        self._lang_combo.setFixedWidth(140)
        current_lang = self._app.config.language
        if current_lang in self._lang_codes:
            self._lang_combo.setCurrentIndex(self._lang_codes.index(current_lang))
        self._lang_combo.currentIndexChanged.connect(self._change_language)
        lhl.addWidget(self._lang_combo)
        app_card.layout().addWidget(lang_row)

        self._hint(app_card, t("settings.language_hint"))

        # -- Clipboard monitor ---------------------------------------------
        sec_clip = self._collapsible_section(t("settings.section.clipboard"), "gen_clipboard", icon="📋")
        clip = self._card(container=sec_clip)

        self._switch_row(
            clip,
            t("settings.clipboard_watch"),
            cfg.clipboard_monitor_enabled,
            self._on_clipboard_toggle,
        )
        self._hint(clip, t("settings.clipboard_hint"))

        # -- Developer -----------------------------------------------------
        sec_dev = self._collapsible_section(t("settings.section.developer"), "gen_developer", icon="🛠️")
        dev = self._card(container=sec_dev)

        self._switch_row(dev, t("settings.debug_logging"), cfg.debug_logging, self._on_debug_toggle)
        self._hint(dev, t("settings.debug_hint"))

        self._layout.addSpacing(20)

    # ── Handlers ──────────────────────────────────────────────────────────

    def _browse_dir(self) -> None:
        import sys
        import time
        from pathlib import Path

        current_dir = Path(str(self._app.config.download_dir))
        initialdir = str(current_dir) if current_dir.exists() else str(Path.home())

        chosen = None
        _from_com = False

        if sys.platform == "win32":
            try:
                result = _pick_folder_win32(initialdir)
            except Exception as exc:
                logger.debug("_pick_folder_win32 raised (fallback): %s", exc)
                result = None

            if result is _CANCELLED:
                return
            if result is not None:
                chosen = result
                _from_com = True

        if chosen is None:
            chosen = QFileDialog.getExistingDirectory(self, t("settings.select_folder"), initialdir)

        if not chosen:
            return

        chosen_path = Path(chosen).resolve()

        if _from_com:
            if not chosen_path.exists():
                time.sleep(0.1)

            if not chosen_path.exists():
                recovered = _resolve_com_rename(chosen_path)
                if recovered is None or not recovered.exists():
                    logger.warning("_browse_dir: COM path not found: %s", chosen_path)
                    self._app.toast(t("settings.folder_not_found"), "error")
                    return
                chosen_path = recovered
            else:
                try:
                    if time.time() - chosen_path.parent.stat().st_mtime < 1.5:
                        _rb_now = time.time()
                        _rb_candidates = [
                            d
                            for d in chosen_path.parent.iterdir()
                            if d.is_dir()
                            and d.name != chosen_path.name
                            and (_rb_now - d.stat().st_ctime) < 30
                        ]
                        if len(_rb_candidates) == 1 and _rb_candidates[0].exists():
                            chosen_path = _rb_candidates[0]
                except OSError:
                    pass
        else:
            try:
                chosen_path.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                logger.warning("Cannot create download dir %s: %s", chosen_path, exc)
                self._app.toast(t("settings.folder_create_failed", err=exc), "error")
                return

        chosen_str = str(chosen_path)
        self._app.config.set("download_dir", chosen_str)
        self._dir_lbl.setText(chosen_str)

    def _change_language(self, index: int) -> None:
        if not 0 <= index < len(self._lang_codes):
            return
        code = self._lang_codes[index]
        if code == self._app.config.language:
            return

        from utils.i18n import set_language

        self._app.config.set("language", code)
        # set_language() fires MainWindow's i18n listener, which queues
        # retranslate() onto the Qt event loop. Going through the listener (and
        # not calling retranslate() straight from this slot) matters: the
        # rebuild deletes the very combo box whose signal is running here.
        set_language(code)
        self._app.toast(t("settings.language_changed", language=LANGUAGES[code]))

    def _change_theme(self, theme: str) -> None:
        self._app.config.set("theme", theme)
        T.set_mode(theme)

    def _on_clipboard_toggle(self, enabled: bool) -> None:
        self._app.config.set("clipboard_monitor_enabled", enabled)
        if enabled:
            self._app.start_clipboard_monitor()
            self._app.toast(t("settings.clipboard_on"))
        else:
            self._app.stop_clipboard_monitor()
            self._app.toast(t("settings.clipboard_off"))

    def _on_debug_toggle(self, enabled: bool) -> None:
        self._app.config.set("debug_logging", enabled)
        from utils.logger import apply_debug_logging

        apply_debug_logging(enabled)
        msg = t("settings.debug_on") if enabled else t("settings.debug_off")
        logger.info("debug logging %s", "ON" if enabled else "OFF")
        if hasattr(self._app, "toast"):
            self._app.toast(msg)
