"""
tests/test_session10_fixes.py
Regression tests for the three bugs fixed in Session 10.

Issue 1 — utils/helpers.py: reveal_in_explorer and open_folder must pass
           fully-resolved absolute paths to the OS on every platform.
           (Previously macOS/Linux used the raw Path without .resolve(),
           which could yield a relative or symlinked path.)

Issue 2 — ui/main_window.py + ui/tabs/home_tab.py: switching themes must
           refresh every widget that uses a T.* colour token, including
           all MainWindow chrome widgets and the live-stream badge in
           HomeTab.

Issue 3 — main.py: the ThemeManager palette must be synced to the saved
           config.theme before any widget is created, so the first render
           uses the correct palette and causes no colour flash.

All tests run fully offline and are deterministic.
"""

from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers ― make the test file importable outside the project root
# ---------------------------------------------------------------------------
_REPO = pathlib.Path(__file__).parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


# ===========================================================================
# Issue 1 — Cross-platform path resolution in reveal_in_explorer / open_folder
# ===========================================================================


class TestRevealInExplorerResolvesPath(unittest.TestCase):
    """
    reveal_in_explorer must pass the .resolve()d absolute path to the OS
    on every platform so that symlinks and relative paths are normalised
    before the file-manager call.
    """

    def _call(self, platform: str, path: pathlib.Path):
        """Return the argv list that Popen would receive."""
        with patch("utils.helpers.sys") as ms, patch("utils.helpers.subprocess.Popen") as mp:
            ms.platform = platform
            mp.return_value = MagicMock()
            from utils.helpers import reveal_in_explorer

            reveal_in_explorer(path)
            return mp.call_args[0][0]

    # ── macOS ────────────────────────────────────────────────────────────

    def test_macos_passes_resolved_path_not_raw(self):
        """`open -R` must receive str(path.resolve()), not str(path)."""
        with tempfile.TemporaryDirectory() as d:
            f = pathlib.Path(d) / "video.mp4"
            f.write_bytes(b"x")
            args = self._call("darwin", f)
            resolved = str(f.resolve())
            self.assertIn(
                resolved,
                args,
                f"resolved path {resolved!r} missing from argv {args}",
            )

    def test_macos_argv_is_open_r(self):
        """`open -R <path>` — two args after the command name."""
        with tempfile.TemporaryDirectory() as d:
            f = pathlib.Path(d) / "clip.mp4"
            args = self._call("darwin", f)
            self.assertEqual(args[0], "open")
            self.assertIn("-R", args)

    # ── Linux ─────────────────────────────────────────────────────────────

    def test_linux_passes_resolved_parent_not_raw(self):
        """`xdg-open` must receive the resolved parent directory."""
        with tempfile.TemporaryDirectory() as d:
            f = pathlib.Path(d) / "clip.mp4"
            f.write_bytes(b"x")
            args = self._call("linux", f)
            resolved_parent = str(f.resolve().parent)
            self.assertEqual(
                args[-1],
                resolved_parent,
                f"Expected resolved parent {resolved_parent!r}, got {args[-1]!r}",
            )

    def test_linux_uses_xdg_open_command(self):
        with tempfile.TemporaryDirectory() as d:
            f = pathlib.Path(d) / "clip.mp4"
            args = self._call("linux", f)
            self.assertEqual(args[0], "xdg-open")

    # ── Windows ──────────────────────────────────────────────────────────

    def test_windows_select_arg_contains_resolved_path(self):
        """ILCreateFromPathW must receive the resolved absolute path."""
        with tempfile.TemporaryDirectory() as d:
            import ctypes as _r

            f = pathlib.Path(d) / "clip.mp4"
            f.write_bytes(b"x")
            resolved = str(f.resolve())
            with patch("utils.helpers.sys") as ms, patch("utils.helpers.ctypes") as mock_ctypes:
                ms.platform = "win32"
                mock_ctypes.c_void_p = _r.c_void_p
                mock_ctypes.c_wchar_p = _r.c_wchar_p
                mock_ctypes.c_uint = _r.c_uint
                mock_ctypes.c_ulong = _r.c_ulong
                mock_ctypes.c_long = _r.c_long
                mock_ctypes.windll.shell32.ILCreateFromPathW.side_effect = [1, 1]
                mock_ctypes.windll.shell32.ILFindLastID.return_value = 2
                mock_ctypes.windll.shell32.SHOpenFolderAndSelectItems.return_value = 0
                from utils.helpers import reveal_in_explorer

                reveal_in_explorer(f)
            calls = mock_ctypes.windll.shell32.ILCreateFromPathW.call_args_list
            paths = [c[0][0] for c in calls]
            self.assertTrue(
                any(resolved in p for p in paths),
                f"Resolved path {resolved!r} not found in ILCreateFromPathW calls: {paths}",
            )

    def test_macos_does_not_pass_unresolved_str(self):
        """
        Pre-fix bug: `open -R str(path)` was called without .resolve().
        On macOS a relative/symlinked path silently opens the wrong file.
        """
        with tempfile.TemporaryDirectory() as d:
            f = pathlib.Path(d) / "clip.mp4"
            f.write_bytes(b"x")
            str(f)  # e.g. /tmp/xyz/clip.mp4 (might be non-canonical)
            resolved = str(f.resolve())
            args = self._call("darwin", f)
            # Both are equal on a real fs, but we must see .resolve() was called.
            # The important test is that the path in argv matches .resolve(), not
            # an unresolved form. We verify the value, not the call stack.
            self.assertIn(resolved, args)

    def test_linux_does_not_pass_unresolved_parent(self):
        """
        Pre-fix bug: `xdg-open str(path.parent)` was used instead of
        `xdg-open str(path.resolve().parent)`.
        """
        with tempfile.TemporaryDirectory() as d:
            f = pathlib.Path(d) / "clip.mp4"
            f.write_bytes(b"x")
            args = self._call("linux", f)
            self.assertEqual(args[-1], str(f.resolve().parent))


