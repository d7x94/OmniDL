"""Settings panel: Network & Authentication · Global Cookie · Per-Platform Cookies (PySide6)."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from infrastructure.downloader.cookie_storage import invalidate_cookie_cache
from ui.signals import ui_bridge
from ui.tabs.settings._base_panel import _BasePanel
from ui.themes.tokens import T
from utils.i18n import t

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = __import__("logging").getLogger(__name__)


def _is_netscape_cookie_file(path: Path) -> bool:
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            first = fh.readline()
        return "Netscape HTTP Cookie File" in first
    except OSError:
        return False


# Browsers whose cookies can be read through the Chrome DevTools Protocol.
# Firefox/Opera are not Chromium and must go through the yt-dlp path.
_CDP_BROWSERS = ("brave", "chrome", "chromium", "edge")


def _browser_family_supported_by_cdp(browser: str) -> bool:
    b = (browser or "").lower()
    return any(fam in b for fam in _CDP_BROWSERS)


def _cookie_file_candidates(path_str: str) -> list[Path]:
    p = Path(path_str)
    candidates = [p]
    if p.suffix == ".txt":
        candidates.append(p.with_suffix(".enc"))
    elif p.suffix == ".enc":
        candidates.append(p.with_suffix(".txt"))
    return candidates


_PC_PLATFORMS = [
    ("youtube", "YouTube"),
    ("instagram", "Instagram"),
    ("tiktok", "TikTok"),
    ("facebook", "Facebook"),
    ("twitter", "Twitter / X"),
    ("threads", "Threads"),
    ("kuaishou", "Kuaishou"),
    ("ok_ru", "OK.ru"),
]


_PLATFORM_NAMES = dict(_PC_PLATFORMS)


def _INPUT_SS(border_color=""):
    return (
        f"QLineEdit {{ background: {T.input}; color: {T.text}; border: 1px solid "
        f"{border_color or T.border2}; border-radius: 8px; padding: 6px 12px; }}"
    )


class NetworkPanel(_BasePanel):
    def __init__(self, master, app: "MainWindow") -> None:
        super().__init__(master, app)
        self._build()

    def _build(self) -> None:
        cfg = self._app.config

        # -- Network & Auth ------------------------------------------------
        sec_net = self._collapsible_section(t("settings.network.section.auth"), "net_auth", icon="🔒")

        # Proxy card
        proxy_card = self._card(container=sec_net)
        proxy_row = QWidget()
        proxy_row.setStyleSheet("background: transparent;")
        phl = QHBoxLayout(proxy_row)
        phl.setContentsMargins(20, 14, 20, 14)
        proxy_lbl = QLabel(t("settings.network.proxy_label"))
        proxy_lbl.setStyleSheet(f"color: {T.text}; font-size: 13px; background: transparent;")
        phl.addWidget(proxy_lbl)
        phl.addStretch()
        self._proxy_entry = QLineEdit()
        self._proxy_entry.setFixedSize(260, 32)
        self._proxy_entry.setStyleSheet(_INPUT_SS())
        self._proxy_entry.setPlaceholderText("http://host:port")
        self._proxy_entry.setText(cfg.proxy)
        self._proxy_entry.editingFinished.connect(self._on_proxy_focusout)
        phl.addWidget(self._proxy_entry)
        proxy_card.layout().addWidget(proxy_row)

        # Cookie card
        cookie_card = self._card(container=sec_net)

        # Row: browser selector + use-cookies toggle
        br_row = QWidget()
        br_row.setStyleSheet("background: transparent;")
        brhl = QHBoxLayout(br_row)
        brhl.setContentsMargins(20, 14, 20, 12)
        brhl.setSpacing(10)
        br_lbl = QLabel(t("settings.network.browser_label"))
        br_lbl.setStyleSheet(f"color: {T.text}; font-size: 13px; background: transparent;")
        brhl.addWidget(br_lbl)
        self._browser_combo = QComboBox()
        self._browser_combo.addItems(["chrome", "firefox", "safari", "edge", "opera", "brave"])
        self._browser_combo.setCurrentText(cfg.cookies_browser)
        self._browser_combo.setFixedWidth(120)
        self._browser_combo.currentTextChanged.connect(lambda v: cfg.set("cookies_browser", v))
        brhl.addWidget(self._browser_combo)
        brhl.addStretch()
        use_lbl = QLabel(t("settings.network.use_cookies_label"))
        use_lbl.setStyleSheet(f"color: {T.text2}; font-size: 12px; background: transparent;")
        brhl.addWidget(use_lbl)
        self._use_cookies_sw = QCheckBox()
        self._use_cookies_sw.setChecked(cfg.use_cookies)
        self._use_cookies_sw.clicked.connect(lambda v: cfg.set("use_cookies", v))
        brhl.addWidget(self._use_cookies_sw)
        cookie_card.layout().addWidget(br_row)

        self._separator(cookie_card)

        # Sub-heading: auto extract
        sh1 = QWidget()
        sh1.setStyleSheet("background: transparent;")
        sh1l = QHBoxLayout(sh1)
        sh1l.setContentsMargins(20, 10, 20, 6)
        sh1_lbl = QLabel(t("settings.network.section.auto_extract"))
        sh1_lbl.setStyleSheet(
            f"color: {T.text3}; font-size: 10px; font-weight: 600; letter-spacing: 0.8px; background: transparent;"
        )
        sh1l.addWidget(sh1_lbl)
        sh1l.addStretch()
        cookie_card.layout().addWidget(sh1)

        ext_row = QWidget()
        ext_row.setStyleSheet("background: transparent;")
        ehl = QHBoxLayout(ext_row)
        ehl.setContentsMargins(20, 0, 20, 6)
        ehl.setSpacing(8)
        self._extract_global_btn = QPushButton(t("settings.network.extract_global_btn"))
        self._extract_global_btn.setFixedHeight(30)
        self._extract_global_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._extract_global_btn.setStyleSheet(
            f"background: {T.surface3}; color: {T.text2}; border-radius: 8px; border: none; font-size: 12px; padding: 0 12px;"
        )
        self._extract_global_btn.clicked.connect(self._extract_global_cookies)
        ehl.addWidget(self._extract_global_btn)
        self._extract_cdp_btn = QPushButton(t("settings.network.extract_cdp_btn"))
        self._extract_cdp_btn.setFixedHeight(30)
        self._extract_cdp_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._extract_cdp_btn.setStyleSheet(
            f"background: {T.surface3}; color: {T.text2}; border-radius: 8px; border: none; font-size: 12px; padding: 0 12px;"
        )
        self._extract_cdp_btn.clicked.connect(self._extract_global_cdp)
        ehl.addWidget(self._extract_cdp_btn)
        self._extract_global_status = QLabel("")
        self._extract_global_status.setStyleSheet(
            f"color: {T.text2}; font-size: 11px; background: transparent;"
        )
        ehl.addWidget(self._extract_global_status)
        ehl.addStretch()
        cookie_card.layout().addWidget(ext_row)

        hint_row = QWidget()
        hint_row.setStyleSheet("background: transparent;")
        hhl = QHBoxLayout(hint_row)
        hhl.setContentsMargins(20, 0, 20, 10)
        h_lbl = QLabel(t("settings.network.extract_hint"))
        h_lbl.setWordWrap(True)
        h_lbl.setStyleSheet(f"color: {T.text3}; font-size: 11px; background: transparent;")
        hhl.addWidget(h_lbl)
        cookie_card.layout().addWidget(hint_row)

        self._separator(cookie_card)

        # Sub-heading: manual import
        sh2 = QWidget()
        sh2.setStyleSheet("background: transparent;")
        sh2l = QHBoxLayout(sh2)
        sh2l.setContentsMargins(20, 10, 20, 6)
        sh2_lbl = QLabel(t("settings.network.section.manual_import"))
        sh2_lbl.setStyleSheet(
            f"color: {T.text3}; font-size: 10px; font-weight: 600; letter-spacing: 0.8px; background: transparent;"
        )
        sh2l.addWidget(sh2_lbl)
        sh2l.addStretch()
        cookie_card.layout().addWidget(sh2)

        fb_row = QWidget()
        fb_row.setStyleSheet("background: transparent;")
        fbhl = QHBoxLayout(fb_row)
        fbhl.setContentsMargins(20, 0, 20, 4)
        fb_lbl = QLabel(t("settings.network.fallback_hint"))
        fb_lbl.setWordWrap(True)
        fb_lbl.setStyleSheet(f"color: {T.text2}; font-size: 11px; background: transparent;")
        fbhl.addWidget(fb_lbl)
        cookie_card.layout().addWidget(fb_row)

        warn_row = QWidget()
        warn_row.setStyleSheet("background: transparent;")
        whl = QHBoxLayout(warn_row)
        whl.setContentsMargins(20, 2, 20, 8)
        w_lbl = QLabel(t("settings.network.fallback_warning"))
        w_lbl.setWordWrap(True)
        w_lbl.setStyleSheet(f"color: {T.warning_text}; font-size: 11px; background: transparent;")
        whl.addWidget(w_lbl)
        cookie_card.layout().addWidget(warn_row)

        cf_row = QWidget()
        cf_row.setStyleSheet("background: transparent;")
        cfhl = QHBoxLayout(cf_row)
        cfhl.setContentsMargins(20, 0, 20, 14)
        cfhl.setSpacing(8)
        self._cf_lbl = QLabel(self._short_cookie_path(cfg.cookie_file))
        self._cf_lbl.setStyleSheet(f"color: {T.primary_text}; font-size: 11px; background: transparent;")
        cfhl.addWidget(self._cf_lbl, 1)
        self._browse_cf_btn = QPushButton(t("settings.network.browse_btn"))
        self._browse_cf_btn.setFixedSize(80, 28)
        self._browse_cf_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._browse_cf_btn.setStyleSheet(
            f"background: {T.surface3}; color: {T.text2}; border-radius: 6px; border: none; font-size: 11px; padding: 0 6px;"
        )
        self._browse_cf_btn.clicked.connect(self._browse_cookie_file)
        cfhl.addWidget(self._browse_cf_btn)
        self._clear_cf_btn = QPushButton(t("settings.network.clear_btn"))
        self._clear_cf_btn.setFixedSize(72, 28)
        self._clear_cf_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_cf_btn.setStyleSheet(
            f"background: {T.error_bg}; color: {T.error}; border-radius: 6px; border: none; font-size: 11px; padding: 0 6px;"
        )
        self._clear_cf_btn.clicked.connect(self._clear_cookie_file)
        cfhl.addWidget(self._clear_cf_btn)
        cookie_card.layout().addWidget(cf_row)

        # -- Per-platform cookies ------------------------------------------
        sec_pc = self._collapsible_section(
            t("settings.network.section.per_platform"),
            "net_per_platform",
            icon="🍪",
            badge=t("settings.network.recommended_badge"),
            badge_color=T.success,
        )
        pc_card = self._card(container=sec_pc)

        self._row_label(
            pc_card,
            t("settings.network.per_platform_desc"),
            wrap=True,
        )

        self._pc_lbls: list[QLabel] = []
        self._pc_browse_btns: list[QPushButton] = []
        self._pc_clear_btns: list[QPushButton] = []
        self._pc_extract_btns: list[QPushButton] = []

        for key, label in _PC_PLATFORMS:
            row = QWidget()
            row.setStyleSheet("background: transparent;")
            rhl = QHBoxLayout(row)
            rhl.setContentsMargins(16, 0, 16, 6)

            plbl = QLabel(label)
            plbl.setFixedWidth(100)
            plbl.setStyleSheet(f"color: {T.text2}; font-size: 12px; background: transparent;")
            rhl.addWidget(plbl)

            path_lbl = QLabel(self._short_cookie_path(cfg.get_cookie_for_platform(key)))
            path_lbl.setStyleSheet(f"color: {T.primary_text}; font-size: 11px; background: transparent;")
            rhl.addWidget(path_lbl, 1)
            self._pc_lbls.append(path_lbl)

            _pc_btn_ss = (
                "font-size: 11px; font-weight: 600; border: none; border-radius: 6px; padding: 2px 4px;"
            )
            cdp_btn = QPushButton(t("settings.network.cdp_btn"))
            cdp_btn.setFixedSize(48, 28)
            cdp_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            cdp_btn.setToolTip(t("settings.network.cdp_tip"))
            cdp_btn.setStyleSheet(f"background: {T.surface3}; color: {T.text2}; {_pc_btn_ss}")
            cdp_btn.clicked.connect(lambda _=False, k=key, lbl=path_lbl: self._extract_platform_cdp(k, lbl))
            rhl.addWidget(cdp_btn)
            self._pc_extract_btns.append(cdp_btn)

            extract_btn = QPushButton(t("settings.network.ytdlp_btn"))
            extract_btn.setFixedSize(54, 28)
            extract_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            extract_btn.setToolTip(t("settings.network.ytdlp_tip"))
            extract_btn.setStyleSheet(f"background: {T.surface3}; color: {T.text2}; {_pc_btn_ss}")
            extract_btn.clicked.connect(
                lambda _=False, k=key, lbl=path_lbl: self._extract_platform_cookie(k, lbl)
            )
            rhl.addWidget(extract_btn)
            self._pc_extract_btns.append(extract_btn)

            browse_btn = QPushButton(t("settings.network.choose_btn"))
            browse_btn.setFixedSize(48, 28)
            browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            browse_btn.setToolTip(t("settings.network.choose_tip"))
            browse_btn.setStyleSheet(f"background: {T.surface3}; color: {T.text2}; {_pc_btn_ss}")
            browse_btn.clicked.connect(
                lambda _=False, k=key, lbl=path_lbl: self._browse_platform_cookie(k, lbl)
            )
            rhl.addWidget(browse_btn)
            self._pc_browse_btns.append(browse_btn)

            clear_btn = QPushButton(t("history.delete"))
            clear_btn.setFixedSize(44, 28)
            clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            clear_btn.setToolTip(t("settings.network.delete_tip"))
            clear_btn.setStyleSheet(f"background: {T.error_bg}; color: {T.error}; {_pc_btn_ss}")
            clear_btn.clicked.connect(
                lambda _=False, k=key, lbl=path_lbl: self._clear_platform_cookie(k, lbl)
            )
            rhl.addWidget(clear_btn)
            self._pc_clear_btns.append(clear_btn)

            pc_card.layout().addWidget(row)

        self._separator(pc_card)

        status_row = QWidget()
        status_row.setStyleSheet("background: transparent;")
        shl = QHBoxLayout(status_row)
        shl.setContentsMargins(16, 0, 16, 4)
        self._pc_extract_status = QLabel("")
        self._pc_extract_status.setStyleSheet(f"color: {T.text2}; font-size: 11px; background: transparent;")
        shl.addWidget(self._pc_extract_status, 1)
        pc_card.layout().addWidget(status_row)

        self._row_label(pc_card, t("settings.network.extract_footer_hint"))

        self._build_tiktok_accounts_section()
        self._layout.addSpacing(20)

    # ── TikTok Account Pool UI ─────────────────────────────────────────────

    def _build_tiktok_accounts_section(self) -> None:
        sec_tt = self._collapsible_section(
            t("settings.network.section.tiktok_accounts"),
            "net_tiktok_pool",
            icon="🎵",
            badge=t("settings.network.pool_badge"),
        )
        self._tt_card = self._card(container=sec_tt)

        self._row_label(
            self._tt_card,
            t("settings.network.tiktok_desc"),
            wrap=True,
        )

        # Container rebuilt by _refresh_tiktok_accounts_list()
        self._tt_list_container = QWidget()
        self._tt_list_container.setStyleSheet("background: transparent;")
        self._tt_list_vbox = QVBoxLayout(self._tt_list_container)
        self._tt_list_vbox.setContentsMargins(0, 0, 0, 0)
        self._tt_list_vbox.setSpacing(0)
        self._tt_card.layout().addWidget(self._tt_list_container)

        self._separator(self._tt_card)

        # "Add Account" button
        add_row = QWidget()
        add_row.setStyleSheet("background: transparent;")
        add_hl = QHBoxLayout(add_row)
        add_hl.setContentsMargins(16, 8, 16, 8)
        self._tt_add_btn = QPushButton(t("settings.network.add_account_btn"))
        self._tt_add_btn.setFixedHeight(30)
        self._tt_add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._tt_add_btn.setStyleSheet(
            f"background: {T.surface3}; color: {T.text2}; border-radius: 8px; border: none;"
            " font-size: 12px; padding: 0 12px;"
        )
        self._tt_add_btn.clicked.connect(self._show_tiktok_add_form)
        add_hl.addWidget(self._tt_add_btn)
        add_hl.addStretch()
        self._tt_card.layout().addWidget(add_row)

        self._build_tiktok_add_form()
        self._row_label(self._tt_card, t("settings.network.pool_hint"), wrap=True)

        # Pending cookie for the add form, plus where it came from.
        self._tt_add_pending_cookie: str = ""
        self._tt_add_pending_browser: str = ""
        self._tt_add_pending_profile: str = ""
        self._tt_add_pending_fp: str = ""
        # Bumped every time the form opens or closes.  Background extraction
        # workers capture the value at start and drop their UI callbacks when it
        # no longer matches, so a result from a cancelled run cannot land in the
        # form the user has since reopened for a different account.
        self._tt_form_gen: int = 0

        self._refresh_tiktok_accounts_list()

    def _build_tiktok_add_form(self) -> None:
        """The inline 'add account' form (hidden until '+ Add account')."""
        self._tt_add_form = QWidget()
        self._tt_add_form.setStyleSheet(f"background: {T.surface2}; border-radius: 8px;")
        self._tt_add_form.hide()
        form_vbox = QVBoxLayout(self._tt_add_form)
        form_vbox.setContentsMargins(16, 10, 16, 10)
        form_vbox.setSpacing(6)

        _line_ss = (
            f"QLineEdit {{ background: {T.input}; color: {T.text}; border: 1px solid {T.border2};"
            " border-radius: 6px; padding: 0 8px; }"
        )
        _combo_ss = (
            f"QComboBox {{ background: {T.input}; color: {T.text}; border: 1px solid {T.border2};"
            " border-radius: 6px; padding: 0 8px; font-size: 11px; }"
        )
        _spin_ss = (
            f"QSpinBox {{ background: {T.input}; color: {T.text}; border: 1px solid {T.border2};"
            " border-radius: 6px; padding: 0 4px; font-size: 11px; }"
        )

        # Row 1 — name + slots
        name_row = QWidget()
        name_row.setStyleSheet("background: transparent;")
        name_hl = QHBoxLayout(name_row)
        name_hl.setContentsMargins(0, 0, 0, 0)
        name_hl.addWidget(QLabel(t("settings.network.name_label")))
        self._tt_add_name = QLineEdit()
        self._tt_add_name.setPlaceholderText(t("settings.network.name_placeholder"))
        self._tt_add_name.setFixedHeight(28)
        self._tt_add_name.setStyleSheet(_line_ss)
        name_hl.addWidget(self._tt_add_name, 1)
        name_hl.addWidget(QLabel(t("settings.network.slots_label")))
        self._tt_add_slots = QSpinBox()
        self._tt_add_slots.setRange(1, 5)
        self._tt_add_slots.setValue(1)
        self._tt_add_slots.setFixedSize(52, 28)
        self._tt_add_slots.setToolTip(t("settings.network.slots_tip"))
        self._tt_add_slots.setStyleSheet(_spin_ss)
        name_hl.addWidget(self._tt_add_slots)
        form_vbox.addWidget(name_row)

        # Row 2 — browser + profile pickers
        src_row = QWidget()
        src_row.setStyleSheet("background: transparent;")
        src_hl = QHBoxLayout(src_row)
        src_hl.setContentsMargins(0, 0, 0, 0)
        src_hl.addWidget(QLabel(t("settings.network.browser_label")))
        self._tt_add_browser = QComboBox()
        self._tt_add_browser.addItems(["brave", "chrome", "edge", "chromium", "firefox", "opera"])
        self._tt_add_browser.setCurrentText(self._app.config.cookies_browser)
        self._tt_add_browser.setFixedHeight(28)
        self._tt_add_browser.setStyleSheet(_combo_ss)
        self._tt_add_browser.currentTextChanged.connect(lambda _: self._reload_add_form_profiles())
        src_hl.addWidget(self._tt_add_browser)
        src_hl.addWidget(QLabel(t("settings.network.profile_label")))
        self._tt_add_profile = QComboBox()
        self._tt_add_profile.setFixedHeight(28)
        self._tt_add_profile.setMinimumWidth(150)
        self._tt_add_profile.setStyleSheet(_combo_ss)
        self._tt_add_profile.setToolTip(t("settings.network.profile_tip"))
        src_hl.addWidget(self._tt_add_profile, 1)
        form_vbox.addWidget(src_row)

        # Row 3 — the three ways to obtain a cookie file
        cookie_row = QWidget()
        cookie_row.setStyleSheet("background: transparent;")
        cookie_hl = QHBoxLayout(cookie_row)
        cookie_hl.setContentsMargins(0, 0, 0, 0)
        _btn_ss = "font-size: 11px; font-weight: 600; border: none; border-radius: 6px; padding: 2px 6px;"
        self._tt_add_cdp_btn = QPushButton(t("settings.network.cdp_btn"))
        self._tt_add_cdp_btn.setFixedSize(48, 28)
        self._tt_add_cdp_btn.setToolTip(t("settings.network.cdp_tip"))
        self._tt_add_cdp_btn.setStyleSheet(f"background: {T.surface3}; color: {T.text2}; {_btn_ss}")
        self._tt_add_cdp_btn.clicked.connect(self._add_form_extract_cdp)
        cookie_hl.addWidget(self._tt_add_cdp_btn)

        self._tt_add_ytdlp_btn = QPushButton(t("settings.network.ytdlp_btn"))
        self._tt_add_ytdlp_btn.setFixedSize(54, 28)
        self._tt_add_ytdlp_btn.setToolTip(t("settings.network.ytdlp_tip"))
        self._tt_add_ytdlp_btn.setStyleSheet(f"background: {T.surface3}; color: {T.text2}; {_btn_ss}")
        self._tt_add_ytdlp_btn.clicked.connect(self._add_form_extract_ytdlp)
        cookie_hl.addWidget(self._tt_add_ytdlp_btn)

        self._tt_add_browse_btn = QPushButton(t("settings.network.choose_btn"))
        self._tt_add_browse_btn.setFixedSize(48, 28)
        self._tt_add_browse_btn.setToolTip(t("settings.network.choose_tip"))
        self._tt_add_browse_btn.setStyleSheet(f"background: {T.surface3}; color: {T.text2}; {_btn_ss}")
        self._tt_add_browse_btn.clicked.connect(self._add_form_browse)
        cookie_hl.addWidget(self._tt_add_browse_btn)

        self._tt_add_cookie_lbl = QLabel(t("settings.network.no_cookie_chosen"))
        self._tt_add_cookie_lbl.setWordWrap(True)
        self._tt_add_cookie_lbl.setStyleSheet(f"color: {T.text3}; font-size: 11px; background: transparent;")
        cookie_hl.addWidget(self._tt_add_cookie_lbl, 1)
        form_vbox.addWidget(cookie_row)

        # Row 4 — save / cancel
        btn_row = QWidget()
        btn_row.setStyleSheet("background: transparent;")
        btn_hl = QHBoxLayout(btn_row)
        btn_hl.setContentsMargins(0, 4, 0, 0)
        self._tt_add_save_btn = QPushButton(t("settings.network.save_btn"))
        self._tt_add_save_btn.setFixedHeight(28)
        self._tt_add_save_btn.setEnabled(False)
        self._tt_add_save_btn.setStyleSheet(
            f"background: {T.primary}; color: white; border-radius: 6px; border: none;"
            " font-size: 12px; font-weight: 600; padding: 0 12px;"
        )
        self._tt_add_save_btn.clicked.connect(self._save_new_tiktok_account)
        btn_hl.addStretch()
        cancel_btn = QPushButton(t("archive.cancel"))
        cancel_btn.setFixedHeight(28)
        cancel_btn.setStyleSheet(
            f"background: {T.surface3}; color: {T.text2}; border-radius: 6px; border: none;"
            " font-size: 12px; padding: 0 12px;"
        )
        cancel_btn.clicked.connect(self._hide_tiktok_add_form)
        btn_hl.addWidget(cancel_btn)
        btn_hl.addWidget(self._tt_add_save_btn)
        form_vbox.addWidget(btn_row)

        self._tt_card.layout().addWidget(self._tt_add_form)

    # ── Browser profiles ──────────────────────────────────────────────────

    def _reload_add_form_profiles(self) -> None:
        """Refill the profile picker for the browser currently selected."""
        from infrastructure.downloader.cookie_extractor import list_browser_profiles

        browser = self._tt_add_browser.currentText()
        self._tt_add_profile.clear()
        try:
            profiles = list_browser_profiles(browser)
        except Exception as exc:  # a broken browser install must not block the form
            logger.warning("list_browser_profiles(%s) failed: %s", browser, exc)
            profiles = []

        if not profiles:
            # Firefox/Opera (and any browser we cannot enumerate) still work —
            # they just extract from whatever profile the browser calls default.
            self._tt_add_profile.addItem(t("settings.network.profile_default"), "")
            self._tt_add_profile.setEnabled(False)
            return

        self._tt_add_profile.setEnabled(True)
        for dir_name, label in profiles:
            shown = label if label == dir_name else f"{label} — {dir_name}"
            self._tt_add_profile.addItem(shown, dir_name)

    def _add_form_profile(self) -> str:
        data = self._tt_add_profile.currentData()
        return str(data) if data else ""

    # ── Account rows ──────────────────────────────────────────────────────

    @staticmethod
    def _account_health(acc: dict) -> "tuple[str, str, str]":
        """Return (dot, color, tooltip) describing this account's cookie."""
        from infrastructure.downloader.account_pool import inspect_tiktok_cookie

        if not bool(acc.get("enabled", True)):
            return "⏸", T.warning_text, t("settings.network.status_paused")
        health = inspect_tiktok_cookie(acc.get("cookie_file", ""))
        if health.status == "ok":
            if health.expires_at:
                import time as _time

                days = max(0, int((health.expires_at - _time.time()) // 86400))
                return "●", T.success, t("settings.network.status_ok", days=days)
            return "●", T.success, t("settings.network.status_ok_session")
        return "●", T.error, t(f"settings.network.status_{health.status}")

    def _refresh_tiktok_accounts_list(self) -> None:
        # Rows are recreated below, so any button kept from the previous build
        # is about to be deleted — drop the references before they go stale.
        self._tt_refresh_btns: dict[str, QPushButton] = {}
        # Clear existing rows
        while self._tt_list_vbox.count():
            item = self._tt_list_vbox.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        accounts = self._app.config.tiktok_account_pool
        if not accounts:
            empty_lbl = QLabel(t("settings.network.no_accounts"))
            empty_lbl.setStyleSheet(
                f"color: {T.text3}; font-size: 11px; background: transparent; padding: 8px 16px 4px;"
            )
            self._tt_list_vbox.addWidget(empty_lbl)
            return

        _btn_ss = "font-size: 11px; font-weight: 600; border: none; border-radius: 6px; padding: 2px 6px;"
        for acc in accounts:
            acc_id = acc.get("id", "")
            row = QWidget()
            row.setStyleSheet("background: transparent;")
            hl = QHBoxLayout(row)
            hl.setContentsMargins(16, 4, 16, 4)

            # Editable name — renaming no longer means delete + re-add.
            name_edit = QLineEdit(acc.get("name", "Account"))
            name_edit.setFixedWidth(110)
            name_edit.setFixedHeight(26)
            name_edit.setToolTip(t("settings.network.rename_tip"))
            name_edit.setStyleSheet(
                f"QLineEdit {{ background: transparent; color: {T.text}; font-size: 12px;"
                f" border: 1px solid transparent; border-radius: 5px; padding: 0 4px; }}"
                f"QLineEdit:focus {{ background: {T.input}; border: 1px solid {T.border2}; }}"
            )
            name_edit.editingFinished.connect(
                lambda aid=acc_id, w=name_edit: self._rename_tiktok_account(aid, w.text())
            )
            hl.addWidget(name_edit)

            dot, dot_color, tip = self._account_health(acc)
            status_lbl = QLabel(dot)
            status_lbl.setFixedWidth(18)
            status_lbl.setToolTip(tip)
            status_lbl.setStyleSheet(f"color: {dot_color}; font-size: 13px; background: transparent;")
            hl.addWidget(status_lbl)

            source = self._account_source_text(acc)
            cookie_lbl = QLabel(source)
            cookie_lbl.setToolTip(self._short_cookie_path(acc.get("cookie_file", "")))
            cookie_lbl.setStyleSheet(f"color: {T.text2}; font-size: 10px; background: transparent;")
            hl.addWidget(cookie_lbl, 1)

            slots_spin = QSpinBox()
            slots_spin.setRange(1, 5)
            slots_spin.setValue(max(1, min(5, int(acc.get("max_slots", 1)))))
            slots_spin.setFixedSize(52, 26)
            slots_spin.setToolTip(t("settings.network.slots_tip"))
            slots_spin.setStyleSheet(
                f"QSpinBox {{ background: {T.input}; color: {T.text}; border: 1px solid {T.border2};"
                " border-radius: 5px; padding: 0 4px; font-size: 11px; }"
            )
            slots_spin.valueChanged.connect(lambda v, aid=acc_id: self._set_tiktok_account_slots(aid, v))
            hl.addWidget(slots_spin)

            refresh_btn = QPushButton(t("settings.network.refresh_btn"))
            refresh_btn.setFixedSize(30, 26)
            refresh_btn.setToolTip(t("settings.network.refresh_tip"))
            refresh_btn.setStyleSheet(f"background: {T.surface3}; color: {T.text2}; {_btn_ss}")
            refresh_btn.clicked.connect(lambda _, aid=acc_id: self._refresh_tiktok_account_cookie(aid))
            hl.addWidget(refresh_btn)
            self._tt_refresh_btns[acc_id] = refresh_btn

            enabled = bool(acc.get("enabled", True))
            pause_lbl = t("settings.network.resume_btn") if not enabled else t("settings.network.pause_btn")
            pause_btn = QPushButton(pause_lbl)
            pause_btn.setFixedSize(60, 26)
            pause_btn.setStyleSheet(f"background: {T.surface3}; color: {T.text2}; {_btn_ss}")
            pause_btn.clicked.connect(
                lambda _, aid=acc_id, en=enabled: self._toggle_tiktok_account(aid, not en)
            )
            hl.addWidget(pause_btn)

            del_btn = QPushButton(t("history.delete"))
            del_btn.setFixedSize(40, 26)
            del_btn.setStyleSheet(f"background: {T.error_bg}; color: {T.error}; {_btn_ss}")
            del_btn.clicked.connect(lambda _, aid=acc_id: self._remove_tiktok_account(aid))
            hl.addWidget(del_btn)

            self._tt_list_vbox.addWidget(row)

    @staticmethod
    def _account_source_text(acc: dict) -> str:
        """'brave · Work — Profile 1', or the shortened path for manual files."""
        browser = str(acc.get("browser", "")).strip()
        if not browser:
            return t("settings.network.source_manual")
        profile = str(acc.get("profile", "")).strip() or "Default"
        return f"{browser} · {profile}"

    # ── TikTok account pool handlers ──────────────────────────────────────

    def _show_tiktok_add_form(self) -> None:
        self._tt_form_gen += 1
        self._tt_add_pending_cookie = ""
        self._tt_add_pending_browser = ""
        self._tt_add_pending_profile = ""
        self._tt_add_pending_fp = ""
        self._tt_add_name.setText("")
        self._tt_add_slots.setValue(1)
        self._tt_add_browser.setCurrentText(self._app.config.cookies_browser)
        self._reload_add_form_profiles()
        self._set_add_form_status(t("settings.network.no_cookie_chosen"), T.text3)
        self._tt_add_save_btn.setEnabled(False)
        self._set_add_form_busy(False)
        self._tt_add_form.show()
        self._tt_add_btn.hide()

    def _hide_tiktok_add_form(self) -> None:
        self._tt_form_gen += 1
        self._tt_add_form.hide()
        self._tt_add_btn.show()
        # Cancelling after a successful extraction used to strand the jar in the
        # cookies folder.  _save_new_tiktok_account clears the pending path
        # first, so a saved account's file is never touched here.
        if self._tt_add_pending_cookie:
            self._delete_pool_cookie_file(self._tt_add_pending_cookie)
        self._tt_add_pending_cookie = ""
        self._tt_add_pending_browser = ""
        self._tt_add_pending_profile = ""
        self._tt_add_pending_fp = ""

    def _set_add_form_status(self, text: str, color: str) -> None:
        self._tt_add_cookie_lbl.setText(text)
        self._tt_add_cookie_lbl.setStyleSheet(f"color: {color}; font-size: 11px; background: transparent;")

    def _set_add_form_busy(self, busy: bool) -> None:
        for btn in (self._tt_add_cdp_btn, self._tt_add_ytdlp_btn, self._tt_add_browse_btn):
            btn.setEnabled(not busy)

    def _duplicate_account_name(self, fingerprint: str, exclude_id: str = "") -> str:
        """Name of the pool account already using *fingerprint*, else ''."""
        if not fingerprint:
            return ""
        for acc in self._app.config.tiktok_account_pool:
            if acc.get("id") == exclude_id:
                continue
            if acc.get("session_fp") == fingerprint:
                return str(acc.get("name", "?"))
        return ""

    def _accept_cookie_for_form(self, path: str, browser: str, profile: str) -> None:
        """Validate a freshly obtained cookie file and arm the Save button.

        Rejects jars that are not signed in, whose session already expired, or
        that belong to a TikTok account the pool already holds — all three used
        to be accepted silently and only surfaced as failed downloads later.
        """
        from infrastructure.downloader.account_pool import inspect_tiktok_cookie

        # A rejected jar still exists on disk and no account will ever point at
        # it, so it has to go — a real TikTok session left lying in the cookies
        # folder is both a leak and a security problem.  Same for the jar a
        # previous extraction left behind when the user re-extracts.
        previous = self._tt_add_pending_cookie
        if previous and previous != path:
            self._delete_pool_cookie_file(previous)

        def _reject(status_text: str) -> None:
            self._set_add_form_status(status_text, T.error)
            self._tt_add_save_btn.setEnabled(False)
            self._tt_add_pending_cookie = ""
            self._tt_add_pending_fp = ""
            self._delete_pool_cookie_file(path)

        health = inspect_tiktok_cookie(path)
        if not health.ok:
            _reject(t(f"settings.network.reject_{health.status}"))
            return

        dup = self._duplicate_account_name(health.fingerprint)
        if dup:
            _reject(t("settings.network.duplicate_account", name=dup))
            return

        self._tt_add_pending_cookie = path
        self._tt_add_pending_browser = browser
        self._tt_add_pending_profile = profile
        self._tt_add_pending_fp = health.fingerprint
        if not self._tt_add_name.text().strip():
            self._tt_add_name.setText(self._suggest_account_name(browser, profile))
        self._set_add_form_status(t("settings.network.cookie_ready", count=health.count), T.success)
        self._tt_add_save_btn.setEnabled(True)

    def _suggest_account_name(self, browser: str, profile: str) -> str:
        """Pre-fill a name the user can recognise, kept unique within the pool."""
        if browser:
            base = f"{browser.title()} {profile or 'Default'}"
        else:
            base = t("settings.network.default_account_name")
        taken = {str(a.get("name", "")).lower() for a in self._app.config.tiktok_account_pool}
        if base.lower() not in taken:
            return base
        n = 2
        while f"{base} {n}".lower() in taken:
            n += 1
        return f"{base} {n}"

    def _cookies_dir(self) -> Path:
        safe_dir = self._app.config.config_path.parent / "cookies"
        safe_dir.mkdir(parents=True, exist_ok=True)
        return safe_dir

    def _add_form_browse(self) -> None:
        import shutil

        chosen, _ = QFileDialog.getOpenFileName(
            self,
            t("settings.network.select_tiktok_cookie_title"),
            "",
            "Cookie files (*.txt);;All files (*.*)",
        )
        if not chosen:
            return
        src = Path(chosen)
        if not src.is_file() or not _is_netscape_cookie_file(src):
            self._app.toast(t("settings.network.not_netscape_format"), "error")
            return
        safe_dir = self._cookies_dir()
        import uuid as _uuid

        dest = safe_dir / f"tiktok_pool_{_uuid.uuid4().hex[:6]}_{src.name}"
        try:
            shutil.copy2(src, dest)
        except OSError as exc:
            self._app.toast(t("settings.network.copy_failed", err=exc), "error")
            return
        from infrastructure.downloader.cookie_storage import encrypt_cookie_file

        dest = encrypt_cookie_file(dest)
        # _accept_cookie_for_form deletes the copy again if it rejects the jar.
        self._accept_cookie_for_form(str(dest), "", "")

    def _add_form_extract_cdp(self) -> None:
        browser = self._tt_add_browser.currentText()
        if _browser_family_supported_by_cdp(browser):
            self._start_add_form_extraction("cdp", browser, self._add_form_profile())
        else:
            self._app.toast(t("settings.network.cdp_unsupported_browser"), "error")

    def _add_form_extract_ytdlp(self) -> None:
        self._start_add_form_extraction("ytdlp", self._tt_add_browser.currentText(), self._add_form_profile())

    def _start_add_form_extraction(self, method: str, browser: str, profile: str) -> None:
        """Run CDP / yt-dlp extraction off the UI thread for the add form."""
        import uuid as _uuid

        safe_dir = self._cookies_dir()
        suffix = "_cdp" if method == "cdp" else ""
        output_path = safe_dir / f"tiktok_pool_{_uuid.uuid4().hex[:6]}_{browser}{suffix}.txt"

        self._set_add_form_busy(True)
        self._set_add_form_status(
            t("settings.network.starting_browser", browser=browser.title())
            if method == "cdp"
            else t("settings.network.reading_tiktok_from", browser=browser),
            T.text3,
        )
        gen = self._tt_form_gen

        def _worker():
            try:
                if method == "cdp":
                    from infrastructure.downloader.cookie_extractor import extract_via_cdp

                    count, error = extract_via_cdp(
                        output_path, platform_key="tiktok", browser=browser, profile=profile or None
                    )
                else:
                    from infrastructure.downloader.cookie_extractor import extract_browser_cookies

                    count, error = extract_browser_cookies(
                        browser, output_path, platform_key="tiktok", profile=profile or None
                    )
            except Exception as exc:
                error, count = str(exc), 0

            def _apply(err=error, c=count):
                saved = self._resolve_saved_cookie_path(output_path)
                # The form was closed or reopened while we were extracting —
                # dropping the result keeps it out of an unrelated account, and
                # the jar it wrote must be removed with it.
                if gen != self._tt_form_gen:
                    self._delete_pool_cookie_file(saved)
                    return
                self._set_add_form_busy(False)
                if err:
                    self._delete_pool_cookie_file(saved)
                    key = "cdp_failed" if method == "cdp" else "extract_failed"
                    self._set_add_form_status(t(f"settings.network.{key}", err=err[:80]), T.error)
                    return
                self._accept_cookie_for_form(saved, browser, profile)
                if self._tt_add_pending_cookie:
                    self._app.toast(t("settings.network.got_tiktok_cookies", count=c), "success")

            ui_bridge.post(_apply)

        threading.Thread(target=_worker, daemon=True, name=f"omnidl-tt-pool-{method}").start()

    def _save_new_tiktok_account(self) -> None:
        cookie_path = self._tt_add_pending_cookie
        if not cookie_path:
            return
        name = self._tt_add_name.text().strip()
        if not name:
            name = self._suggest_account_name(self._tt_add_pending_browser, self._tt_add_pending_profile)
        taken = {str(a.get("name", "")).lower() for a in self._app.config.tiktok_account_pool}
        if name.lower() in taken:
            self._app.toast(t("settings.network.name_in_use", name=name), "error")
            return
        import uuid as _uuid

        from infrastructure.downloader.account_pool import TikTokAccount

        new_acc = TikTokAccount(
            id=_uuid.uuid4().hex[:8],
            name=name,
            cookie_file=cookie_path,
            max_slots=self._tt_add_slots.value(),
            enabled=True,
            browser=self._tt_add_pending_browser,
            profile=self._tt_add_pending_profile,
            session_fp=self._tt_add_pending_fp,
        )
        pool = list(self._app.config.tiktok_account_pool)
        pool.append(new_acc.to_dict())
        self._app.config.set_tiktok_account_pool(pool)
        # set_tiktok_account_pool drops entries whose cookie_file sits outside the
        # managed cookies dir.  Without this check the pool stays empty while the
        # user gets a success toast.
        if not any(a.get("id") == new_acc.id for a in self._app.config.tiktok_account_pool):
            self._tt_add_pending_cookie = ""
            self._delete_pool_cookie_file(cookie_path)
            self._app.toast(t("settings.network.account_rejected"), "error")
            return
        # The account now owns the file — hand it over before hiding the form,
        # which deletes whatever is still pending.
        self._tt_add_pending_cookie = ""
        self._hide_tiktok_add_form()
        self._refresh_tiktok_accounts_list()
        self._rebuild_pool()
        self._app.toast(t("settings.network.account_added", name=name), "success")

    def _remove_tiktok_account(self, account_id: str) -> None:
        gone = next((a for a in self._app.config.tiktok_account_pool if a.get("id") == account_id), None)
        pool = [a for a in self._app.config.tiktok_account_pool if a.get("id") != account_id]
        self._app.config.set_tiktok_account_pool(pool)
        if gone:
            # Otherwise every removed account leaves its encrypted cookie jar
            # behind in the cookies folder forever.
            self._delete_pool_cookie_file(gone.get("cookie_file", ""))
        self._refresh_tiktok_accounts_list()
        self._rebuild_pool()

    def _delete_pool_cookie_file(self, cookie_file: str) -> None:
        if not cookie_file:
            return
        safe_dir = (self._app.config.config_path.parent / "cookies").resolve()
        for candidate in _cookie_file_candidates(cookie_file):
            try:
                resolved = candidate.resolve()
                if safe_dir not in resolved.parents:
                    logger.warning("_delete_pool_cookie_file: rejected outside path: %s", candidate)
                    continue
                if resolved.is_file():
                    resolved.unlink()
                    invalidate_cookie_cache(resolved)
                    logger.info("Deleted pool cookie file: %s", resolved.name)
            except OSError as exc:
                logger.warning("_delete_pool_cookie_file: could not delete %s — %s", candidate, exc)

    def _rename_tiktok_account(self, account_id: str, new_name: str) -> None:
        new_name = new_name.strip()
        current = self._app.config.tiktok_account_pool
        old = next((a for a in current if a.get("id") == account_id), None)
        if old is None or not new_name or new_name == old.get("name"):
            return
        taken = {str(a.get("name", "")).lower() for a in current if a.get("id") != account_id}
        if new_name.lower() in taken:
            self._app.toast(t("settings.network.name_in_use", name=new_name), "error")
            self._refresh_tiktok_accounts_list()
            return
        pool = []
        for a in current:
            entry = dict(a)
            if entry.get("id") == account_id:
                entry["name"] = new_name
            pool.append(entry)
        self._app.config.set_tiktok_account_pool(pool)
        self._rebuild_pool()

    def _refresh_tiktok_account_cookie(self, account_id: str) -> None:
        """Re-extract cookies for an existing account, in place.

        Sessions expire; before this the only cure was Delete + Add again,
        retyping the name and slot count.
        """
        acc = next((a for a in self._app.config.tiktok_account_pool if a.get("id") == account_id), None)
        if acc is None:
            return
        browser = str(acc.get("browser", "")).strip()
        if not browser:
            self._app.toast(t("settings.network.refresh_no_source"), "error")
            return
        profile = str(acc.get("profile", "")).strip()
        method = "cdp" if _browser_family_supported_by_cdp(browser) else "ytdlp"
        import uuid as _uuid

        safe_dir = self._cookies_dir()
        output_path = safe_dir / f"tiktok_pool_{_uuid.uuid4().hex[:6]}_{browser}_refresh.txt"

        btn = getattr(self, "_tt_refresh_btns", {}).get(account_id)
        if btn is not None:
            btn.setEnabled(False)
        self._app.toast(t("settings.network.refreshing", name=acc.get("name", "?")), "info")

        def _worker():
            try:
                if method == "cdp":
                    from infrastructure.downloader.cookie_extractor import extract_via_cdp

                    _, error = extract_via_cdp(
                        output_path, platform_key="tiktok", browser=browser, profile=profile or None
                    )
                else:
                    from infrastructure.downloader.cookie_extractor import extract_browser_cookies

                    _, error = extract_browser_cookies(
                        browser, output_path, platform_key="tiktok", profile=profile or None
                    )
            except Exception as exc:
                error = str(exc)
            saved = self._resolve_saved_cookie_path(output_path)
            ui_bridge.post(lambda e=error, s=saved: self._apply_refreshed_cookie(account_id, s, e))

        threading.Thread(target=_worker, daemon=True, name="omnidl-tt-pool-refresh").start()

    def _apply_refreshed_cookie(self, account_id: str, saved_path: str, error: str) -> None:
        from infrastructure.downloader.account_pool import inspect_tiktok_cookie

        current = self._app.config.tiktok_account_pool
        acc = next((a for a in current if a.get("id") == account_id), None)
        if acc is None:  # deleted while the extraction was running
            self._delete_pool_cookie_file(saved_path)
            return

        if error:
            self._delete_pool_cookie_file(saved_path)
            self._app.toast(t("settings.network.extract_failed", err=error[:80]), "error")
            self._refresh_tiktok_accounts_list()
            return

        health = inspect_tiktok_cookie(saved_path)
        if not health.ok:
            self._delete_pool_cookie_file(saved_path)
            self._app.toast(t(f"settings.network.reject_{health.status}"), "error")
            self._refresh_tiktok_accounts_list()
            return

        dup = self._duplicate_account_name(health.fingerprint, exclude_id=account_id)
        if dup:
            self._delete_pool_cookie_file(saved_path)
            self._app.toast(t("settings.network.duplicate_account", name=dup), "error")
            self._refresh_tiktok_accounts_list()
            return

        old_cookie = acc.get("cookie_file", "")
        pool = []
        for a in current:
            entry = dict(a)
            if entry.get("id") == account_id:
                entry["cookie_file"] = saved_path
                entry["session_fp"] = health.fingerprint
            pool.append(entry)
        self._app.config.set_tiktok_account_pool(pool)
        if any(
            a.get("id") == account_id and a.get("cookie_file") == saved_path
            for a in self._app.config.tiktok_account_pool
        ):
            if old_cookie != saved_path:
                self._delete_pool_cookie_file(old_cookie)
            self._app.toast(t("settings.network.account_refreshed", name=acc.get("name", "?")), "success")
        else:
            self._delete_pool_cookie_file(saved_path)
            self._app.toast(t("settings.network.account_rejected"), "error")
        self._refresh_tiktok_accounts_list()
        self._rebuild_pool()

    def _toggle_tiktok_account(self, account_id: str, enabled: bool) -> None:
        pool = []
        for a in self._app.config.tiktok_account_pool:
            entry = dict(a)
            if entry.get("id") == account_id:
                entry["enabled"] = enabled
            pool.append(entry)
        self._app.config.set_tiktok_account_pool(pool)
        self._refresh_tiktok_accounts_list()
        self._rebuild_pool()

    def _set_tiktok_account_slots(self, account_id: str, n: int) -> None:
        pool = []
        for a in self._app.config.tiktok_account_pool:
            entry = dict(a)
            if entry.get("id") == account_id:
                entry["max_slots"] = max(1, min(5, n))
            pool.append(entry)
        self._app.config.set_tiktok_account_pool(pool)
        self._rebuild_pool()

    def _rebuild_pool(self) -> None:
        if hasattr(self._app, "rebuild_tiktok_pool"):
            self._app.rebuild_tiktok_pool()

    # ── Handlers — Network ────────────────────────────────────────────────

    def _on_proxy_focusout(self) -> None:
        val = self._proxy_entry.text().strip()
        if not val:
            self._app.config.set("proxy", "")
            return
        _valid_schemes = ("http://", "https://", "socks4://", "socks5://")
        if any(val.lower().startswith(s) for s in _valid_schemes):
            self._app.config.set("proxy", val)
            self._proxy_entry.setStyleSheet(_INPUT_SS())
        else:
            self._proxy_entry.setStyleSheet(
                f"QLineEdit {{ background: {T.input}; color: {T.text}; border: 1px solid {T.error};"
                f" border-radius: 8px; padding: 6px 12px; }}"
            )
            QTimer.singleShot(1500, self._proxy_entry, lambda: self._proxy_entry.setStyleSheet(_INPUT_SS()))
            self._app.toast(t("settings.network.proxy_invalid"), "error")

    # ── Handlers — Global Cookie ──────────────────────────────────────────

    def _browse_cookie_file(self) -> None:
        import shutil

        from infrastructure.downloader.cookie_storage import encrypt_cookie_file

        chosen, _ = QFileDialog.getOpenFileName(
            self, t("settings.network.select_cookies_title"), "", "Cookie files (*.txt);;All files (*.*)"
        )
        if not chosen:
            return
        src = Path(chosen)
        if not src.is_file():
            self._app.toast(t("settings.network.file_not_found"), "error")
            return
        if not _is_netscape_cookie_file(src):
            self._app.toast(t("settings.network.not_netscape_full"), "error")
            return
        old_path_str = self._app.config.get("cookie_file", "")
        safe_dir = self._app.config.config_path.parent / "cookies"
        safe_dir.mkdir(parents=True, exist_ok=True)
        dest = safe_dir / src.name
        try:
            shutil.copy2(src, dest)
        except OSError as exc:
            logger.warning("Failed to copy cookie file: %s", exc)
            self._app.toast(t("settings.network.copy_failed_full", err=exc), "error")
            return
        dest = encrypt_cookie_file(dest)
        self._app.config.set("cookie_file", str(dest))
        self._cf_lbl.setText(self._short_cookie_path(str(dest)))
        self._app.toast(t("settings.network.cookie_saved_encrypted"), "info")
        self._delete_old_cookie_if_replaced(old_path_str, dest)

    def _clear_cookie_file(self) -> None:
        old_path_str = self._app.config.get("cookie_file", "")
        self._app.config.set("cookie_file", "")
        self._cf_lbl.setText(t("settings.network.no_file_selected"))
        if old_path_str:
            safe_dir = self._app.config.config_path.parent.resolve()
            for candidate in _cookie_file_candidates(old_path_str):
                try:
                    resolved = candidate.resolve()
                    if safe_dir not in resolved.parents and resolved != safe_dir:
                        logger.warning("_clear_cookie_file: rejected path outside safe dir: %s", candidate)
                        continue
                    if resolved.is_file():
                        resolved.unlink()
                        invalidate_cookie_cache(resolved)
                        logger.info("Deleted global cookie file on clear: %s", resolved.name)
                except OSError as exc:
                    logger.warning("_clear_cookie_file: could not delete %s — %s", candidate, exc)

    def _extract_global_cdp(self) -> None:
        reply = QMessageBox.question(
            self,
            t("settings.network.cdp_confirm_title"),
            t("settings.network.cdp_confirm_msg"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        browser = self._browser_combo.currentText()
        if browser not in ("brave", "chrome", "chromium", "edge"):
            self._app.toast(t("settings.network.cdp_browser_unsupported", browser=browser), "error")
            return
        safe_dir = self._app.config.config_path.parent / "cookies"
        output_path = safe_dir / f"{browser}_cdp_cookies.txt"
        busy_btns = (self._extract_global_btn, self._extract_cdp_btn)
        status = self._extract_global_status
        old_path_str = self._app.config.get("cookie_file", "")

        def _worker():
            try:
                from infrastructure.downloader.cookie_extractor import extract_via_cdp

                count, error = extract_via_cdp(output_path, platform_key=None, browser=browser)
            except Exception as exc:
                err_msg = str(exc)
                ui_bridge.post(
                    lambda e=err_msg: (
                        status.setText(t("settings.network.error_status", err=e.splitlines()[0][:70])),
                        status.setStyleSheet(f"color: {T.error}; font-size: 11px; background: transparent;"),
                        self._app.toast(
                            t("settings.network.cdp_global_failed_toast", err=e.splitlines()[0][:60]), "error"
                        ),
                    )
                )
                ui_bridge.post(lambda: [b.setEnabled(True) for b in busy_btns])
                return
            path_str = self._resolve_saved_cookie_path(output_path)
            self._app.config.set("cookie_file", path_str)
            self._delete_old_cookie_if_replaced(old_path_str, Path(path_str))
            ui_bridge.post(
                lambda c=count, ps=path_str: (
                    self._cf_lbl.setText(self._short_cookie_path(ps)),
                    status.setText(t("settings.network.cdp_saved_status", count=c)),
                    status.setStyleSheet(f"color: {T.success}; font-size: 11px; background: transparent;"),
                    self._app.toast(
                        t("settings.network.cdp_from_browser_toast", count=c, browser=browser.title()),
                        "success",
                    ),
                )
            )
            ui_bridge.post(
                lambda: QTimer.singleShot(
                    6000,
                    status,
                    lambda: (
                        status.setText(""),
                        status.setStyleSheet(f"color: {T.text2}; font-size: 11px; background: transparent;"),
                    ),
                )
            )
            ui_bridge.post(lambda: [b.setEnabled(True) for b in busy_btns])

        for b in busy_btns:
            b.setEnabled(False)
        status.setText(t("settings.network.starting_cdp_status", browser=browser.title()))
        threading.Thread(target=_worker, daemon=True, name="omnidl-cdp-extract").start()

    def _extract_global_cookies(self) -> None:
        reply = QMessageBox.question(
            self,
            t("settings.network.global_confirm_title"),
            t("settings.network.global_confirm_msg"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        browser = self._browser_combo.currentText()
        safe_dir = self._app.config.config_path.parent / "cookies"
        output_path = safe_dir / f"{browser}_global_cookies.txt"
        busy_btns = (self._extract_global_btn, self._extract_cdp_btn)
        status = self._extract_global_status
        old_path_str = self._app.config.get("cookie_file", "")

        def _worker():
            try:
                from infrastructure.downloader.cookie_extractor import extract_browser_cookies

                count, error = extract_browser_cookies(browser, output_path, platform_key=None)
            except Exception as exc:
                error = str(exc)
                count = 0
            if error:
                ui_bridge.post(
                    lambda e=error: (
                        status.setText(t("settings.network.error_status", err=e.splitlines()[0][:70])),
                        status.setStyleSheet(f"color: {T.error}; font-size: 11px; background: transparent;"),
                        self._app.toast(
                            t("settings.network.extract_failed_toast", err=e.splitlines()[0][:60]), "error"
                        ),
                    )
                )
            else:
                enc_candidate = output_path.parent / (output_path.stem + ".enc")
                final_path = enc_candidate if enc_candidate.exists() else output_path
                path_str = str(final_path)
                self._app.config.set("cookie_file", path_str)
                self._delete_old_cookie_if_replaced(old_path_str, final_path)
                enc_note = t("settings.network.encrypted_note") if path_str.endswith(".enc") else ""
                ui_bridge.post(
                    lambda c=count, ps=path_str, n=enc_note: (
                        self._cf_lbl.setText(self._short_cookie_path(ps)),
                        status.setText(t("settings.network.saved_status", count=c, note=n)),
                        status.setStyleSheet(
                            f"color: {T.success}; font-size: 11px; background: transparent;"
                        ),
                        self._app.toast(
                            t("settings.network.got_from_browser_toast", count=c, browser=browser, note=n),
                            "success",
                        ),
                    )
                )
                ui_bridge.post(
                    lambda: QTimer.singleShot(
                        6000,
                        status,
                        lambda: (
                            status.setText(""),
                            status.setStyleSheet(
                                f"color: {T.text2}; font-size: 11px; background: transparent;"
                            ),
                        ),
                    )
                )
            ui_bridge.post(lambda: [b.setEnabled(True) for b in busy_btns])

        for b in busy_btns:
            b.setEnabled(False)
        status.setText(t("settings.network.reading_from_browser_status", browser=browser))
        threading.Thread(target=_worker, daemon=True, name="omnidl-cookie-extract").start()

    # ── Handlers — Per-Platform Cookies ───────────────────────────────────

    def _browse_platform_cookie(self, platform_key: str, path_lbl: QLabel) -> None:
        import shutil

        platform_name = _PLATFORM_NAMES.get(platform_key, platform_key.title())
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            t("settings.network.select_cookie_for", platform=platform_name),
            "",
            "Cookie files (*.txt);;All files (*.*)",
        )
        if not chosen:
            return
        src_path = Path(chosen)
        if not src_path.is_file():
            self._app.toast(t("settings.network.file_not_found_vi"), "error")
            return
        if not _is_netscape_cookie_file(src_path):
            self._app.toast(t("settings.network.not_netscape_full"), "error")
            return
        old_path_str = self._app.config.get_cookie_for_platform(platform_key)
        safe_dir = self._app.config.config_path.parent / "cookies"
        safe_dir.mkdir(parents=True, exist_ok=True)
        dest = safe_dir / f"{platform_key}_{src_path.name}"
        try:
            shutil.copy2(src_path, dest)
        except OSError as exc:
            logger.warning("Failed to copy platform cookie file: %s", exc)
            self._app.toast(t("settings.network.copy_platform_failed", err=exc), "error")
            return
        from infrastructure.downloader.cookie_storage import encrypt_cookie_file

        dest = encrypt_cookie_file(dest)
        self._app.config.set_cookie_for_platform(platform_key, str(dest))
        path_lbl.setText(self._short_cookie_path(str(dest)))
        self._app.toast(t("settings.network.platform_cookie_saved", platform=platform_name), "info")
        self._delete_old_cookie_if_replaced(old_path_str, dest)

    def _extract_platform_cookie(self, platform_key: str, path_lbl: QLabel) -> None:
        browser = self._browser_combo.currentText()
        platform_name = _PLATFORM_NAMES.get(platform_key, platform_key.title())
        safe_dir = self._app.config.config_path.parent / "cookies"
        output_path = safe_dir / f"{platform_key}_{browser}_cookies.txt"
        status = self._pc_extract_status
        old_path_str = self._app.config.get_cookie_for_platform(platform_key)
        for btn in self._pc_extract_btns:
            btn.setEnabled(False)

        def _worker():
            try:
                from infrastructure.downloader.cookie_extractor import extract_browser_cookies

                count, error = extract_browser_cookies(browser, output_path, platform_key=platform_key)
            except Exception as exc:
                error = str(exc)
                count = 0
            if error:
                ui_bridge.post(
                    lambda e=error, pn=platform_name: (
                        status.setText(
                            t(
                                "settings.network.platform_extract_failed_status",
                                platform=pn,
                                err=e.splitlines()[0][:65],
                            )
                        ),
                        status.setStyleSheet(f"color: {T.error}; font-size: 11px; background: transparent;"),
                        self._app.toast(
                            t(
                                "settings.network.platform_extract_failed_toast",
                                platform=pn,
                                err=e.splitlines()[0][:55],
                            ),
                            "error",
                        ),
                    )
                )
            else:
                path_str = self._resolve_saved_cookie_path(output_path)
                self._app.config.set_cookie_for_platform(platform_key, path_str)
                self._delete_old_cookie_if_replaced(old_path_str, Path(path_str))
                ui_bridge.post(
                    lambda c=count, ps=path_str, pn=platform_name: (
                        path_lbl.setText(self._short_cookie_path(ps)),
                        status.setText(t("settings.network.platform_saved_status", platform=pn, count=c)),
                        status.setStyleSheet(
                            f"color: {T.success}; font-size: 11px; background: transparent;"
                        ),
                        self._app.toast(
                            t("settings.network.platform_got_toast", count=c, platform=pn, browser=browser),
                            "success",
                        ),
                    )
                )
                ui_bridge.post(
                    lambda: QTimer.singleShot(
                        6000,
                        status,
                        lambda: (
                            status.setText(""),
                            status.setStyleSheet(
                                f"color: {T.text2}; font-size: 11px; background: transparent;"
                            ),
                        ),
                    )
                )
            ui_bridge.post(lambda: [btn.setEnabled(True) for btn in self._pc_extract_btns])

        status.setText(t("settings.network.reading_platform_status", platform=platform_name, browser=browser))
        threading.Thread(
            target=_worker,
            daemon=True,
            name=f"omnidl-cookie-extract-{platform_key}",
        ).start()

    def _extract_platform_cdp(self, platform_key: str, path_lbl: QLabel) -> None:
        browser = self._browser_combo.currentText()
        if browser not in ("brave", "chrome", "chromium", "edge"):
            self._app.toast(t("settings.network.cdp_unsupported_use_browser", browser=browser), "error")
            return
        platform_name = _PLATFORM_NAMES.get(platform_key, platform_key.title())
        safe_dir = self._app.config.config_path.parent / "cookies"
        output_path = safe_dir / f"{platform_key}_{browser}_cdp_cookies.txt"
        status = self._pc_extract_status
        old_path_str = self._app.config.get_cookie_for_platform(platform_key)
        for btn in self._pc_extract_btns:
            btn.setEnabled(False)

        def _worker():
            try:
                from infrastructure.downloader.cookie_extractor import extract_via_cdp

                count, error = extract_via_cdp(output_path, platform_key=platform_key, browser=browser)
            except Exception as exc:
                err_msg = str(exc)
                ui_bridge.post(
                    lambda e=err_msg, pn=platform_name: (
                        status.setText(
                            t(
                                "settings.network.platform_cdp_failed_status",
                                platform=pn,
                                err=e.splitlines()[0][:60],
                            )
                        ),
                        status.setStyleSheet(f"color: {T.error}; font-size: 11px; background: transparent;"),
                        self._app.toast(
                            t(
                                "settings.network.platform_cdp_failed_toast",
                                platform=pn,
                                err=e.splitlines()[0][:50],
                            ),
                            "error",
                        ),
                    )
                )
                ui_bridge.post(lambda: [btn.setEnabled(True) for btn in self._pc_extract_btns])
                return
            path_str = self._resolve_saved_cookie_path(output_path)
            self._app.config.set_cookie_for_platform(platform_key, path_str)
            self._delete_old_cookie_if_replaced(old_path_str, Path(path_str))
            ui_bridge.post(
                lambda c=count, ps=path_str, pn=platform_name: (
                    path_lbl.setText(self._short_cookie_path(ps)),
                    status.setText(t("settings.network.platform_cdp_saved_status", platform=pn, count=c)),
                    status.setStyleSheet(f"color: {T.success}; font-size: 11px; background: transparent;"),
                    self._app.toast(
                        t("settings.network.platform_cdp_toast", count=c, platform=pn), "success"
                    ),
                )
            )
            ui_bridge.post(
                lambda: QTimer.singleShot(
                    6000,
                    status,
                    lambda: (
                        status.setText(""),
                        status.setStyleSheet(f"color: {T.text2}; font-size: 11px; background: transparent;"),
                    ),
                )
            )
            ui_bridge.post(lambda: [btn.setEnabled(True) for btn in self._pc_extract_btns])

        status.setText(
            t(
                "settings.network.starting_cdp_platform_status",
                browser=browser.title(),
                platform=platform_name,
            )
        )
        threading.Thread(
            target=_worker,
            daemon=True,
            name=f"omnidl-cdp-extract-{platform_key}",
        ).start()

    def _delete_old_cookie_if_replaced(self, old_path_str: str, new_path: Path) -> None:
        if not old_path_str:
            return
        safe_dir = self._app.config.config_path.parent.resolve()
        try:
            new_resolved = new_path.resolve()
        except OSError:
            new_resolved = new_path
        for candidate in _cookie_file_candidates(old_path_str):
            try:
                resolved = candidate.resolve()
                if resolved == new_resolved:
                    continue
                if safe_dir not in resolved.parents and resolved != safe_dir:
                    logger.warning("_delete_old_cookie_if_replaced: rejected: %s", candidate)
                    continue
                if resolved.is_file():
                    resolved.unlink()
                    invalidate_cookie_cache(resolved)
                    logger.info("Deleted replaced cookie file: %s", resolved.name)
            except OSError as exc:
                logger.warning("_delete_old_cookie_if_replaced: could not delete %s — %s", candidate, exc)

    def _clear_platform_cookie(self, platform_key: str, path_lbl: QLabel) -> None:
        old_path_str = self._app.config.get_cookie_for_platform(platform_key)
        self._app.config.set_cookie_for_platform(platform_key, "")
        path_lbl.setText(t("settings.network.no_file_selected"))
        if old_path_str:
            safe_dir = self._app.config.config_path.parent.resolve()
            for candidate in _cookie_file_candidates(old_path_str):
                try:
                    resolved = candidate.resolve()
                    if safe_dir not in resolved.parents and resolved != safe_dir:
                        logger.warning("_clear_platform_cookie: rejected: %s", candidate)
                        continue
                    if resolved.is_file():
                        resolved.unlink()
                        invalidate_cookie_cache(resolved)
                        logger.info("Deleted platform cookie file on clear: %s", resolved.name)
                except OSError as exc:
                    logger.warning("_clear_platform_cookie: could not delete %s — %s", candidate, exc)

    @staticmethod
    def _short_cookie_path(path: str) -> str:
        if not path:
            return t("settings.network.no_file_selected")
        s = str(Path(path))
        return s if len(s) <= 45 else f"…{s[-42:]}"

    @staticmethod
    def _resolve_saved_cookie_path(output_path: Path) -> str:
        enc = output_path.with_suffix(".enc")
        if enc.exists():
            return str(enc)
        return str(output_path)
