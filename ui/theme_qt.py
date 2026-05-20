"""Qt theming: generates QSS from the token palette and applies it to QApplication."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

from ui.themes.tokens import T


def get_stylesheet() -> str:
    return _build_qss()


def apply_theme() -> None:
    app = QApplication.instance()
    if app:
        app.setStyleSheet(_build_qss())


def _build_qss() -> str:
    return f"""
QMainWindow, QDialog {{
    background-color: {T.bg};
}}
QWidget {{
    background-color: {T.bg};
    color: {T.text};
    font-family: "Segoe UI", "SF Pro Text", system-ui, -apple-system, sans-serif;
    font-size: 13px;
    font-weight: 400;
}}
QFrame {{
    background-color: transparent;
    border: none;
}}
/* --- Card frame --- */
QFrame#card {{
    background-color: {T.surface};
    border-radius: 14px;
    border: 1px solid {T.border};
}}
QFrame#card:hover {{
    border-color: {T.border2};
}}
/* --- Buttons --- */
QPushButton {{
    background-color: {T.surface2};
    color: {T.text2};
    border: none;
    border-radius: 8px;
    padding: 7px 18px;
    font-size: 13px;
    font-weight: 400;
    letter-spacing: 0.1px;
}}
QPushButton:hover {{
    background-color: {T.surface3};
    color: {T.text};
}}
QPushButton:pressed {{
    background-color: {T.border};
}}
QPushButton:disabled {{
    background-color: {T.surface};
    color: {T.text3};
}}
QPushButton#primary {{
    background-color: {T.primary};
    color: white;
    font-weight: 700;
    letter-spacing: 0.3px;
    border: 1px solid {T.primary_hover};
}}
QPushButton#primary:hover {{
    background-color: {T.primary_hover};
    border-color: {T.primary};
}}
QPushButton#primary:disabled {{
    background-color: {T.primary_dim};
    color: {T.text3};
    border-color: {T.border};
}}
QPushButton#danger {{
    background-color: {T.error};
    color: white;
    font-weight: 600;
}}
QPushButton#danger:hover {{
    background-color: {T.error};
}}
QPushButton#ghost {{
    background-color: transparent;
    color: {T.text3};
}}
QPushButton#ghost:hover {{
    background-color: {T.surface2};
    color: {T.text};
}}
/* --- Inputs --- */
QLineEdit {{
    background-color: {T.input};
    color: {T.text};
    border: 1px solid {T.border2};
    border-radius: 10px;
    padding: 9px 14px;
    min-height: 38px;
    selection-background-color: {T.primary};
    selection-color: white;
}}
QLineEdit:focus {{
    border: 2px solid {T.primary};
}}
QLineEdit:disabled {{
    background-color: {T.surface};
    color: {T.text3};
}}
QLineEdit::placeholder {{
    color: {T.text3};
}}
/* --- ComboBox --- */
QComboBox {{
    background-color: {T.input};
    color: {T.text};
    border: 1px solid {T.border2};
    border-radius: 10px;
    padding: 7px 14px;
    min-width: 80px;
}}
QComboBox:focus {{
    border: 1.5px solid {T.primary};
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
}}
QComboBox::down-arrow {{
    image: none;
    width: 0;
    height: 0;
    border: none;
}}
QComboBox QAbstractItemView {{
    background-color: {T.surface2};
    color: {T.text};
    border: 1px solid {T.border};
    border-radius: 10px;
    selection-background-color: {T.primary_dim};
    selection-color: {T.text};
    outline: none;
    padding: 4px;
}}
/* --- Scrollbars --- */
QScrollArea {{
    background-color: transparent;
    border: none;
}}
QScrollArea > QWidget > QWidget {{
    background-color: transparent;
}}
QScrollBar:vertical {{
    background-color: transparent;
    width: 7px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background-color: {T.scrollbar};
    border-radius: 3px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background-color: {T.scrollbar_hover};
}}
QScrollBar:horizontal {{
    background-color: transparent;
    height: 7px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background-color: {T.scrollbar};
    border-radius: 3px;
    min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{
    background-color: {T.scrollbar_hover};
}}
QScrollBar::add-line, QScrollBar::sub-line,
QScrollBar::add-page, QScrollBar::sub-page {{
    background: none;
    border: none;
    width: 0;
    height: 0;
}}
/* --- List Widget --- */
QListWidget {{
    background-color: transparent;
    border: none;
    outline: none;
    padding: 4px;
}}
QListWidget::item {{
    border-radius: 10px;
    padding: 7px 12px;
    color: {T.text3};
    margin: 1px 0;
}}
QListWidget::item:selected {{
    background-color: {T.primary_dim};
    color: {T.primary_text};
    border-left: 2px solid {T.primary};
    padding-left: 10px;
}}
QListWidget::item:hover:!selected {{
    background-color: {T.surface2};
}}
/* --- Labels --- */
QLabel {{
    background-color: transparent;
    color: {T.text};
}}
QLabel#section_title {{
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.8px;
    color: {T.text3};
    background: transparent;
}}
QLabel#page_title {{
    font-size: 22px;
    font-weight: 700;
    color: {T.text};
    background: transparent;
}}
/* --- Status Bar --- */
QStatusBar {{
    background-color: {T.statusbar};
    color: {T.text3};
    border-top: 1px solid {T.border};
    font-size: 11px;
    min-height: 28px;
    padding: 0 10px;
}}
QStatusBar QLabel {{
    color: {T.text3};
    font-size: 11px;
}}
/* --- Progress Bar --- */
QProgressBar {{
    background-color: {T.prog_track};
    border: none;
    border-radius: 3px;
    text-align: center;
    color: transparent;
    max-height: 5px;
}}
QProgressBar::chunk {{
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {T.prog_start}, stop:1 {T.prog_end});
    border-radius: 3px;
}}
/* --- Splitter --- */
QSplitter::handle {{
    background-color: {T.border};
}}
QSplitter::handle:vertical {{
    height: 1px;
}}
QSplitter::handle:horizontal {{
    width: 1px;
}}
/* --- CheckBox --- */
QCheckBox {{
    color: {T.text};
    spacing: 8px;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {T.border2};
    border-radius: 4px;
    background-color: {T.surface2};
}}
QCheckBox::indicator:checked {{
    background-color: {T.primary};
    border-color: {T.primary};
    image: url(ui/assets/check.svg);
}}
QCheckBox::indicator:hover {{
    border-color: {T.primary};
}}
/* --- SpinBox --- */
QSpinBox, QDoubleSpinBox {{
    background-color: {T.input};
    color: {T.text};
    border: 1px solid {T.border2};
    border-radius: 8px;
    padding: 6px 8px;
}}
QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 1.5px solid {T.primary};
}}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    background-color: {T.surface2};
    border: none;
    border-radius: 3px;
    width: 16px;
}}
/* --- Slider --- */
QSlider::groove:horizontal {{
    height: 3px;
    background-color: {T.border};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background-color: {T.primary};
    width: 14px;
    height: 14px;
    border-radius: 7px;
    margin: -6px 0;
}}
QSlider::sub-page:horizontal {{
    background-color: {T.primary};
    border-radius: 2px;
}}
/* --- Table --- */
QTableWidget {{
    background-color: {T.surface};
    color: {T.text};
    border: none;
    gridline-color: {T.divider};
    outline: none;
    selection-background-color: {T.primary_dim};
}}
QTableWidget::item {{
    padding: 6px 8px;
    border: none;
}}
QTableWidget::item:selected {{
    background-color: {T.primary_dim};
    color: {T.text};
}}
QHeaderView::section {{
    background-color: {T.surface2};
    color: {T.text2};
    border: none;
    border-bottom: 1px solid {T.border};
    padding: 6px 8px;
    font-weight: 600;
    font-size: 11px;
}}
/* --- Tab Widget --- */
QTabWidget::pane {{
    border: none;
    background-color: {T.bg};
}}
QTabBar::tab {{
    background-color: {T.surface};
    color: {T.text2};
    padding: 8px 16px;
    border: none;
    border-bottom: 2px solid transparent;
    font-size: 12px;
    font-weight: 500;
}}
QTabBar::tab:selected {{
    color: {T.primary};
    border-bottom: 2px solid {T.primary};
    background-color: {T.surface};
    font-weight: 600;
}}
QTabBar::tab:hover:!selected {{
    background-color: {T.surface2};
    color: {T.text};
}}
/* --- TextEdit --- */
QTextEdit, QPlainTextEdit {{
    background-color: {T.input};
    color: {T.text};
    border: 1px solid {T.border2};
    border-radius: 10px;
    padding: 8px;
    selection-background-color: {T.primary};
    selection-color: white;
}}
/* --- ToolTip --- */
QToolTip {{
    background-color: {T.surface3};
    color: {T.text};
    border: 1px solid {T.border2};
    padding: 5px 10px;
    border-radius: 8px;
    font-size: 11px;
}}
/* --- Message Box --- */
QMessageBox {{
    background-color: {T.surface};
}}
QMessageBox QLabel {{
    color: {T.text};
}}
/* --- Menu --- */
QMenu {{
    background-color: {T.surface2};
    color: {T.text};
    border: 1px solid {T.border};
    border-radius: 10px;
    padding: 4px;
}}
QMenu::item {{
    padding: 7px 14px;
    border-radius: 6px;
}}
QMenu::item:selected {{
    background-color: {T.primary_dim};
    color: {T.text};
}}
QMenu::separator {{
    height: 1px;
    background-color: {T.divider};
    margin: 4px 8px;
}}
/* --- Pill tab bar --- */
QWidget#pill_bar {{
    background: {T.surface};
    border-radius: 10px;
    padding: 4px;
}}
QPushButton#pill_tab {{
    background: transparent;
    color: {T.text2};
    border: none;
    border-radius: 7px;
    padding: 5px 12px;
    font-size: 12px;
    font-weight: 500;
    min-height: 28px;
}}
QPushButton#pill_tab[active="true"] {{
    background: #6366F1;
    color: white;
    font-weight: 700;
}}
QPushButton#pill_tab[active="true"][tab_key="home"]        {{ background: #6366F1; }}
QPushButton#pill_tab[active="true"][tab_key="queue"]       {{ background: #6366F1; }}
QPushButton#pill_tab[active="true"][tab_key="batch"]       {{ background: #818CF8; }}
QPushButton#pill_tab[active="true"][tab_key="live_monitor"]{{ background: #EF4444; }}
QPushButton#pill_tab[active="true"][tab_key="convert"]     {{ background: #14B8A6; }}
QPushButton#pill_tab[active="true"][tab_key="history"]     {{ background: #F59E0B; }}
QPushButton#pill_tab[active="true"][tab_key="settings"]    {{ background: #6B7A8E; }}
QPushButton#pill_tab[active="true"][tab_key="special_dl"]  {{ background: #A78BFA; }}
QPushButton#pill_tab[active="false"]:hover {{
    background: {T.surface2};
    color: {T.text};
}}
/* --- Badge --- */
QLabel#badge {{
    background: {T.primary};
    color: white;
    border-radius: 8px;
    padding: 1px 5px;
    font-size: 10px;
    font-weight: 700;
}}
QLabel#badge[type="error"] {{
    background: {T.error};
}}
/* --- Per-tab content frames --- */
/* Tab accent colors are intentionally hardcoded - they identify each tab
   visually and don't change with theme. See TAB_ACCENTS in tokens.py. */
