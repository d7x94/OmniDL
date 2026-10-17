"""021026 audit (cluster 10): HomeTab.on_analysis_error's status label lives
inside _result_card, which on_analysis_error hides in favour of the welcome
screen -- so the error text was never actually visible. on_analysis_error
must also surface the error through a visible channel (toast)."""

import types


def test_on_analysis_error_shows_toast():
    from ui.tabs.home_tab import HomeTab

    ht = types.SimpleNamespace()
    ht._loading = types.SimpleNamespace(hide=lambda: None)
    ht._welcome = types.SimpleNamespace(show=lambda: None)
    statuses = []
    ht._set_status = lambda text, color: statuses.append((text, color))
    toasts = []
    ht._app = types.SimpleNamespace(toast=lambda msg, kind="info": toasts.append((msg, kind)))

    HomeTab.on_analysis_error(ht, "no media info found")

    assert statuses, "status label should still be updated"
    assert toasts == [("no media info found", "error")], (
        "on_analysis_error must toast the error since _status_lbl sits inside "
        "the hidden _result_card and is never visible on this path"
    )
