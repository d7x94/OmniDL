"""Tests for ui/components/notification_panel.py i18n wiring.

Title, Clear all and dismiss tooltip were hardcoded ASCII strings with no
t() call, so switching language left this panel untranslated.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from utils.i18n import get_language, set_language, t


def _app():
    if QApplication.instance() is None:
        try:
            QApplication([])
        except Exception:
            pytest.skip("Qt cannot create a QApplication in this environment")


def test_title_and_clear_all_use_i18n():
    _app()
    from ui.components.notification_panel import NotificationPanel

    panel = NotificationPanel()
    assert panel._title_lbl.text() == t("notification.title")
    assert panel._clear_btn.text() == t("notification.clear_all")


def test_retranslate_updates_text_after_language_switch():
    _app()
    from ui.components.notification_panel import NotificationPanel

    panel = NotificationPanel()
    original = get_language()
    set_language("en")
    try:
        panel.retranslate()
        assert panel._title_lbl.text() == t("notification.title")
        assert panel._clear_btn.text() == t("notification.clear_all")
        assert panel._title_lbl.text() == "NOTIFICATIONS"
    finally:
        set_language(original)
