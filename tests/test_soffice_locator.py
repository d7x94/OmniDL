"""
tests/test_soffice_locator.py
Unit tests for utils/soffice_locator.py.

LibreOffice is not installed on CI, so every path is driven with a stubbed
shutil.which / Path.is_file rather than a real binary.
"""

from __future__ import annotations

import pytest

import utils.soffice_locator as loc


@pytest.fixture(autouse=True)
def _clear_cache():
    loc.reset_soffice_cache()
    yield
    loc.reset_soffice_cache()


class TestPathLookup:
    def test_found_on_path(self, monkeypatch, tmp_path):
        binary = tmp_path / "soffice"
        binary.write_text("#!/bin/sh\n", encoding="utf-8")
        monkeypatch.setattr(loc.shutil, "which", lambda n: str(binary) if n == "soffice" else None)
        assert loc.locate_soffice() == binary.resolve()
        assert loc.has_soffice() is True

    def test_falls_back_to_the_libreoffice_name(self, monkeypatch, tmp_path):
        binary = tmp_path / "libreoffice"
        binary.write_text("#!/bin/sh\n", encoding="utf-8")
        monkeypatch.setattr(loc.shutil, "which", lambda n: str(binary) if n == "libreoffice" else None)
        assert loc.locate_soffice() == binary.resolve()

    def test_absent_everywhere_returns_none(self, monkeypatch):
        monkeypatch.setattr(loc.shutil, "which", lambda _n: None)
        monkeypatch.setattr(loc, "_platform_candidates", lambda: ())
        assert loc.locate_soffice() is None
        assert loc.has_soffice() is False


class TestWellKnownLocations:
    def test_probed_when_not_on_path(self, monkeypatch, tmp_path):
        binary = tmp_path / "program" / "soffice"
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\n", encoding="utf-8")
        monkeypatch.setattr(loc.shutil, "which", lambda _n: None)
        monkeypatch.setattr(loc, "_platform_candidates", lambda: (str(binary),))
        assert loc.locate_soffice() == binary.resolve()

    def test_nonexistent_candidates_are_skipped(self, monkeypatch, tmp_path):
        real = tmp_path / "real-soffice"
        real.write_text("x", encoding="utf-8")
        monkeypatch.setattr(loc.shutil, "which", lambda _n: None)
        monkeypatch.setattr(loc, "_platform_candidates", lambda: (str(tmp_path / "gone"), str(real)))
        assert loc.locate_soffice() == real.resolve()

    @pytest.mark.parametrize(
        "platform,expected_fragment",
        [("win32", "LibreOffice"), ("darwin", "LibreOffice.app"), ("linux", "/usr/bin/")],
    )
    def test_candidate_list_is_per_platform(self, monkeypatch, platform, expected_fragment):
        monkeypatch.setattr(loc.sys, "platform", platform)
        candidates = loc._platform_candidates()
        assert any(expected_fragment in c for c in candidates)


class TestCaching:
    def test_result_is_cached(self, monkeypatch, tmp_path):
        calls: list[str] = []
        binary = tmp_path / "soffice"
        binary.write_text("x", encoding="utf-8")

        def _which(name):
            calls.append(name)
            return str(binary) if name == "soffice" else None

        monkeypatch.setattr(loc.shutil, "which", _which)
        loc.locate_soffice()
        loc.locate_soffice()
        assert calls == ["soffice"]  # second call served from the cache

    def test_reset_makes_a_fresh_install_visible(self, monkeypatch, tmp_path):
        """A user can install LibreOffice without restarting OmniDL."""
        binary = tmp_path / "soffice"
        state = {"installed": False}

        def _which(name):
            if name == "soffice" and state["installed"]:
                return str(binary)
            return None

        monkeypatch.setattr(loc.shutil, "which", _which)
        monkeypatch.setattr(loc, "_platform_candidates", lambda: ())
        assert loc.locate_soffice() is None

        state["installed"] = True
        binary.write_text("x", encoding="utf-8")
        assert loc.locate_soffice() is None, "still cached until reset"
        loc.reset_soffice_cache()
        assert loc.locate_soffice() == binary.resolve()
