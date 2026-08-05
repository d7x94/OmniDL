"""Tests for ui/theme_qt.py — checkbox checkmark asset resolution.

Regression coverage for the checkmark never rendering: the QSS referenced
ui/assets/check.svg by a path relative to the process cwd (broken once cwd
!= repo root, e.g. any packaged build) and via a format needing the QtSvg
plugin (never bundled by PyInstaller since nothing imports it). The fix
registers ui/assets as a Qt search-path prefix instead of interpolating a
raw filesystem path into url(), which also sidesteps drive-letter/space
issues in absolute Windows paths.
"""

from __future__ import annotations

import os

from ui.theme_qt import _assets_dir, get_stylesheet


def test_assets_dir_points_at_the_real_checkmark_file():
    assert os.path.isfile(os.path.join(_assets_dir(), "check.png"))


def test_stylesheet_references_the_registered_search_path_not_a_raw_path():
    qss = get_stylesheet()
    assert "omnidl-assets:check.png" in qss
    assert "url(ui/assets" not in qss