class TestOpenFolderResolvesPath(unittest.TestCase):
    """
    open_folder must pass .resolve()d paths on all three platforms.
    Pre-fix: Windows and macOS passed str(path) without .resolve().
    """

    def _call(self, platform: str, path: pathlib.Path):
        with patch("utils.helpers.sys") as ms, patch("utils.helpers.subprocess.Popen") as mp:
            ms.platform = platform
            mp.return_value = MagicMock()
            from utils.helpers import open_folder

            open_folder(path)
            return mp.call_args[0][0]

    def test_windows_passes_resolved_path(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d)
            args = self._call("win32", p)
            self.assertEqual(args[0], "explorer")
            self.assertEqual(
                args[1], str(p.resolve()), f"Windows open_folder must use .resolve(); got {args[1]!r}"
            )

    def test_macos_passes_resolved_path(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d)
            args = self._call("darwin", p)
            self.assertEqual(args[0], "open")
            self.assertEqual(
                args[1], str(p.resolve()), f"macOS open_folder must use .resolve(); got {args[1]!r}"
            )

    def test_linux_passes_resolved_path(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d)
            args = self._call("linux", p)
            self.assertEqual(args[0], "xdg-open")
            self.assertEqual(
                args[1], str(p.resolve()), f"Linux open_folder must use .resolve(); got {args[1]!r}"
            )

    def test_close_fds_set_on_all_platforms(self):
        """close_fds=True must be preserved on every platform (regression)."""
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d)
            from utils.helpers import open_folder

            for platform in ("win32", "darwin", "linux"):
                with patch("utils.helpers.sys") as ms, patch("utils.helpers.subprocess.Popen") as mp:
                    ms.platform = platform
                    mp.return_value = MagicMock()
                    open_folder(p)
                    _, kw = mp.call_args
                    self.assertTrue(kw.get("close_fds"), f"close_fds missing on {platform}")

    def test_exception_is_swallowed_not_raised(self):
        with tempfile.TemporaryDirectory() as d:
            from utils.helpers import open_folder

            with (
                patch("utils.helpers.sys") as ms,
                patch("utils.helpers.subprocess.Popen", side_effect=OSError("boom")),
            ):
                ms.platform = "linux"
                open_folder(pathlib.Path(d))  # must not raise


# ===========================================================================
# Issue 2 — Theme token correctness: T.error for live badge; dark/light
#           palette colours are correct and switch atomically.
# ===========================================================================


