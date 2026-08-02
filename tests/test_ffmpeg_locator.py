"""
tests/test_ffmpeg_locator.py
Unit tests for utils/ffmpeg_locator.py

Requires only stdlib — compatible with both pytest and plain unittest.
Run with:  python3 -m unittest tests.test_ffmpeg_locator -v

Coverage matrix
───────────────
 1.  PyInstaller bundle — both binaries present                → SUCCESS
 2.  PyInstaller bundle — ffprobe missing                      → WARNING + degraded
 3.  PyInstaller bundle — directory entirely missing            → ERROR + fallthrough [L6]
 4.  Source-mode resources/ffmpeg/ — both present              → SUCCESS
 5.  Source-mode — resources/ absent                           → falls to system PATH
 6.  System PATH — ffmpeg + ffprobe in same directory          → SUCCESS
 7.  System PATH — ffmpeg and ffprobe in different directories → WARNING + result
 8.  System PATH — ffmpeg only, ffprobe nowhere                → WARNING + degraded
 9.  Nothing found anywhere                                    → None + ERROR
10.  Caching — lru_cache
filesystem probed at most once [L3]
11.  get_ffmpeg_path() — public wrapper
12.  get_ffmpeg_diagnostics() — correct label strings
13.  _find_binary() — .exe preferred
directories ignored
14.  _probe_directory() — symlink resolution [L5]
partial match [L1]
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from utils.ffmpeg_locator import (  # noqa: E402
    _find_binary,
    _probe_directory,
    get_ffmpeg_diagnostics,
    get_ffmpeg_path,
    locate_ffmpeg,
)

# ── Helpers ────────────────────────────────────────────────────────────────

def _make_ffmpeg_dir(parent: Path, *, exe: bool = False) -> Path:
    d = parent / "ffmpeg"
    d.mkdir(parents=True, exist_ok=True)
    suffix = ".exe" if exe else ""
    (d / f"ffmpeg{suffix}").touch()
    (d / f"ffprobe{suffix}").touch()
    return d


# ── Base ───────────────────────────────────────────────────────────────────

class LocatorTC(unittest.TestCase):

    def setUp(self):
        locate_ffmpeg.cache_clear()
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        locate_ffmpeg.cache_clear()
        self._td.cleanup()

    def _meipass(self, v):
        return patch.object(sys, "_MEIPASS", new=v, create=True)


# ── 1. Frozen bundle — both binaries ──────────────────────────────────────

class TestBundle(LocatorTC):

    def test_source_is_bundle(self):
        _make_ffmpeg_dir(self.tmp)
        with self._meipass(str(self.tmp)):
            r = locate_ffmpeg()
        self.assertEqual(r.source, "bundle")

    def test_directory_correct(self):
        fd = _make_ffmpeg_dir(self.tmp)
        with self._meipass(str(self.tmp)):
            r = locate_ffmpeg()
        self.assertEqual(Path(r.directory).resolve(), fd.resolve())

    def test_binary_paths_filled(self):
        _make_ffmpeg_dir(self.tmp)
        with self._meipass(str(self.tmp)):
            r = locate_ffmpeg()
        self.assertNotIn("<not found>", r.ffmpeg_bin)
        self.assertNotIn("<not found>", r.ffprobe_bin)

    def test_info_logged(self):
        _make_ffmpeg_dir(self.tmp)
        with self.assertLogs("utils.ffmpeg_locator", "INFO") as cm:
            with self._meipass(str(self.tmp)):
                locate_ffmpeg()
        self.assertTrue(any("Using bundled FFmpeg" in m for m in cm.output))

    def test_exe_binaries(self):
        _make_ffmpeg_dir(self.tmp, exe=True)
        with self._meipass(str(self.tmp)):
            r = locate_ffmpeg()
        self.assertIn(".exe", r.ffmpeg_bin)
        self.assertIn(".exe", r.ffprobe_bin)


# ── 2. Frozen bundle — ffprobe missing ────────────────────────────────────

class TestBundleFfprobeMissing(LocatorTC):

    def _setup(self):
        m = self.tmp / "m"
        m.mkdir()
        d = m / "ffmpeg"
        d.mkdir()
        (d / "ffmpeg").touch()
        return m

    def test_degraded_result(self):
        m = self._setup()
        with self._meipass(str(m)):
            with patch("shutil.which", return_value=None):
                with self.assertLogs("utils.ffmpeg_locator", "WARNING"):
                    r = locate_ffmpeg()
        self.assertEqual(r.ffprobe_bin, "<not found>")

    def test_warning_mentions_ffprobe(self):
        m = self._setup()
        with self._meipass(str(m)):
            with patch("shutil.which", return_value=None):
                with self.assertLogs("utils.ffmpeg_locator", "WARNING") as cm:
                    locate_ffmpeg()
        self.assertTrue(any("ffprobe" in m for m in cm.output))


# ── 3. Frozen bundle — dir absent → fallthrough [L6] ─────────────────────

class TestBundleAbsent(LocatorTC):

    def test_falls_through_to_system(self):
        """FIX [L6]: absent bundle must NOT return None immediately."""
        m = self.tmp / "empty_m"
        m.mkdir()
        d = self.tmp / "sys"
        d.mkdir()
        (d / "ffmpeg").touch()
        (d / "ffprobe").touch()

        with self._meipass(str(m)):
            with self.assertLogs("utils.ffmpeg_locator", "ERROR"):
                with patch("shutil.which", side_effect=lambda n: str(d / n)):
                    r = locate_ffmpeg()
        self.assertIsNotNone(r)
        self.assertEqual(r.source, "system")

    def test_error_logged(self):
        m = self.tmp / "empty_m2"
        m.mkdir()
        with self._meipass(str(m)):
            with patch("shutil.which", return_value=None):
                with self.assertLogs("utils.ffmpeg_locator", "ERROR") as cm:
                    locate_ffmpeg()
        self.assertTrue(any("FROZEN APP" in m for m in cm.output))


# ── 4. Source-mode — resources present ────────────────────────────────────

class TestSourceResources(LocatorTC):

    def _setup(self):
        f = self.tmp / "utils" / "ffmpeg_locator.py"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.touch()
        res = self.tmp / "resources" / "ffmpeg"
        res.mkdir(parents=True)
        (res / "ffmpeg").touch()
        (res / "ffprobe").touch()
        return f, res

    def test_source_is_resources(self):
        import utils.ffmpeg_locator as mod
        f, res = self._setup()
        with patch.object(mod, "__file__", str(f)):
            r = locate_ffmpeg()
        self.assertEqual(r.source, "resources")
        self.assertEqual(Path(r.directory).resolve(), res.resolve())

    def test_info_logged(self):
        import utils.ffmpeg_locator as mod
        f, _ = self._setup()
        with self.assertLogs("utils.ffmpeg_locator", "INFO") as cm:
            with patch.object(mod, "__file__", str(f)):
                locate_ffmpeg()
        self.assertTrue(any("source-mode" in m for m in cm.output))


# ── 5. Source-mode — no resources → system PATH ───────────────────────────

class TestSourceNoResources(LocatorTC):

    def test_falls_to_system(self):
        import utils.ffmpeg_locator as mod
        f = self.tmp / "utils" / "ffmpeg_locator.py"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.touch()
        d = self.tmp / "sys"
        d.mkdir()
        (d / "ffmpeg").touch()
        (d / "ffprobe").touch()
        with patch.object(mod, "__file__", str(f)):
            with patch("shutil.which", side_effect=lambda n: str(d / n)):
                r = locate_ffmpeg()
        self.assertEqual(r.source, "system")


# ── 6. System PATH — both in same dir ─────────────────────────────────────

class TestSystemBoth(LocatorTC):

    def _setup(self):
        d = self.tmp / "bin"
        d.mkdir()
        (d / "ffmpeg").touch()
        (d / "ffprobe").touch()
        return d

    def test_source_is_system(self):
        d = self._setup()
        with patch("shutil.which", side_effect=lambda n: str(d / n)):
            r = locate_ffmpeg()
        self.assertEqual(r.source, "system")
        self.assertEqual(Path(r.directory).resolve(), d.resolve())

    def test_info_logged(self):
        d = self._setup()
        with self.assertLogs("utils.ffmpeg_locator", "INFO") as cm:
            with patch("shutil.which", side_effect=lambda n: str(d / n)):
                locate_ffmpeg()
        self.assertTrue(any("Using system FFmpeg" in m for m in cm.output))

    def test_ffprobe_populated(self):
        d = self._setup()
        with patch("shutil.which", side_effect=lambda n: str(d / n)):
            r = locate_ffmpeg()
        self.assertNotEqual(r.ffprobe_bin, "<not found>")


# ── 7. System PATH — split directories ────────────────────────────────────

class TestSystemSplit(LocatorTC):

    def _run(self):
        a = self.tmp / "a"
        a.mkdir()
        b = self.tmp / "b"
        b.mkdir()
        (a / "ffmpeg").touch()
        (b / "ffprobe").touch()
        def w(n):
            return str(a / "ffmpeg") if n == "ffmpeg" else str(b / "ffprobe")
        with self.assertLogs("utils.ffmpeg_locator", "WARNING") as cm:
            with patch("shutil.which", side_effect=w):
                r = locate_ffmpeg()
        return r, cm

    def test_result_returned(self):
        r, _ = self._run()
        self.assertIsNotNone(r)

    def test_warning_different_directories(self):
        _, cm = self._run()
        self.assertTrue(any("different directories" in m for m in cm.output))

    def test_both_paths_set(self):
        r, _ = self._run()
        self.assertNotEqual(r.ffprobe_bin, "<not found>")


# ── 8. System PATH — ffmpeg only ──────────────────────────────────────────

class TestSystemFfmpegOnly(LocatorTC):

    def test_degraded_and_warning(self):
        d = self.tmp / "bin"
        d.mkdir()
        (d / "ffmpeg").touch()
        def w(n):
            return str(d / "ffmpeg") if n == "ffmpeg" else None
        with self.assertLogs("utils.ffmpeg_locator", "WARNING") as cm:
            with patch("shutil.which", side_effect=w):
                r = locate_ffmpeg()
        self.assertEqual(r.ffprobe_bin, "<not found>")
        self.assertTrue(any("ffprobe is NOT on PATH" in m for m in cm.output))


# ── 9. Nothing found ──────────────────────────────────────────────────────

class TestNothingFound(LocatorTC):

    def _isolate(self):
        import utils.ffmpeg_locator as mod
        f = self.tmp / "utils" / "ffmpeg_locator.py"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.touch()
        return patch.object(mod, "__file__", str(f))

    def test_returns_none(self):
        with self._isolate():
            with patch("shutil.which", return_value=None):
                with self.assertLogs("utils.ffmpeg_locator", "ERROR"):
                    r = locate_ffmpeg()
        self.assertIsNone(r)

    def test_error_is_actionable(self):
        with self._isolate():
            with patch("shutil.which", return_value=None):
                with self.assertLogs("utils.ffmpeg_locator", "ERROR") as cm:
                    locate_ffmpeg()
        msgs = "\n".join(cm.output).lower()
        self.assertIn("not found", msgs)
        self.assertTrue("install" in msgs or "rebuild" in msgs)


# ── 10. Caching [L3] ──────────────────────────────────────────────────────

class TestCaching(LocatorTC):

    def test_probed_once(self):
        d = self.tmp / "bin"
        d.mkdir()
        (d / "ffmpeg").touch()
        (d / "ffprobe").touch()
        calls: list = []

        def _w(n):
            calls.append(n)
            return str(d / n)

        with patch("shutil.which", side_effect=_w):
            r1 = locate_ffmpeg()
            r2 = locate_ffmpeg()
            r3 = locate_ffmpeg()

        self.assertEqual(len(calls), 2,
                         f"Expected 2 which() calls, got {len(calls)}: {calls}")
        self.assertIs(r1, r2)
        self.assertIs(r2, r3)

    def test_get_ffmpeg_path_shares_cache(self):
        d = self.tmp / "bin"
        d.mkdir()
        (d / "ffmpeg").touch()
        (d / "ffprobe").touch()
        calls: list = []

        def _w(n):
            calls.append(n)
            return str(d / n)

        with patch("shutil.which", side_effect=_w):
            get_ffmpeg_path()
            get_ffmpeg_path()
            locate_ffmpeg()
        self.assertEqual(len(calls), 2)


# ── 11. get_ffmpeg_path() ─────────────────────────────────────────────────

class TestGetFfmpegPath(LocatorTC):

    def test_returns_dir_string(self):
        d = self.tmp / "bin"
        d.mkdir()
        (d / "ffmpeg").touch()
        (d / "ffprobe").touch()
        with patch("shutil.which", side_effect=lambda n: str(d / n)):
            p = get_ffmpeg_path()
        self.assertIsInstance(p, str)
        self.assertTrue(Path(p).is_dir())

    def test_returns_none(self):
        import utils.ffmpeg_locator as mod
        f = self.tmp / "utils" / "ffmpeg_locator.py"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.touch()
        with patch.object(mod, "__file__", str(f)):
            with patch("shutil.which", return_value=None):
                with self.assertLogs("utils.ffmpeg_locator", "ERROR"):
                    p = get_ffmpeg_path()
        self.assertIsNone(p)


# ── 12. get_ffmpeg_diagnostics() ──────────────────────────────────────────

class TestDiagnostics(LocatorTC):

    def test_bundle_label(self):
        _make_ffmpeg_dir(self.tmp)
        with self._meipass(str(self.tmp)):
            d = get_ffmpeg_diagnostics()
        self.assertTrue("bundled" in d.lower() or "pyinstaller" in d.lower())

    def test_system_label(self):
        bd = self.tmp / "bin"
        bd.mkdir()
        (bd / "ffmpeg").touch()
        (bd / "ffprobe").touch()
        with patch("shutil.which", side_effect=lambda n: str(bd / n)):
            d = get_ffmpeg_diagnostics()
        self.assertIn("system PATH", d)

    def test_resources_label(self):
        import utils.ffmpeg_locator as mod
        f = self.tmp / "utils" / "ffmpeg_locator.py"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.touch()
        res = self.tmp / "resources" / "ffmpeg"
        res.mkdir(parents=True)
        (res / "ffmpeg").touch()
        (res / "ffprobe").touch()
        with patch.object(mod, "__file__", str(f)):
            d = get_ffmpeg_diagnostics()
        self.assertIn("resources", d.lower())

    def test_not_found_label(self):
        import utils.ffmpeg_locator as mod
        f = self.tmp / "utils" / "ffmpeg_locator.py"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.touch()
        with patch.object(mod, "__file__", str(f)):
            with patch("shutil.which", return_value=None):
                with self.assertLogs("utils.ffmpeg_locator", "ERROR"):
                    d = get_ffmpeg_diagnostics()
        self.assertIn("not found", d.lower())

    def test_includes_binary_paths(self):
        bd = self.tmp / "bin"
        bd.mkdir()
        (bd / "ffmpeg").touch()
        (bd / "ffprobe").touch()
        with patch("shutil.which", side_effect=lambda n: str(bd / n)):
            d = get_ffmpeg_diagnostics()
        self.assertIn("ffmpeg=", d)
        self.assertIn("ffprobe=", d)


# ── 13. _find_binary() ────────────────────────────────────────────────────

class TestFindBinary(LocatorTC):

    def test_prefers_exe(self):
        (self.tmp / "ffmpeg").touch()
        (self.tmp / "ffmpeg.exe").touch()
        r = _find_binary(self.tmp, ("ffmpeg.exe", "ffmpeg"))
        self.assertEqual(r.name, "ffmpeg.exe")

    def test_falls_back_to_bare(self):
        (self.tmp / "ffmpeg").touch()
        r = _find_binary(self.tmp, ("ffmpeg.exe", "ffmpeg"))
        self.assertEqual(r.name, "ffmpeg")

    def test_none_when_absent(self):
        self.assertIsNone(_find_binary(self.tmp, ("ffmpeg.exe", "ffmpeg")))

    def test_ignores_directories(self):
        (self.tmp / "ffmpeg").mkdir()
        self.assertIsNone(_find_binary(self.tmp, ("ffmpeg.exe", "ffmpeg")))


# ── 14. _probe_directory() ────────────────────────────────────────────────

class TestProbeDir(LocatorTC):

    def test_nonexistent(self):
        self.assertIsNone(_probe_directory(self.tmp / "ghost", "bundle"))

    def test_full_result(self):
        d = self.tmp / "d"
        d.mkdir()
        (d / "ffmpeg").touch()
        (d / "ffprobe").touch()
        r = _probe_directory(d, "bundle")
        self.assertIsNotNone(r)
        self.assertNotEqual(r.ffprobe_bin, "<not found>")

    def test_degraded_when_ffprobe_absent(self):
        d = self.tmp / "d"
        d.mkdir()
        (d / "ffmpeg").touch()
        with self.assertLogs("utils.ffmpeg_locator", "WARNING"):
            r = _probe_directory(d, "bundle")
        self.assertEqual(r.ffprobe_bin, "<not found>")

    def test_none_when_empty(self):
        d = self.tmp / "d"
        d.mkdir()
        self.assertIsNone(_probe_directory(d, "bundle"))

    @unittest.skipIf(
        __import__('sys').platform == 'win32'
        and not __import__('ctypes').windll.shell32.IsUserAnAdmin(),
        'Creating symlinks on Windows requires elevated privileges (Developer Mode or Admin)'
    )
    def test_resolves_symlinks(self):
        real = self.tmp / "real"
        real.mkdir()
        (real / "ffmpeg").touch()
        (real / "ffprobe").touch()
        link = self.tmp / "link"
        link.symlink_to(real)
        r = _probe_directory(link, "bundle")
        self.assertEqual(Path(r.directory), real.resolve())

    def test_source_preserved(self):
        d = self.tmp / "d"
        d.mkdir()
        (d / "ffmpeg").touch()
        (d / "ffprobe").touch()
        for src in ("bundle", "resources", "system"):
            r = _probe_directory(d, src)
            self.assertEqual(r.source, src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
