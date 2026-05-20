"""Settings panel: Network & Authentication · Global Cookie · Per-Platform Cookies (PySide6)."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QWidget,
)

from ui.signals import ui_bridge
from ui.tabs.settings._base_panel import _BasePanel
from ui.themes.tokens import T

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
    ("tiktok", "TikTok"),
    ("instagram", "Instagram"),
    ("facebook", "Facebook"),
    ("twitter", "Twitter / X"),
    ("threads", "Threads"),
    ("kuaishou", "Kuaishou"),
]

_INPUT_SS = lambda border_color="": (
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
        self._section(None, "🔒   NETWORK & AUTHENTICATION")
        net = self._card()

        proxy_row = QWidget()
        proxy_row.setStyleSheet("background: transparent;")
        phl = QHBoxLayout(proxy_row)
        phl.setContentsMargins(16, 12, 16, 4)
        phl.addWidget(QLabel("Proxy URL"))
        phl.addStretch()
        self._proxy_entry = QLineEdit()
        self._proxy_entry.setFixedSize(260, 32)
        self._proxy_entry.setStyleSheet(_INPUT_SS())
        self._proxy_entry.setPlaceholderText("http://host:port")
        self._proxy_entry.setText(cfg.proxy)
        self._proxy_entry.editingFinished.connect(self._on_proxy_focusout)
        phl.addWidget(self._proxy_entry)
        net.layout().addWidget(proxy_row)

        self._switch_row(net, "Use browser cookies", cfg.use_cookies, lambda v: cfg.set("use_cookies", v))

        browser_row = QWidget()
        browser_row.setStyleSheet("background: transparent;")
        bhl = QHBoxLayout(browser_row)
        bhl.setContentsMargins(16, 4, 16, 4)
        bhl.addWidget(QLabel("Cookie source browser"))
        bhl.addStretch()
        self._browser_combo = QComboBox()
        self._browser_combo.addItems(["chrome", "firefox", "safari", "edge", "opera", "brave"])
        self._browser_combo.setCurrentText(cfg.cookies_browser)
        self._browser_combo.setFixedWidth(130)
        self._browser_combo.currentTextChanged.connect(lambda v: cfg.set("cookies_browser", v))
        bhl.addWidget(self._browser_combo)
        net.layout().addWidget(browser_row)

        self._row_label(
            net,
            "🌐  Cookie fallback — cho YouTube, Twitch, Vimeo...  "
            "(dùng khi nền tảng chưa có trong bảng Per-Platform bên dưới)",
            T.text2,
        )
        self._row_label(
            net,
            "⚠  File này chứa toàn bộ cookies của trình duyệt (Google, email, banking...).\n"
            "   Ưu tiên dùng bảng Per-Platform bên dưới để bảo mật hơn.",
            T.warning_text,
            wrap=True,
        )

        ext_row = QWidget()
        ext_row.setStyleSheet("background: transparent;")
        ehl = QHBoxLayout(ext_row)
        ehl.setContentsMargins(16, 0, 16, 4)
        self._extract_global_btn = QPushButton("🔄  Firefox / Edge / Opera")
        self._extract_global_btn.setFixedHeight(30)
        self._extract_global_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._extract_global_btn.setStyleSheet(
            f"background: {T.surface3}; color: {T.text2}; border-radius: 8px; border: none; font-size: 12px;"
        )
        self._extract_global_btn.clicked.connect(self._extract_global_cookies)
        ehl.addWidget(self._extract_global_btn)
        self._extract_cdp_btn = QPushButton("🦁  Brave / Chrome 127+")
        self._extract_cdp_btn.setFixedHeight(30)
        self._extract_cdp_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._extract_cdp_btn.setStyleSheet(
            f"background: {T.surface3}; color: {T.text2}; border-radius: 8px; border: none; font-size: 12px;"
        )
        self._extract_cdp_btn.clicked.connect(self._extract_global_cdp)
        ehl.addWidget(self._extract_cdp_btn)
        self._extract_global_status = QLabel("")
        self._extract_global_status.setStyleSheet(
            f"color: {T.text2}; font-size: 11px; background: transparent;"
        )
        ehl.addWidget(self._extract_global_status)
        ehl.addStretch()
        net.layout().addWidget(ext_row)

        self._row_label(
            net,
            "🔄 = yt-dlp đọc trực tiếp (cần đóng Brave/Chrome trước).  "
            "🦁 = CDP — không cần đóng trình duyệt, Brave 127+ an toàn.",
        )

        self._row_label(net, "📁  Hoặc import file .txt thủ công:")

        cf_row = QWidget()
        cf_row.setStyleSheet("background: transparent;")
        cfhl = QHBoxLayout(cf_row)
        cfhl.setContentsMargins(16, 0, 16, 14)
        self._cf_lbl = QLabel(self._short_cookie_path(cfg.cookie_file))
        self._cf_lbl.setStyleSheet(f"color: {T.primary_text}; font-size: 11px; background: transparent;")
        cfhl.addWidget(self._cf_lbl, 1)
        self._browse_cf_btn = QPushButton("Browse…")
        self._browse_cf_btn.setFixedSize(80, 28)
        self._browse_cf_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._browse_cf_btn.setStyleSheet(
            f"background: {T.surface3}; color: {T.text2}; border-radius: 6px; border: none; font-size: 11px; padding: 0 6px;"
        )
        self._browse_cf_btn.clicked.connect(self._browse_cookie_file)
        cfhl.addWidget(self._browse_cf_btn)
        self._clear_cf_btn = QPushButton("🗑 Clear")
        self._clear_cf_btn.setFixedSize(72, 28)
        self._clear_cf_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_cf_btn.setStyleSheet(
            f"background: {T.error_bg}; color: {T.error}; border-radius: 6px; border: none; font-size: 11px; padding: 0 6px;"
        )
        self._clear_cf_btn.clicked.connect(self._clear_cookie_file)
        cfhl.addWidget(self._clear_cf_btn)
        net.layout().addWidget(cf_row)

        # -- Per-platform cookies ------------------------------------------
        self._section(None, "🍪   PER-PLATFORM COOKIES  ✅ Khuyến nghị — bảo mật hơn")
        pc_card = self._card()

        self._row_label(
            pc_card,
            "✅ Ưu tiên dùng bảng này — mỗi file chỉ chứa cookies của đúng nền tảng đó.\n"
            "File TikTok không có cookies Google/email, file Instagram không có cookies banking.\n"
            "Nếu nền tảng có hàng riêng ở đây → KHÔNG cần dùng Cookie fallback bên trên.",
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
            cdp_btn = QPushButton("CDP")
            cdp_btn.setFixedSize(48, 28)
            cdp_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            cdp_btn.setToolTip("CDP — Brave/Chrome 127+ (không cần đóng trình duyệt)")
            cdp_btn.setStyleSheet(f"background: {T.surface3}; color: {T.text2}; {_pc_btn_ss}")
            cdp_btn.clicked.connect(lambda _=False, k=key, lbl=path_lbl: self._extract_platform_cdp(k, lbl))
            rhl.addWidget(cdp_btn)
            self._pc_extract_btns.append(cdp_btn)

            extract_btn = QPushButton("yt-dlp")
            extract_btn.setFixedSize(54, 28)
            extract_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            extract_btn.setToolTip("yt-dlp — Firefox / Edge / Opera (cần đóng Brave/Chrome trước)")
            extract_btn.setStyleSheet(f"background: {T.surface3}; color: {T.text2}; {_pc_btn_ss}")
            extract_btn.clicked.connect(
                lambda _=False, k=key, lbl=path_lbl: self._extract_platform_cookie(k, lbl)
            )
            rhl.addWidget(extract_btn)
            self._pc_extract_btns.append(extract_btn)

            browse_btn = QPushButton("Chọn")
            browse_btn.setFixedSize(48, 28)
            browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            browse_btn.setToolTip("Chọn file cookie thủ công (.txt Netscape)")
            browse_btn.setStyleSheet(f"background: {T.surface3}; color: {T.text2}; {_pc_btn_ss}")
            browse_btn.clicked.connect(
                lambda _=False, k=key, lbl=path_lbl: self._browse_platform_cookie(k, lbl)
            )
            rhl.addWidget(browse_btn)
            self._pc_browse_btns.append(browse_btn)

            clear_btn = QPushButton("Xóa")
            clear_btn.setFixedSize(44, 28)
            clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            clear_btn.setToolTip("Xóa cookie file của nền tảng này")
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

        self._row_label(
            pc_card, "🔄 = yt-dlp (Firefox/Opera).  🦁 = CDP (Brave/Chrome 127+, không cần đóng trình duyệt)."
        )

        self._layout.addSpacing(20)

    # ── Handlers — Network ────────────────────────────────────────────────

    def _on_proxy_focusout(self) -> None:
        val = self._proxy_entry.text().strip()
        if not val:
            self._app.config.set("proxy", "")
            return
        _valid_schemes = ("http://", "https://", "socks4://", "socks5://")
        if any(val.lower().startswith(s) for s in _valid_schemes):
            self._app.config.set("proxy", val)
            self._proxy_entry.setStyleSheet("")
        else:
            self._proxy_entry.setStyleSheet(
                f"QLineEdit {{ background: {T.input}; color: {T.text}; border: 1px solid {T.error};"
                f" border-radius: 8px; padding: 6px 12px; }}"
            )
            QTimer.singleShot(1500, lambda: self._proxy_entry.setStyleSheet(""))
            self._app.toast(
                "Proxy không hợp lệ — phải bắt đầu bằng http://, https://, socks4://, hoặc socks5://",
                "error",
            )

    # ── Handlers — Global Cookie ──────────────────────────────────────────

    def _browse_cookie_file(self) -> None:
        import shutil

        from infrastructure.downloader.cookie_storage import encrypt_cookie_file

        chosen, _ = QFileDialog.getOpenFileName(
            self, "Select cookies.txt (Netscape format)", "", "Cookie files (*.txt);;All files (*.*)"
        )
        if not chosen:
            return
        src = Path(chosen)
        if not src.is_file():
            self._app.toast("File not found.", "error")
            return
        if not _is_netscape_cookie_file(src):
            self._app.toast(
                "File không phải định dạng Netscape cookie.\n"
                "Hãy chọn file cookies.txt được export từ trình duyệt hoặc tiện ích Cookie-Editor.",
                "error",
            )
            return
        old_path_str = self._app.config.get("cookie_file", "")
        safe_dir = self._app.config.config_path.parent / "cookies"
        safe_dir.mkdir(parents=True, exist_ok=True)
        dest = safe_dir / src.name
        try:
            shutil.copy2(src, dest)
        except OSError as exc:
            logger.warning("Failed to copy cookie file: %s", exc)
            self._app.toast(f"Cannot copy cookie file: {exc}", "error")
            return
        dest = encrypt_cookie_file(dest)
        self._app.config.set("cookie_file", str(dest))
        self._cf_lbl.setText(self._short_cookie_path(str(dest)))
        self._app.toast("Cookie file đã được mã hóa và lưu vào thư mục an toàn.", "info")
        self._delete_old_cookie_if_replaced(old_path_str, dest)

    def _clear_cookie_file(self) -> None:
        old_path_str = self._app.config.get("cookie_file", "")
        self._app.config.set("cookie_file", "")
        self._cf_lbl.setText("No file selected")
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
                        logger.info("Deleted global cookie file on clear: %s", resolved.name)
                except OSError as exc:
                    logger.warning("_clear_cookie_file: could not delete %s — %s", candidate, exc)

    def _extract_global_cdp(self) -> None:
        reply = QMessageBox.question(
            self,
            "OmniDL — Xác nhận lấy toàn bộ cookies (CDP)",
            "⚠ Thao tác này lấy TẤT CẢ cookies của Brave/Chrome,\n"
            "bao gồm cả Google, email, banking...\n\n"
            "Cookies sẽ được mã hóa DPAPI và chỉ lưu trên máy này.\n"
            "Một port ngẫu nhiên trên localhost sẽ được mở trong ~10 giây.\n\n"
            "➡ Khuyến nghị: Dùng nút 🦁 ở từng platform bên dưới\n"
            "   để chỉ lấy đúng cookies cần thiết (an toàn hơn).\n\n"
            "Tiếp tục lấy toàn bộ?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        browser = self._browser_combo.currentText()
        if browser not in ("brave", "chrome", "chromium", "edge"):
            self._app.toast(
                f"CDP chỉ hỗ trợ Brave/Chrome/Edge. Trình duyệt hiện tại: {browser}.\n"
                "Dùng nút 🔄 cho Firefox/Opera/Safari.",
                "error",
            )
            return
        safe_dir = self._app.config.config_path.parent / "cookies"
        output_path = safe_dir / f"{browser}_cdp_cookies.txt"
        btn = self._extract_cdp_btn
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
                        status.setText(f"❌ {e.splitlines()[0][:70]}"),
                        status.setStyleSheet(f"color: {T.error}; font-size: 11px; background: transparent;"),
                        self._app.toast(f"CDP thất bại: {e.splitlines()[0][:60]}", "error"),
                    )
                )
                ui_bridge.post(lambda: btn.setEnabled(True))
                return
            path_str = self._resolve_saved_cookie_path(output_path)
            self._app.config.set("cookie_file", path_str)
            self._delete_old_cookie_if_replaced(old_path_str, Path(path_str))
            ui_bridge.post(
                lambda c=count, ps=path_str: (
                    self._cf_lbl.setText(self._short_cookie_path(ps)),
                    status.setText(f"✓ {c} cookies đã lưu (CDP)"),
                    status.setStyleSheet(f"color: {T.success}; font-size: 11px; background: transparent;"),
                    self._app.toast(f"CDP: đã lấy {c} cookies từ {browser.title()}.", "success"),
                )
            )
            ui_bridge.post(
                lambda: QTimer.singleShot(
                    6000,
                    lambda: (
                        status.setText(""),
                        status.setStyleSheet(f"color: {T.text2}; font-size: 11px; background: transparent;"),
                    ),
                )
            )
            ui_bridge.post(lambda: btn.setEnabled(True))

        btn.setEnabled(False)
        status.setText(f"Đang khởi động {browser.title()} (CDP)…")
        threading.Thread(target=_worker, daemon=True, name="omnidl-cdp-extract").start()

    def _extract_global_cookies(self) -> None:
        reply = QMessageBox.question(
            self,
            "OmniDL — Xác nhận lấy toàn bộ cookies",
            "⚠ Thao tác này lấy TẤT CẢ cookies của trình duyệt,\n"
            "bao gồm cả Google, email, banking...\n\n"
            "Cookies sẽ được mã hóa DPAPI và chỉ lưu trên máy này.\n\n"
            "➡ Khuyến nghị: Dùng nút 🔄 / 🦁 ở từng platform bên dưới\n"
            "   để chỉ lấy đúng cookies cần thiết (an toàn hơn).\n\n"
            "Tiếp tục lấy toàn bộ?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        browser = self._browser_combo.currentText()
        safe_dir = self._app.config.config_path.parent / "cookies"
        output_path = safe_dir / f"{browser}_global_cookies.txt"
        btn = self._extract_global_btn
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
                        status.setText(f"❌ {e.splitlines()[0][:70]}"),
                        status.setStyleSheet(f"color: {T.error}; font-size: 11px; background: transparent;"),
                        self._app.toast(f"Lấy cookies thất bại: {e.splitlines()[0][:60]}", "error"),
                    )
                )
            else:
                enc_candidate = output_path.parent / (output_path.stem + ".enc")
                final_path = enc_candidate if enc_candidate.exists() else output_path
                path_str = str(final_path)
                self._app.config.set("cookie_file", path_str)
                self._delete_old_cookie_if_replaced(old_path_str, final_path)
                enc_note = " 🔒 (mã hóa DPAPI)" if path_str.endswith(".enc") else ""
                ui_bridge.post(
                    lambda c=count, ps=path_str, n=enc_note: (
                        self._cf_lbl.setText(self._short_cookie_path(ps)),
                        status.setText(f"✓ {c} cookies đã lưu{n}"),
                        status.setStyleSheet(
                            f"color: {T.success}; font-size: 11px; background: transparent;"
                        ),
                        self._app.toast(f"Đã lấy {c} cookies từ {browser}{n}.", "success"),
                    )
                )
                ui_bridge.post(
                    lambda: QTimer.singleShot(
                        6000,
                        lambda: (
                            status.setText(""),
                            status.setStyleSheet(
                                f"color: {T.text2}; font-size: 11px; background: transparent;"
                            ),
                        ),
                    )
                )
            ui_bridge.post(lambda: btn.setEnabled(True))

        btn.setEnabled(False)
        status.setText(f"Đang đọc cookies từ {browser}…")
        threading.Thread(target=_worker, daemon=True, name="omnidl-cookie-extract").start()

    # ── Handlers — Per-Platform Cookies ───────────────────────────────────

    def _browse_platform_cookie(self, platform_key: str, path_lbl: QLabel) -> None:
        import shutil

        platform_name = {
            "tiktok": "TikTok",
            "instagram": "Instagram",
            "facebook": "Facebook",
            "twitter": "Twitter/X",
            "threads": "Threads",
            "kuaishou": "Kuaishou",
        }.get(platform_key, platform_key.title())
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            f"Chọn cookie file cho {platform_name} (Netscape format)",
            "",
            "Cookie files (*.txt);;All files (*.*)",
        )
        if not chosen:
            return
        src_path = Path(chosen)
        if not src_path.is_file():
            self._app.toast("File không tìm thấy.", "error")
            return
        if not _is_netscape_cookie_file(src_path):
            self._app.toast(
                "File không phải định dạng Netscape cookie.\n"
                "Hãy chọn file cookies.txt được export từ trình duyệt hoặc tiện ích Cookie-Editor.",
                "error",
            )
            return
        old_path_str = self._app.config.get_cookie_for_platform(platform_key)
        safe_dir = self._app.config.config_path.parent / "cookies"
        safe_dir.mkdir(parents=True, exist_ok=True)
        dest = safe_dir / f"{platform_key}_{src_path.name}"
        try:
            shutil.copy2(src_path, dest)
        except OSError as exc:
            logger.warning("Failed to copy platform cookie file: %s", exc)
            self._app.toast(f"Không thể sao chép cookie file: {exc}", "error")
            return
        from infrastructure.downloader.cookie_storage import encrypt_cookie_file

        dest = encrypt_cookie_file(dest)
        self._app.config.set_cookie_for_platform(platform_key, str(dest))
        path_lbl.setText(self._short_cookie_path(str(dest)))
        self._app.toast(f"Cookie {platform_name} đã được mã hóa và lưu vào thư mục an toàn.", "info")
        self._delete_old_cookie_if_replaced(old_path_str, dest)

    def _extract_platform_cookie(self, platform_key: str, path_lbl: QLabel) -> None:
        browser = self._browser_combo.currentText()
        platform_name = {
            "tiktok": "TikTok",
            "instagram": "Instagram",
            "facebook": "Facebook",
            "twitter": "Twitter/X",
            "threads": "Threads",
            "kuaishou": "Kuaishou",
        }.get(platform_key, platform_key.title())
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
                        status.setText(f"❌ {pn}: {e.splitlines()[0][:65]}"),
                        status.setStyleSheet(f"color: {T.error}; font-size: 11px; background: transparent;"),
                        self._app.toast(f"Lấy cookies {pn} thất bại: {e.splitlines()[0][:55]}", "error"),
                    )
                )
            else:
                path_str = self._resolve_saved_cookie_path(output_path)
                self._app.config.set_cookie_for_platform(platform_key, path_str)
                self._delete_old_cookie_if_replaced(old_path_str, Path(path_str))
                ui_bridge.post(
                    lambda c=count, ps=path_str, pn=platform_name: (
                        path_lbl.setText(self._short_cookie_path(ps)),
                        status.setText(f"✓ {pn}: {c} cookies đã lưu"),
                        status.setStyleSheet(
                            f"color: {T.success}; font-size: 11px; background: transparent;"
                        ),
                        self._app.toast(f"Đã lấy {c} cookies {pn} từ {browser}.", "success"),
                    )
                )
                ui_bridge.post(
                    lambda: QTimer.singleShot(
                        6000,
                        lambda: (
                            status.setText(""),
                            status.setStyleSheet(
                                f"color: {T.text2}; font-size: 11px; background: transparent;"
                            ),
                        ),
                    )
                )
            ui_bridge.post(lambda: [btn.setEnabled(True) for btn in self._pc_extract_btns])

        status.setText(f"Đang đọc cookies {platform_name} từ {browser}…")
        threading.Thread(
            target=_worker,
            daemon=True,
            name=f"omnidl-cookie-extract-{platform_key}",
        ).start()

    def _extract_platform_cdp(self, platform_key: str, path_lbl: QLabel) -> None:
        browser = self._browser_combo.currentText()
        if browser not in ("brave", "chrome", "chromium", "edge"):
            self._app.toast(f"CDP chỉ hỗ trợ Brave/Chrome/Edge. Dùng 🔄 cho {browser}.", "error")
            return
        platform_name = {
            "tiktok": "TikTok",
            "instagram": "Instagram",
            "facebook": "Facebook",
            "twitter": "Twitter/X",
            "threads": "Threads",
            "kuaishou": "Kuaishou",
        }.get(platform_key, platform_key.title())
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
                        status.setText(f"❌ {pn} CDP: {e.splitlines()[0][:60]}"),
                        status.setStyleSheet(f"color: {T.error}; font-size: 11px; background: transparent;"),
                        self._app.toast(f"CDP {pn} thất bại: {e.splitlines()[0][:50]}", "error"),
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
                    status.setText(f"✓ {pn}: {c} cookies (CDP)"),
                    status.setStyleSheet(f"color: {T.success}; font-size: 11px; background: transparent;"),
                    self._app.toast(f"CDP: đã lấy {c} cookies {pn}.", "success"),
                )
            )
            ui_bridge.post(
                lambda: QTimer.singleShot(
                    6000,
                    lambda: (
                        status.setText(""),
                        status.setStyleSheet(f"color: {T.text2}; font-size: 11px; background: transparent;"),
                    ),
                )
            )
            ui_bridge.post(lambda: [btn.setEnabled(True) for btn in self._pc_extract_btns])

        status.setText(f"Đang khởi động {browser.title()} để lấy cookies {platform_name}…")
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
                    logger.info("Deleted replaced cookie file: %s", resolved.name)
            except OSError as exc:
                logger.warning("_delete_old_cookie_if_replaced: could not delete %s — %s", candidate, exc)

    def _clear_platform_cookie(self, platform_key: str, path_lbl: QLabel) -> None:
        old_path_str = self._app.config.get_cookie_for_platform(platform_key)
        self._app.config.set_cookie_for_platform(platform_key, "")
        path_lbl.setText("No file selected")
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
                        logger.info("Deleted platform cookie file on clear: %s", resolved.name)
                except OSError as exc:
                    logger.warning("_clear_platform_cookie: could not delete %s — %s", candidate, exc)

    @staticmethod
    def _short_cookie_path(path: str) -> str:
        if not path:
            return "No file selected"
        s = str(Path(path))
        return s if len(s) <= 45 else f"…{s[-42:]}"

    @staticmethod
    def _resolve_saved_cookie_path(output_path: Path) -> str:
        enc = output_path.with_suffix(".enc")
        if enc.exists():
            return str(enc)
        return str(output_path)