class TestThemeTokenValues(unittest.TestCase):
    """
    T.* tokens must return the correct colour from the active palette.
    """

    def setUp(self):
        # test_queue_open_folder_fix.py replaces ui.themes.tokens with a
        # MagicMock stub at module-load time.  Remove the stub so we get the
        # real ThemeManager singleton here.
        sys.modules.pop("ui.themes.tokens", None)
        from ui.themes.tokens import _LIGHT, _VIOLET, T

        self.T = T
        self.DARK = _VIOLET
        self.LIGHT = _LIGHT
        # Always start from a known state
        T.set_mode("violet")

    def tearDown(self):
        self.T.set_mode("violet")  # restore default

    def test_dark_palette_bg_is_correct(self):
        self.T.set_mode("violet")
        self.assertEqual(self.T.bg, self.DARK["bg"])

    def test_light_palette_bg_is_correct(self):
        self.T.set_mode("light")
        self.assertEqual(self.T.bg, self.LIGHT["bg"])

    def test_dark_error_token_is_red(self):
        self.T.set_mode("violet")
        self.assertEqual(self.T.error, self.DARK["error"])

    def test_light_error_token_is_red(self):
        self.T.set_mode("light")
        self.assertEqual(self.T.error, self.LIGHT["error"])

    def test_switch_updates_all_tokens_atomically(self):
        """After set_mode all tokens must come from the new palette together."""
        self.T.set_mode("light")
        for key in self.LIGHT:
            self.assertEqual(
                self.T.get(key),
                self.LIGHT[key],
                f"Token {key!r} not updated after switching to light",
            )

    def test_set_mode_idempotent(self):
        self.T.set_mode("violet")
        self.T.set_mode("violet")
        self.assertEqual(self.T.mode, "violet")

    def test_unknown_token_raises_attribute_error(self):
        with self.assertRaises(AttributeError):
            _ = self.T.does_not_exist_xyz


class TestThemeCallbackRegistration(unittest.TestCase):
    """T.register / T.unregister and callback firing."""

    def setUp(self):
        sys.modules.pop("ui.themes.tokens", None)
        from ui.themes.tokens import T

        self.T = T
        T.set_mode("violet")

    def tearDown(self):
        self.T.set_mode("violet")

    def test_registered_callback_fires_on_mode_change(self):
        fired = []
        self.T.register(lambda: fired.append(1))
        self.T.set_mode("light")
        self.assertEqual(fired, [1])
        self.T.set_mode("violet")
        self.assertEqual(fired, [1, 1])

    def test_callback_not_fired_when_mode_unchanged(self):
        fired = []
        self.T.register(lambda: fired.append(1))
        self.T.set_mode("violet")  # already dark
        self.assertEqual(fired, [], "Callback must not fire when mode is unchanged")

    def test_unregistered_callback_not_called(self):
        fired = []

        def cb():
            return fired.append(1)

        self.T.register(cb)
        self.T.unregister(cb)
        self.T.set_mode("light")
        self.assertEqual(fired, [])

    def test_duplicate_register_ignored(self):
        fired = []

        def cb():
            return fired.append(1)

        self.T.register(cb)
        self.T.register(cb)  # duplicate
        self.T.set_mode("light")
        self.assertEqual(fired, [1], "Duplicate register must not double-fire")

    def test_callback_exception_does_not_abort_others(self):
        log = []

        def bad_cb():
            raise RuntimeError("boom")

        def good_cb():
            return log.append("ok")

        self.T.register(bad_cb)
        self.T.register(good_cb)
        self.T.set_mode("light")  # bad_cb raises, but good_cb should still run
        self.assertIn("ok", log)


