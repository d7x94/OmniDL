"""Settings panel: YT-DLP Engine · Gallery-DL Engine · Data & Privacy (PySide6).

Module-level helpers:
  _install_ytdlp_frozen()      - PyInstaller-safe yt-dlp wheel updater
  _install_gallery_dl_frozen() - PyInstaller-safe gallery-dl wheel updater
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING
from urllib.error import URLError
from urllib.request import Request, urlopen  # noqa: S310

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
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


# ── Module-level frozen-build updaters ────────────────────────────────────────


def _install_ytdlp_frozen() -> str:
    import hashlib
    import json
    import os
    import shutil
    import zipfile
    from pathlib import Path as _Path

    override_dir = _Path(os.getenv("APPDATA", str(_Path.home()))) / "OmniDL" / "site-packages"
    override_dir.mkdir(parents=True, exist_ok=True)

    pypi_url = "https://pypi.org/pypi/yt-dlp/json"
    try:
        req = Request(pypi_url, headers={"User-Agent": "OmniDL-updater/1.0"})
        with urlopen(req, timeout=20) as resp:  # nosec B310
            meta = json.loads(resp.read())
    except URLError as exc:
        raise RuntimeError(f"Cannot reach PyPI: {exc}") from exc

    latest_ver = meta["info"]["version"]
    wheel_url = wheel_sha256 = None
    for release in meta["releases"].get(latest_ver, []):
        fn = release["filename"]
        if fn.endswith(".whl") and "none-any" in fn:
            wheel_url = release["url"]
            wheel_sha256 = (release.get("digests") or {}).get("sha256")
            break
    if not wheel_url:
        raise RuntimeError(f"No universal wheel found for yt-dlp {latest_ver}")
    if not wheel_sha256:
        raise RuntimeError(f"PyPI did not provide SHA-256 for yt-dlp {latest_ver}")
    if not wheel_url.startswith("https://"):
        raise RuntimeError(f"Unexpected wheel URL scheme: {wheel_url!r}")

    tmp_whl = override_dir.parent / "yt_dlp_update.whl"
    try:
        req = Request(wheel_url, headers={"User-Agent": "OmniDL-updater/1.0"})
        with urlopen(req, timeout=120) as resp, open(tmp_whl, "wb") as fout:  # nosec B310
            import shutil as _sh

            _sh.copyfileobj(resp, fout)
    except URLError as exc:
        raise RuntimeError(f"Download failed: {exc}") from exc

    try:
        digest = hashlib.sha256(tmp_whl.read_bytes()).hexdigest()
    except OSError as exc:
        tmp_whl.unlink(missing_ok=True)
        raise RuntimeError(f"Could not read wheel for verification: {exc}") from exc
    if digest.lower() != wheel_sha256.lower():
        tmp_whl.unlink(missing_ok=True)
        raise RuntimeError(f"SHA-256 mismatch for yt-dlp {latest_ver}")

    old_tree = override_dir / "yt_dlp"
    if old_tree.exists():
        shutil.rmtree(old_tree, ignore_errors=True)
    try:
        with zipfile.ZipFile(tmp_whl) as zf:
            for member in zf.namelist():
                if member.startswith("yt_dlp/"):
                    zf.extract(member, override_dir)
    finally:
        tmp_whl.unlink(missing_ok=True)
    return str(override_dir)


def _install_gallery_dl_frozen() -> str:
    import hashlib
    import json
    import os
    import shutil
    import zipfile
    from pathlib import Path as _Path

    override_dir = _Path(os.getenv("APPDATA", str(_Path.home()))) / "OmniDL" / "site-packages"
    override_dir.mkdir(parents=True, exist_ok=True)

    pypi_url = "https://pypi.org/pypi/gallery-dl/json"
    try:
        req = Request(pypi_url, headers={"User-Agent": "OmniDL-updater/1.0"})
        with urlopen(req, timeout=20) as resp:  # nosec B310
            meta = json.loads(resp.read())
    except URLError as exc:
        raise RuntimeError(f"Cannot reach PyPI: {exc}") from exc

    latest_ver = meta["info"]["version"]
    wheel_url = wheel_sha256 = None
    for release in meta["releases"].get(latest_ver, []):
        fn = release["filename"]
        if fn.endswith(".whl") and "none-any" in fn:
            wheel_url = release["url"]
            wheel_sha256 = (release.get("digests") or {}).get("sha256")
            break
    if not wheel_url:
        raise RuntimeError(f"No universal wheel found for gallery-dl {latest_ver}")
    if not wheel_sha256:
        raise RuntimeError(f"PyPI did not provide SHA-256 for gallery-dl {latest_ver}")
    if not wheel_url.startswith("https://"):
        raise RuntimeError(f"Unexpected wheel URL scheme: {wheel_url!r}")

    tmp_whl = override_dir.parent / "gallery_dl_update.whl"
    try:
        req = Request(wheel_url, headers={"User-Agent": "OmniDL-updater/1.0"})
        with urlopen(req, timeout=120) as resp, open(tmp_whl, "wb") as fout:  # nosec B310
            import shutil as _sh

            _sh.copyfileobj(resp, fout)
    except URLError as exc:
        raise RuntimeError(f"Download failed: {exc}") from exc

    try:
        digest = hashlib.sha256(tmp_whl.read_bytes()).hexdigest()
    except OSError as exc:
        tmp_whl.unlink(missing_ok=True)
        raise RuntimeError(f"Could not read wheel for verification: {exc}") from exc
    if digest.lower() != wheel_sha256.lower():
        tmp_whl.unlink(missing_ok=True)
        raise RuntimeError(f"SHA-256 mismatch for gallery-dl {latest_ver}")

    old_tree = override_dir / "gallery_dl"
    if old_tree.exists():
        shutil.rmtree(old_tree, ignore_errors=True)
    try:
        with zipfile.ZipFile(tmp_whl) as zf:
            for member in zf.namelist():
                if member.startswith("gallery_dl/"):
                    zf.extract(member, override_dir)
    finally:
        tmp_whl.unlink(missing_ok=True)
    return str(override_dir)


def _pip_install_cmd(package: str) -> list[str]:
    import shutil
    import sys

    if not getattr(sys, "frozen", False):
        uv_exe = shutil.which("uv")
        if uv_exe:
            return [uv_exe, "pip", "install", "--upgrade", package]
    return [sys.executable, "-m", "pip", "install", "--upgrade", package]


# ── Panel class ───────────────────────────────────────────────────────────────


class ToolsPanel(_BasePanel):
    def __init__(self, master, app: "MainWindow") -> None:
        super().__init__(master, app)
        self._build()

    def _build(self) -> None:
        cfg = self._app.config

        # -- yt-dlp engine -------------------------------------------------
        sec_ytdlp = self._collapsible_section("YT-DLP ENGINE", "tools_ytdlp", icon="🔧")
        ytdlp = self._card(container=sec_ytdlp)

        ver_row = QWidget()
        ver_row.setStyleSheet("background: transparent;")
        vhl = QHBoxLayout(ver_row)
        vhl.setContentsMargins(16, 12, 16, 4)
        vhl.addWidget(QLabel("Phiên bản"))
        vhl.addStretch()
        self._ver_lbl = QLabel(self._get_ytdlp_version())
        self._ver_lbl.setStyleSheet(f"color: {T.primary_text}; font-size: 12px; background: transparent;")
        vhl.addWidget(self._ver_lbl)
        ytdlp.layout().addWidget(ver_row)

        upd_row = QWidget()
        upd_row.setStyleSheet("background: transparent;")
        uhl = QHBoxLayout(upd_row)
        uhl.setContentsMargins(16, 4, 16, 4)
        self._upd_btn = QPushButton("Cập nhật yt-dlp")
        self._upd_btn.setFixedHeight(32)
        self._upd_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._upd_btn.setStyleSheet(
            f"background: {T.surface3}; color: {T.text2}; border-radius: 8px; border: none; font-size: 12px;"
        )
        self._upd_btn.clicked.connect(self._update_ytdlp)
        uhl.addWidget(self._upd_btn)
        self._upd_status = QLabel("")
        self._upd_status.setStyleSheet(f"color: {T.text2}; font-size: 11px; background: transparent;")
        uhl.addWidget(self._upd_status)
        uhl.addStretch()
        ytdlp.layout().addWidget(upd_row)

        keyring_row = QWidget()
        keyring_row.setStyleSheet("background: transparent;")
        khl = QHBoxLayout(keyring_row)
        khl.setContentsMargins(16, 4, 16, 4)
        self._keyring_btn = QPushButton("Cài keyring (hỗ trợ Brave/Chrome 127+)")
        self._keyring_btn.setFixedHeight(32)
        self._keyring_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._keyring_btn.setStyleSheet(
            f"background: {T.surface3}; color: {T.text2}; border-radius: 8px; border: none; font-size: 12px;"
        )
        self._keyring_btn.clicked.connect(self._install_keyring)
        khl.addWidget(self._keyring_btn)
        self._keyring_status = QLabel(self._keyring_installed_text())
        self._keyring_status.setStyleSheet(f"color: {T.text2}; font-size: 11px; background: transparent;")
        khl.addWidget(self._keyring_status)
        khl.addStretch()
        ytdlp.layout().addWidget(keyring_row)
        self._row_label(ytdlp, "⚠  Cần thiết nếu Brave/Chrome báo lỗi DPAPI khi lấy cookies.", T.warning_text)

        extra_row = QWidget()
        extra_row.setStyleSheet("background: transparent;")
        ehl = QHBoxLayout(extra_row)
        ehl.setContentsMargins(16, 4, 16, 14)
        ehl.addWidget(QLabel("Tham số thêm"))
        ehl.addStretch()
        self._extra_entry = QLineEdit()
        self._extra_entry.setFixedSize(260, 32)
        self._extra_entry.setPlaceholderText("e.g. --no-playlist")
        self._extra_entry.setText(cfg.extra_args)
        self._extra_entry.editingFinished.connect(
            lambda: cfg.set("extra_args", self._extra_entry.text().strip())
        )
        ehl.addWidget(self._extra_entry)
        ytdlp.layout().addWidget(extra_row)

        # -- gallery-dl engine ---------------------------------------------
        sec_gdl = self._collapsible_section("GALLERY-DL ENGINE", "tools_gallery_dl", icon="🖼")
        gdl = self._card(container=sec_gdl)

        gdl_ver_row = QWidget()
        gdl_ver_row.setStyleSheet("background: transparent;")
        gvhl = QHBoxLayout(gdl_ver_row)
        gvhl.setContentsMargins(16, 12, 16, 4)
        gvhl.addWidget(QLabel("Phiên bản"))
        gvhl.addStretch()
        self._gdl_ver_lbl = QLabel(self._get_gallery_dl_version())
        self._gdl_ver_lbl.setStyleSheet(f"color: {T.primary_text}; font-size: 12px; background: transparent;")
        gvhl.addWidget(self._gdl_ver_lbl)
        gdl.layout().addWidget(gdl_ver_row)

        gdl_upd_row = QWidget()
        gdl_upd_row.setStyleSheet("background: transparent;")
        guhl = QHBoxLayout(gdl_upd_row)
        guhl.setContentsMargins(16, 4, 16, 14)
        self._gdl_upd_btn = QPushButton("Cập nhật gallery-dl")
        self._gdl_upd_btn.setFixedHeight(32)
        self._gdl_upd_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._gdl_upd_btn.setStyleSheet(
            f"background: {T.surface3}; color: {T.text2}; border-radius: 8px; border: none; font-size: 12px;"
        )
        self._gdl_upd_btn.clicked.connect(self._update_gallery_dl)
        guhl.addWidget(self._gdl_upd_btn)
        self._gdl_upd_status = QLabel("")
        self._gdl_upd_status.setStyleSheet(f"color: {T.text2}; font-size: 11px; background: transparent;")
        guhl.addWidget(self._gdl_upd_status)
        guhl.addStretch()
        gdl.layout().addWidget(gdl_upd_row)

        # -- Data & Privacy ------------------------------------------------
        sec_data = self._collapsible_section("DATA & PRIVACY", "tools_data_privacy", icon="🗑")
        data_card = self._card(container=sec_data)

        self._row_label(
            data_card,
            "Xóa toàn bộ dữ liệu ứng dụng: lịch sử tải, thiết lập cấu hình\n"
            "và đường dẫn cookie file. File cookie trên đĩa không bị xóa.\n"
            "Không thể thực hiện khi đang có download/conversion đang chạy.",
            wrap=True,
        )

        action_row = QWidget()
        action_row.setStyleSheet("background: transparent;")
        ahl = QHBoxLayout(action_row)
        ahl.setContentsMargins(16, 0, 16, 14)
        self._clear_data_btn = QPushButton("🗑  Xóa tất cả dữ liệu")
        self._clear_data_btn.setFixedHeight(34)
        self._clear_data_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_data_btn.setStyleSheet(
            f"background: {T.error_bg}; color: {T.error}; border-radius: 8px; border: none;"
            " font-size: 12px; font-weight: bold;"
        )
        self._clear_data_btn.clicked.connect(self._clear_all_data)
        ahl.addWidget(self._clear_data_btn)
        self._clear_data_status = QLabel("")
        self._clear_data_status.setStyleSheet(f"color: {T.text2}; font-size: 11px; background: transparent;")
        ahl.addWidget(self._clear_data_status)
        ahl.addStretch()
        data_card.layout().addWidget(action_row)

        self._layout.addSpacing(20)

    # ── YT-DLP handlers ───────────────────────────────────────────────────

    def _get_ytdlp_version(self) -> str:
        try:
            import yt_dlp

            return yt_dlp.version.__version__
        except Exception:
            return "unknown"

    def _update_ytdlp(self) -> None:
        import sys

        self._upd_btn.setEnabled(False)
        self._upd_status.setText("Checking for updates…")
        self._upd_status.setStyleSheet(f"color: {T.primary_text}; font-size: 11px; background: transparent;")
        self._app.toast("Updating yt-dlp, please wait…", "info")

        def _worker():
            import importlib

            try:
                if getattr(sys, "frozen", False):
                    override_str = _install_ytdlp_frozen()
                else:
                    override_str = None
                    import subprocess

                    r = subprocess.run(
                        _pip_install_cmd("yt-dlp"),
                        capture_output=True,
                        timeout=60,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    if r.returncode != 0:
                        raise RuntimeError(r.stderr.decode(errors="replace")[:200])

                def _apply_on_ui(override=override_str):
                    if override and override not in sys.path:
                        sys.path.insert(0, override)
                    importlib.invalidate_caches()
                    try:
                        import yt_dlp.version as _yv

                        importlib.reload(_yv)
                    except Exception:
                        pass
                    ver = self._get_ytdlp_version()
                    self._upd_status.setText(f"Updated → {ver}")
                    self._upd_status.setStyleSheet(
                        f"color: {T.success}; font-size: 11px; background: transparent;"
                    )
                    self._ver_lbl.setText(ver)
                    self._app.toast(f"yt-dlp updated to {ver}", "success")

                ui_bridge.post(_apply_on_ui)
            except Exception as exc:
                msg = str(exc)
                ui_bridge.post(
                    lambda m=msg: (
                        self._upd_status.setText(f"Error: {m[:80]}"),
                        self._upd_status.setStyleSheet(
                            f"color: {T.error}; font-size: 11px; background: transparent;"
                        ),
                        self._app.toast(f"Update failed: {m[:60]}", "error"),
                    )
                )
            finally:
                ui_bridge.post(lambda: self._upd_btn.setEnabled(True))

        threading.Thread(target=_worker, daemon=True, name="omnidl-ytdlp-update").start()

    @staticmethod
    def _keyring_installed_text() -> str:
        try:
            import keyring as _kr

            ver = getattr(_kr, "__version__", "installed")
            return f"✓ keyring {ver}"
        except ImportError:
            return "⚠ Chưa cài — cần cho Brave/Chrome 127+"

    def _install_keyring(self) -> None:
        import sys

        self._keyring_btn.setEnabled(False)

        if getattr(sys, "frozen", False):
            status_text = self._keyring_installed_text()
            try:
                import keyring  # noqa: F401

                self._keyring_status.setText(status_text + " (bundled)")
                self._keyring_status.setStyleSheet(
                    f"color: {T.success}; font-size: 11px; background: transparent;"
                )
                self._app.toast("keyring đã có sẵn trong bản build. Thử lại lấy cookies.", "success")
            except ImportError:
                self._keyring_status.setText("keyring không có — tải lại phiên bản mới hơn")
                self._keyring_status.setStyleSheet(
                    f"color: {T.error}; font-size: 11px; background: transparent;"
                )
                self._app.toast(
                    "keyring không tìm thấy trong build. Vui lòng tải phiên bản EXE mới nhất.", "error"
                )
            self._keyring_btn.setEnabled(True)
            return

        self._keyring_status.setText("Đang cài keyring…")
        self._keyring_status.setStyleSheet(
            f"color: {T.primary_text}; font-size: 11px; background: transparent;"
        )

        def _worker():
            import importlib
            import subprocess

            try:
                r = subprocess.run(
                    _pip_install_cmd("keyring"),
                    capture_output=True,
                    timeout=120,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if r.returncode != 0:
                    raise RuntimeError(r.stderr.decode(errors="replace")[:200])
                importlib.invalidate_caches()
                status_text = self._keyring_installed_text()
                ui_bridge.post(
                    lambda t=status_text: (
                        self._keyring_status.setText(t),
                        self._keyring_status.setStyleSheet(
                            f"color: {T.success}; font-size: 11px; background: transparent;"
                        ),
                        self._app.toast("keyring đã cài. Thử lại lấy cookies từ Brave/Chrome.", "success"),
                    )
                )
            except Exception as exc:
                msg = str(exc)[:80]
                ui_bridge.post(
                    lambda m=msg: (
                        self._keyring_status.setText(f"Lỗi: {m}"),
                        self._keyring_status.setStyleSheet(
                            f"color: {T.error}; font-size: 11px; background: transparent;"
                        ),
                        self._app.toast(f"Cài keyring thất bại: {m}", "error"),
                    )
                )
            finally:
                ui_bridge.post(lambda: self._keyring_btn.setEnabled(True))

        threading.Thread(target=_worker, daemon=True, name="omnidl-install-keyring").start()

    # ── Gallery-DL handlers ───────────────────────────────────────────────

    def _get_gallery_dl_version(self) -> str:
        try:
            import gallery_dl

            return getattr(gallery_dl, "__version__", "installed")
        except Exception:
            return "not installed"

    def _update_gallery_dl(self) -> None:
        import sys

        self._gdl_upd_btn.setEnabled(False)
        self._gdl_upd_status.setText("Checking for updates…")
        self._gdl_upd_status.setStyleSheet(
            f"color: {T.primary_text}; font-size: 11px; background: transparent;"
        )
        self._app.toast("Updating gallery-dl, please wait…", "info")

        def _worker():
            import importlib

            try:
                if getattr(sys, "frozen", False):
                    override_str = _install_gallery_dl_frozen()
                else:
                    override_str = None
                    import subprocess

                    r = subprocess.run(
                        _pip_install_cmd("gallery-dl"),
                        capture_output=True,
                        timeout=60,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    if r.returncode != 0:
                        raise RuntimeError(r.stderr.decode(errors="replace")[:200])

                def _apply_on_ui(override=override_str):
                    if override and override not in sys.path:
                        sys.path.insert(0, override)
                    importlib.invalidate_caches()
                    try:
                        import gallery_dl as _gdl

                        importlib.reload(_gdl)
                    except Exception:
                        pass
                    ver = self._get_gallery_dl_version()
                    self._gdl_upd_status.setText(f"Updated → {ver}")
                    self._gdl_upd_status.setStyleSheet(
                        f"color: {T.success}; font-size: 11px; background: transparent;"
                    )
                    self._gdl_ver_lbl.setText(ver)
                    self._app.toast(f"gallery-dl updated to {ver}", "success")

                ui_bridge.post(_apply_on_ui)
            except Exception as exc:
                msg = str(exc)
                ui_bridge.post(
                    lambda m=msg: (
                        self._gdl_upd_status.setText(f"Error: {m[:80]}"),
                        self._gdl_upd_status.setStyleSheet(
                            f"color: {T.error}; font-size: 11px; background: transparent;"
                        ),
                        self._app.toast(f"Update failed: {m[:60]}", "error"),
                    )
                )
            finally:
                ui_bridge.post(lambda: self._gdl_upd_btn.setEnabled(True))

        threading.Thread(target=_worker, daemon=True, name="omnidl-gdl-update").start()

    # ── Data & Privacy handler ────────────────────────────────────────────

    def _clear_all_data(self) -> None:
        active_tasks = [
            t
            for t in self._app.service.get_all_tasks()
            if t.status.name in ("QUEUED", "DOWNLOADING", "PROCESSING")
        ]
        convert_tab = self._app.get_tab("convert")
        active_cv = getattr(convert_tab, "_active_count", 0)
        if active_tasks or active_cv:
            parts = []
            if active_tasks:
                parts.append(f"{len(active_tasks)} download đang chạy")
            if active_cv:
                parts.append(f"{active_cv} conversion đang chạy")
            self._app.toast("Không thể xóa dữ liệu khi " + " và ".join(parts) + ".", "error")
            return

        reply = QMessageBox.question(
            self,
            "OmniDL — Xác nhận xóa dữ liệu",
            "Thao tác này sẽ:\n"
            "  • Xóa toàn bộ lịch sử tải\n"
            "  • Đặt lại tất cả thiết lập về mặc định\n"
            "  • Xóa đường dẫn cookie file khỏi cấu hình\n\n"
            "File cookie và file đã tải sẽ không bị ảnh hưởng.\n\nTiếp tục?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            self._app.service.clear_history()
            self._app.config.reset_to_defaults()
            self._app.navigate_to("home")
            self._clear_data_status.setText("✓ Đã xóa")
            self._clear_data_status.setStyleSheet(
                f"color: {T.success}; font-size: 11px; background: transparent;"
            )
            QTimer.singleShot(
                4000,
                lambda: (
                    self._clear_data_status.setText(""),
                    self._clear_data_status.setStyleSheet(
                        f"color: {T.text2}; font-size: 11px; background: transparent;"
                    ),
                ),
            )
            self._app.toast("Đã xóa toàn bộ dữ liệu. Khởi động lại app để áp dụng đầy đủ.", "success")
            logger.info("User cleared all app data (history + config reset)")
        except Exception as exc:
            msg = str(exc)
            logger.error("Clear all data failed: %s", msg)
            self._clear_data_status.setText(f"Lỗi: {msg[:60]}")
            self._clear_data_status.setStyleSheet(
                f"color: {T.error}; font-size: 11px; background: transparent;"
            )
            self._app.toast(f"Xóa dữ liệu thất bại: {msg[:60]}", "error")