QWidget#tab_content[tab_key="home"] {{ border-top: 3px solid #6366F1; }}
QWidget#tab_content[tab_key="queue"] {{ border-top: 3px solid #6366F1; }}
QWidget#tab_content[tab_key="batch"] {{ border-top: 3px solid #818CF8; }}
QWidget#tab_content[tab_key="live_monitor"] {{ border-top: 3px solid #EF4444; }}
QWidget#tab_content[tab_key="convert"] {{ border-top: 3px solid #14B8A6; }}
QWidget#tab_content[tab_key="history"] {{ border-top: 3px solid #F59E0B; }}
QWidget#tab_content[tab_key="settings"] {{ border-top: 3px solid #6B7A8E; }}
QWidget#tab_content[tab_key="special_dl"] {{ border-top: 3px solid #A78BFA; }}
/* --- Command palette --- */
QDialog#command_palette {{
    background: {T.surface};
    border: 1px solid {T.border2};
    border-radius: 10px;
}}
/* --- Notification panel --- */
QFrame#notification_panel {{
    background: {T.surface};
    border: 1px solid {T.border};
    border-radius: 10px;
}}
QFrame#notification_entry[type="success"] {{ border-left: 3px solid {T.success}; }}
QFrame#notification_entry[type="error"]   {{ border-left: 3px solid {T.error}; }}
QFrame#notification_entry[type="info"]    {{ border-left: 3px solid {T.primary}; }}
/* --- Mini status bar --- */
QFrame#mini_status {{
    background: {T.surface};
    border-top: 1px solid {T.border};
    border-radius: 0px;
}}
"""