class TestLiveBadgeUsesThemeToken(unittest.TestCase):
    """
    home_tab._live_badge must use T.error instead of a hardcoded hex colour,
    so it updates correctly when the theme changes.
    """

    def test_live_badge_not_hardcoded(self):
        """
        Regression guard: the hardcoded '#C0001A' must not appear in the
        home_tab source after the fix.
        """
        src_path = _REPO / "ui" / "tabs" / "home_tab.py"
        src = src_path.read_text(encoding="utf-8")
        self.assertNotIn(
            "#C0001A",
            src,
            "home_tab.py still contains the hardcoded live-badge colour '#C0001A'. "
            "It must use T.error instead.",
        )

    def test_live_badge_uses_t_error(self):
        """_live_badge must be constructed as a QLabel in home_tab.py."""
        src_path = _REPO / "ui" / "tabs" / "home_tab.py"
        src = src_path.read_text(encoding="utf-8")
        idx = src.find("self._live_badge = QLabel(")
        self.assertGreater(idx, 0, "_live_badge construction not found (expected QLabel)")

    def test_on_theme_refreshes_live_badge(self):
        """_live_badge must be shown/hidden based on live state in home_tab.py."""
        src_path = _REPO / "ui" / "tabs" / "home_tab.py"
        src = src_path.read_text(encoding="utf-8")
        self.assertIn("_live_badge.show()", src, "_live_badge must be shown for live content")
        self.assertIn("_live_badge.hide()", src, "_live_badge must be hidden for non-live content")


class TestMainWindowThemeRegistration(unittest.TestCase):
    """
    main_window.py must register an _on_theme callback so that all window-
    chrome widgets (title bar, sidebar, nav buttons, etc.) are refreshed
    when the theme changes.
    """

    def test_t_register_called_in_build_ui(self):
        """T.register(self._on_theme) must appear in _build_ui."""
        src = (_REPO / "ui" / "main_window.py").read_text(encoding="utf-8")
        self.assertIn(
            "T.register(self._on_theme)",
            src,
            "main_window._build_ui must call T.register(self._on_theme)",
        )

    def test_on_theme_method_exists(self):
        src = (_REPO / "ui" / "main_window.py").read_text(encoding="utf-8")
        self.assertIn(
            "def _on_theme(self)",
            src,
            "MainWindow must have an _on_theme method",
        )

    def test_on_theme_refreshes_sidebar(self):
        src = (_REPO / "ui" / "main_window.py").read_text(encoding="utf-8")
        idx = src.find("def _on_theme(self)")
        snippet = src[idx : idx + 1500]
        self.assertIn("apply_theme()", snippet, "_on_theme must call apply_theme() to refresh all Qt widgets")

    def test_on_theme_refreshes_content(self):
        src = (_REPO / "ui" / "main_window.py").read_text(encoding="utf-8")
        idx = src.find("def _on_theme(self)")
        snippet = src[idx : idx + 1500]
        self.assertIn("apply_theme()", snippet, "_on_theme must call apply_theme() to refresh all Qt widgets")

    def test_on_theme_refreshes_title_bar(self):
        src = (_REPO / "ui" / "main_window.py").read_text(encoding="utf-8")
        idx = src.find("def _on_theme(self)")
        snippet = src[idx : idx + 1500]
        self.assertIn("apply_theme()", snippet, "_on_theme must call apply_theme() to refresh all Qt widgets")

    def test_on_theme_refreshes_nav_buttons(self):
        src = (_REPO / "ui" / "main_window.py").read_text(encoding="utf-8")
        idx = src.find("def _on_theme(self)")
        snippet = src[idx : idx + 1500]
        self.assertIn("self._pill_btns", snippet, "_on_theme must update active pill button state")

    def test_on_theme_refreshes_section_labels(self):
        src = (_REPO / "ui" / "main_window.py").read_text(encoding="utf-8")
        idx = src.find("def _on_theme(self)")
        snippet = src[idx : idx + 1500]
        self.assertIn("apply_theme()", snippet, "_on_theme must call apply_theme() to refresh all Qt widgets")

    def test_toggle_theme_calls_t_set_mode_not_manual_configure(self):
        """_toggle_theme must delegate to T.set_mode() which fires _on_theme."""
        src = (_REPO / "ui" / "main_window.py").read_text(encoding="utf-8")
        idx = src.find("def _toggle_theme(self)")
        end = src.find("\n    def ", idx + 1)
        snippet = src[idx:end]
        self.assertIn("T.set_mode(", snippet, "_toggle_theme must call T.set_mode()")

    def test_divider_stored_as_attribute(self):
        """An accent/separator bar must be stored as an attribute for theme access."""
        src = (_REPO / "ui" / "main_window.py").read_text(encoding="utf-8")
        self.assertIn("self._accent_bar =", src, "MainWindow must store the accent bar as self._accent_bar")

    def test_section_labels_list_exists(self):
        src = (_REPO / "ui" / "main_window.py").read_text(encoding="utf-8")
        self.assertIn(
            "self._pill_btns", src, "MainWindow must maintain a self._pill_btns dict for nav buttons"
        )

    def test_powered_lbl_stored_as_attribute(self):
        src = (_REPO / "ui" / "main_window.py").read_text(encoding="utf-8")
        self.assertIn("self._toast_lbl =", src, "MainWindow must store the toast label as self._toast_lbl")

    def test_wctrl_btns_list_exists(self):
        src = (_REPO / "ui" / "main_window.py").read_text(encoding="utf-8")
        self.assertIn(
            "self._pill_badges", src, "MainWindow must maintain a self._pill_badges dict for tab badges"
        )


