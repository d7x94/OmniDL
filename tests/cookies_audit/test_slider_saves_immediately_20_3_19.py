"""Settings slider (v20.3.19): every change is saved at once. A debounce timer owned by
the panel was lost when Settings rebuilt inside its 500 ms window, dropping the value."""

from types import SimpleNamespace

from PySide6.QtWidgets import QApplication, QFrame, QVBoxLayout

from ui.tabs.settings._base_panel import _BasePanel


def test_slider_change_saves_without_delay():
    QApplication.instance() or QApplication([])
    card = QFrame()
    card.setLayout(QVBoxLayout())
    saved = []
    slider = _BasePanel._slider_row(SimpleNamespace(), card, "x", 1, 1, 8, saved.append)
    slider.setValue(5)
    assert saved == [5]
