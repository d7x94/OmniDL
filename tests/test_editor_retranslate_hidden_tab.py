"""Editor panel toggles keep the right label when the tab is retranslated while hidden."""

from __future__ import annotations

from unittest.mock import MagicMock

from utils.i18n import t


def test_retranslate_on_hidden_tab_keeps_panel_labels():
    from ui.tabs.editor_tab import EditorTab

    fake = MagicMock(_in_ms=0, _out_ms=0)
    fake._right_scroll.isHidden.return_value = False
    fake._right_scroll.isVisible.return_value = False
    fake._effects_panel.isHidden.return_value = False
    fake._effects_panel.isVisible.return_value = False

    EditorTab.retranslate(fake)

    fake._toggle_ctrl_btn.setText.assert_any_call(t("editor.hide_panel"))
    fake._toggle_effects_btn.setText.assert_any_call(t("editor.hide"))