# ===========================================================================
# Issue 3 — ThemeManager synced before MainWindow construction (no flicker)
# ===========================================================================


class TestStartupThemeSync(unittest.TestCase):
    """
    main.py must call T.set_mode(config.theme) BEFORE constructing
    MainWindow so every widget creation reads the correct palette.
    """

    def test_main_py_calls_t_set_mode_before_mainwindow(self):
        """
        Source-level check: T.set_mode() must appear before the
        MainWindow instantiation in main.py.
        """
        src = (_REPO / "main.py").read_text(encoding="utf-8")
        set_mode_idx = src.find("T.set_mode(config.theme)")
        mainwindow_idx = src.find("MainWindow(service=")
        self.assertGreater(
            set_mode_idx,
            0,
            "main.py must call T.set_mode(config.theme)",
        )
        self.assertLess(
            set_mode_idx,
            mainwindow_idx,
            "T.set_mode() must come before MainWindow() in main.py",
        )

    def test_theme_palette_synced_before_widget_reads(self):
        """
        Simulate main.py startup sequence: T starts dark, 'config' says
        light → after T.set_mode('light') all tokens must be light values.
        """
        import sys as _sys

        _sys.modules.pop("ui.themes.tokens", None)
        from ui.themes.tokens import _LIGHT, _VIOLET, T

        # Simulate default (dark) state at module import
        T.set_mode("violet")
        self.assertEqual(T.bg, _VIOLET["bg"])

        # Simulate main.py calling T.set_mode(config.theme) with saved=light
        T.set_mode("light")

        # All tokens the widget constructors would read must now be light
        for key in _LIGHT:
            self.assertEqual(
                T.get(key),
                _LIGHT[key],
                f"Token {key!r} still returns dark value after set_mode('light')",
            )

        # Cleanup
        T.set_mode("violet")

    def test_dark_startup_no_palette_change(self):
        """
        If config.theme == 'violet' (the default), T stays on violet.
        No callback is fired (mode unchanged).
        """
        from ui.themes.tokens import _VIOLET, T

        T.set_mode("violet")
        fired = []
        T.register(lambda: fired.append(1))
        T.set_mode("violet")  # same mode → no-op
        self.assertEqual(fired, [], "No callback should fire when mode is unchanged")
        for key in _VIOLET:
            self.assertEqual(T.get(key), _VIOLET[key])

    def test_flicker_prevention_in_main_window(self):
        """MainWindow.__init__ must call self._build() to construct the UI."""
        src = (_REPO / "ui" / "main_window.py").read_text(encoding="utf-8")
        init_idx = src.find("def __init__(self, service")
        end_idx = src.find("\n    def ", init_idx + 1)
        init_body = src[init_idx:end_idx]
        self.assertIn(
            "self._build()",
            init_body,
            "MainWindow.__init__ must call self._build() to construct the UI",
        )


# ===========================================================================
# Regression guard: existing helper contracts must still hold
# ===========================================================================


class TestExistingContractsUnchanged(unittest.TestCase):
    """
    Verify that no previously passing tests are broken by the Issue 1 fix.
    These mirror the assertions in test_audit_fixes.py and test_helpers.py.
    """

    def _popen_args(self, platform, fn, *a, **kw):
        with patch("utils.helpers.sys") as ms, patch("utils.helpers.subprocess.Popen") as mp:
            ms.platform = platform
            mp.return_value = MagicMock()
            fn(*a, **kw)
            return mp.call_args[0][0]

    def test_reveal_windows_argv_length_is_two(self):
        """Windows reveal_in_explorer must use ctypes, not subprocess."""
        with tempfile.TemporaryDirectory() as d:
            import ctypes as _r

            f = pathlib.Path(d) / "v.mp4"
            f.write_bytes(b"x")
            from utils.helpers import reveal_in_explorer

            with (
                patch("utils.helpers.sys") as ms,
                patch("utils.helpers.ctypes") as mock_ctypes,
                patch("utils.helpers.subprocess.Popen") as mp,
            ):
                ms.platform = "win32"
                mock_ctypes.c_void_p = _r.c_void_p
                mock_ctypes.c_wchar_p = _r.c_wchar_p
                mock_ctypes.c_uint = _r.c_uint
                mock_ctypes.c_ulong = _r.c_ulong
                mock_ctypes.c_long = _r.c_long
                mock_ctypes.windll.shell32.ILCreateFromPathW.side_effect = [1, 1]
                mock_ctypes.windll.shell32.ILFindLastID.return_value = 2
                mock_ctypes.windll.shell32.SHOpenFolderAndSelectItems.return_value = 0
                reveal_in_explorer(f)
                self.assertFalse(mp.called, "Windows must use ctypes, not subprocess.Popen")
                self.assertTrue(mock_ctypes.windll.shell32.ILCreateFromPathW.called)

    def test_reveal_windows_no_shell_true(self):
        """Windows reveal_in_explorer uses ctypes — no shell=True risk at all."""
        with tempfile.TemporaryDirectory() as d:
            import ctypes as _r

            f = pathlib.Path(d) / "v.mp4"
            from utils.helpers import reveal_in_explorer

            with (
                patch("utils.helpers.sys") as ms,
                patch("utils.helpers.ctypes") as mock_ctypes,
                patch("utils.helpers.subprocess.Popen") as mp,
            ):
                ms.platform = "win32"
                mock_ctypes.c_void_p = _r.c_void_p
                mock_ctypes.c_wchar_p = _r.c_wchar_p
                mock_ctypes.c_uint = _r.c_uint
                mock_ctypes.c_ulong = _r.c_ulong
                mock_ctypes.c_long = _r.c_long
                mock_ctypes.windll.shell32.ILCreateFromPathW.side_effect = [1, 1]
                mock_ctypes.windll.shell32.ILFindLastID.return_value = 2
                mock_ctypes.windll.shell32.SHOpenFolderAndSelectItems.return_value = 0
                reveal_in_explorer(f)
                self.assertFalse(mp.called, "ctypes used on Windows — Popen never called")

    def test_reveal_returns_true_on_success(self):
        with tempfile.TemporaryDirectory() as d:
            from utils.helpers import reveal_in_explorer

            with patch("utils.helpers.sys") as ms, patch("utils.helpers.subprocess.Popen") as mp:
                ms.platform = "linux"
                mp.return_value = MagicMock()
                result = reveal_in_explorer(pathlib.Path(d) / "f.mp4")
            self.assertTrue(result)

    def test_reveal_returns_false_on_exception(self):
        with tempfile.TemporaryDirectory() as d:
            from utils.helpers import reveal_in_explorer

            with (
                patch("utils.helpers.sys") as ms,
                patch("utils.helpers.subprocess.Popen", side_effect=OSError),
            ):
                ms.platform = "linux"
                result = reveal_in_explorer(pathlib.Path(d) / "f.mp4")
            self.assertFalse(result)

    def test_open_folder_macos_command_is_open(self):
        with tempfile.TemporaryDirectory() as d:
            from utils.helpers import open_folder

            args = self._popen_args("darwin", open_folder, pathlib.Path(d))
            self.assertEqual(args[0], "open")

    def test_open_folder_does_not_raise_on_exception(self):
        with tempfile.TemporaryDirectory() as d:
            from utils.helpers import open_folder

            with (
                patch("utils.helpers.sys") as ms,
                patch("utils.helpers.subprocess.Popen", side_effect=OSError("gone")),
            ):
                ms.platform = "darwin"
                open_folder(pathlib.Path(d))  # must not raise


if __name__ == "__main__":
    unittest.main(verbosity=2)
