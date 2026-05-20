"""Batch Download tab."""

from __future__ import annotations

import logging
import threading
import time as _time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING, Optional
from urllib.parse import urlparse

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from domain.models.download_task import MediaInfo
from ui.signals import ui_bridge
from ui.themes.tokens import T
from utils.helpers import is_valid_url

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = logging.getLogger(__name__)

MAX_BATCH_URLS: int = 50
_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


class _ItemState(Enum):
    PENDING = auto()
    ANALYSING = auto()
    READY = auto()
    ERROR = auto()
    QUEUED = auto()


@dataclass
class _BatchItem:
    url: str
    state: _ItemState = _ItemState.PENDING
    media_info: Optional[MediaInfo] = None
    error_msg: str = ""
    checked: bool = True
    row_frame: Optional[QFrame] = field(default=None, repr=False)
    state_lbl: Optional[QLabel] = field(default=None, repr=False)
    title_lbl: Optional[QLabel] = field(default=None, repr=False)
    platform_lbl: Optional[QLabel] = field(default=None, repr=False)
    check_box: Optional[QCheckBox] = field(default=None, repr=False)
    remove_btn: Optional[QPushButton] = field(default=None, repr=False)


class BatchTab(QWidget):
    _PLATFORM_ANALYSIS_DELAY: dict[str, float] = {
        "instagram.com": 2.5,
        "tiktok.com": 2.0,
        "facebook.com": 1.5,
        "fb.watch": 1.5,
        "twitter.com": 1.5,
        "x.com": 1.5,
        "threads.net": 2.0,
        "youtube.com": 0.8,
        "youtu.be": 0.8,
    }

    def __init__(self, app: "MainWindow") -> None:
        super().__init__()
        self._app = app
        self._items: list[_BatchItem] = []
        self._batch_token: int = 0
        self._analysing_count: int = 0
        self._spinner_idx: int = 0
        self._quality_map = {
            "Best": "bestvideo+bestaudio/best",
            "1080p": "bestvideo[height<=1080]+bestaudio/best",
            "720p": "bestvideo[height<=720]+bestaudio/best",
            "480p": "bestvideo[height<=480]+bestaudio/best",
        }
        self._build()

        from PySide6.QtCore import QPropertyAnimation
        from PySide6.QtWidgets import QGraphicsOpacityEffect

        self._fade_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._fade_effect)
        self._fade_anim = QPropertyAnimation(self._fade_effect, b"opacity", self)
        self._fade_anim.setDuration(150)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        hdr = QWidget()
        hdr.setStyleSheet("background: transparent;")
        hdr_layout = QHBoxLayout(hdr)
        hdr_layout.setContentsMargins(28, 24, 28, 0)
        title = QLabel("Batch Download")
        title.setObjectName("page_title")
        hdr_layout.addWidget(title)
        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(f"color: {T.text3}; font-size: 12px;")
        hdr_layout.addWidget(self._status_lbl)
        hdr_layout.addStretch()
        layout.addWidget(hdr)

        # Input card
        input_wrap = QWidget()
        input_wrap.setStyleSheet("background: transparent;")
        input_wrap_layout = QVBoxLayout(input_wrap)
        input_wrap_layout.setContentsMargins(28, 14, 28, 0)
        input_wrap_layout.setSpacing(0)

        input_card = QFrame()
        input_card.setStyleSheet(f"""
            QFrame {{
                background-color: {T.surface};
                border: 1px solid {T.border};
                border-radius: 12px;
            }}
            QLabel {{ border: none; }}
        """)
        input_card_layout = QVBoxLayout(input_card)
        input_card_layout.setContentsMargins(16, 12, 16, 12)
        input_card_layout.setSpacing(6)

        hint = QLabel("Dán URL vào đây — mỗi dòng một link (tối đa 50)")
        hint.setStyleSheet(f"color: {T.text3}; font-size: 11px; background: transparent;")
        input_card_layout.addWidget(hint)

        self._text_area = QTextEdit()
        self._text_area.setFixedHeight(130)
        self._text_area.setStyleSheet(f"""
            QTextEdit {{
                background-color: {T.input};
                border: 1px solid {T.border2};
                border-radius: 8px;
                color: {T.text};
                font-size: 12px;
                padding: 4px;
            }}
        """)
        self._text_area.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self._text_area.textChanged.connect(self._on_text_change)
        input_card_layout.addWidget(self._text_area)

        btn_row = QWidget()
        btn_row.setStyleSheet("background: transparent;")
        btn_row_layout = QHBoxLayout(btn_row)
        btn_row_layout.setContentsMargins(0, 0, 0, 0)
        btn_row_layout.setSpacing(8)

        import_btn = QPushButton("Import .txt")
        import_btn.setFixedHeight(34)
        import_btn.setStyleSheet(
            f"background: {T.surface2}; color: {T.text2}; border: none; border-radius: 8px; font-size: 12px; padding: 0 12px;"
        )
        import_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        import_btn.clicked.connect(self._import_file)
        btn_row_layout.addWidget(import_btn)

        clear_btn = QPushButton("Xóa tất cả")
        clear_btn.setFixedHeight(34)
        clear_btn.setStyleSheet(
            f"background: {T.surface2}; color: {T.text3}; border: none; border-radius: 8px; font-size: 12px; padding: 0 12px;"
        )
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.clicked.connect(self._clear_all)
        btn_row_layout.addWidget(clear_btn)

        self._url_count_lbl = QLabel("")
        self._url_count_lbl.setStyleSheet(f"color: {T.text3}; font-size: 11px; background: transparent;")
        btn_row_layout.addWidget(self._url_count_lbl)
        btn_row_layout.addStretch()

        self._analyse_btn = QPushButton("Phân tích")
        self._analyse_btn.setFixedHeight(34)
        self._analyse_btn.setEnabled(False)
        self._analyse_btn.setStyleSheet(
            f"background: {T.primary}; color: white; border: none; border-radius: 8px; font-size: 13px; font-weight: bold; padding: 0 16px;"
        )
        self._analyse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._analyse_btn.clicked.connect(self._start_batch_analyse)
        btn_row_layout.addWidget(self._analyse_btn)

        input_card_layout.addWidget(btn_row)
        input_wrap_layout.addWidget(input_card)
        layout.addWidget(input_wrap)

        # Results header
        results_hdr = QWidget()
        results_hdr.setStyleSheet("background: transparent;")
        rh_layout = QHBoxLayout(results_hdr)
        rh_layout.setContentsMargins(28, 14, 28, 4)
        rh_layout.setSpacing(8)

        rl = QLabel("Kết quả phân tích")
        rl.setStyleSheet(f"color: {T.text2}; font-size: 13px; font-weight: bold;")
        rh_layout.addWidget(rl)
        rh_layout.addStretch()

        ql = QLabel("Quality:")
        ql.setStyleSheet(f"color: {T.text3}; font-size: 11px;")
        rh_layout.addWidget(ql)
        self._quality_combo = QComboBox()
        self._quality_combo.addItems(list(self._quality_map.keys()))
        self._quality_combo.setFixedSize(90, 30)
        rh_layout.addWidget(self._quality_combo)

        fl = QLabel("Format:")
        fl.setStyleSheet(f"color: {T.text3}; font-size: 11px;")
        rh_layout.addWidget(fl)
        self._format_combo = QComboBox()
        self._format_combo.addItems(["mp4", "mkv", "webm", "mp3", "m4a"])
        self._format_combo.setFixedSize(80, 30)
        self._format_combo.setCurrentText(self._app.config.default_format)
        rh_layout.addWidget(self._format_combo)

        self._retry_btn = QPushButton("Retry errors")
        self._retry_btn.setFixedHeight(36)
        self._retry_btn.setEnabled(False)
        self._retry_btn.setStyleSheet(
            f"background: {T.surface2}; color: {T.text2}; border: none; border-radius: 8px; font-size: 12px; padding: 0 12px;"
        )
        self._retry_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._retry_btn.clicked.connect(self._retry_errors)
        rh_layout.addWidget(self._retry_btn)

        self._queue_all_btn = QPushButton("Queue All")
        self._queue_all_btn.setFixedHeight(36)
        self._queue_all_btn.setEnabled(False)
        self._queue_all_btn.setStyleSheet(
            f"background: {T.success_bg}; color: {T.success_text}; border: none; border-radius: 8px; font-size: 13px; font-weight: bold; padding: 0 16px;"
        )
        self._queue_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._queue_all_btn.clicked.connect(self._queue_all)
        rh_layout.addWidget(self._queue_all_btn)

        layout.addWidget(results_hdr)

        # Scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._scroll_content = QWidget()
        self._scroll_content.setStyleSheet("background: transparent;")
        self._items_layout = QVBoxLayout(self._scroll_content)
        self._items_layout.setContentsMargins(28, 4, 28, 20)
        self._items_layout.setSpacing(4)
        self._items_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._add_empty_label()

        scroll.setWidget(self._scroll_content)
        layout.addWidget(scroll, 1)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _add_empty_label(self) -> None:
        empty = QLabel("Chưa có URL nào — dán link ở trên hoặc import file .txt")
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty.setStyleSheet(f"color: {T.text3}; font-size: 13px; background: transparent;")
        empty.setMinimumHeight(100)
        self._items_layout.addWidget(empty)

    @staticmethod
    def _short_url(url: str, max_len: int = 70) -> str:
        if len(url) <= max_len:
            return url
        return url[: max_len - 1] + "…"

    @staticmethod
    def _get_analysis_delay(url: str) -> float:
        try:
            host = urlparse(url).hostname or ""
        except Exception:
            host = ""
        for domain, delay in BatchTab._PLATFORM_ANALYSIS_DELAY.items():
            if host == domain or host.endswith("." + domain):
                return delay
        return 0.3

    # ── Text input ────────────────────────────────────────────────────────────

    def _on_text_change(self) -> None:
        urls = self._parse_textarea()
        count = len(urls)
        if count == 0:
            self._url_count_lbl.setText("")
            self._analyse_btn.setEnabled(False)
        elif count > MAX_BATCH_URLS:
            self._url_count_lbl.setText(f"{count} URL — chỉ lấy {MAX_BATCH_URLS} đầu tiên")
            self._analyse_btn.setEnabled(True)
        else:
            self._url_count_lbl.setText(f"{count} URL")
            self._analyse_btn.setEnabled(True)

    def _parse_textarea(self) -> list[str]:
        raw = self._text_area.toPlainText()
        seen: set[str] = set()
        result: list[str] = []
        for line in raw.splitlines():
            url = line.strip()
            if url and is_valid_url(url) and url not in seen:
                seen.add(url)
                result.append(url)
        return result[:MAX_BATCH_URLS]

    # ── Import ────────────────────────────────────────────────────────────────

    def _import_file(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "Nhập danh sách URL",
            "",
            "Text files (*.txt);;All files (*.*)",
        )
        if not path_str:
            return
        try:
            content = Path(path_str).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            self._app.toast(f"Không đọc được file: {exc}", "error")
            return

        urls: list[str] = []
        invalid = 0
        for line in content.splitlines():
            url = line.strip()
            if not url or url.startswith("#"):
                continue
            if is_valid_url(url):
                urls.append(url)
            else:
                invalid += 1

        if not urls:
            self._app.toast("Không tìm thấy URL hợp lệ trong file.", "error")
            return

        total = len(urls)
        capped = total > MAX_BATCH_URLS
        urls = urls[:MAX_BATCH_URLS]

        existing = self._text_area.toPlainText().strip()
        separator = "\n" if existing else ""
        self._text_area.setPlainText(existing + separator + "\n".join(urls))

        msg_parts = [f"Đã nhập {len(urls)} URL"]
        if capped:
            msg_parts.append(f"(giới hạn {MAX_BATCH_URLS}, bỏ qua {total - MAX_BATCH_URLS})")
        if invalid:
            msg_parts.append(f"· {invalid} dòng không hợp lệ bỏ qua")
        self._app.toast(" ".join(msg_parts), "success" if not capped else "info")
        self._on_text_change()

    # ── Batch analysis ────────────────────────────────────────────────────────

    def _start_batch_analyse(self) -> None:
        urls = self._parse_textarea()
        if not urls:
            return

        self._batch_token += 1
        my_token = self._batch_token

        self._items = [_BatchItem(url=u) for u in urls]
        self._analysing_count = 0

        self._analyse_btn.setEnabled(False)
        self._analyse_btn.setText("Đang phân tích…")
        self._queue_all_btn.setEnabled(False)
        self._status_lbl.setText("")

        self._rebuild_results_ui()
        self._analyse_next(my_token)
        self._tick_spinner(my_token)

    def _analyse_next(self, token: int) -> None:
        if token != self._batch_token:
            return

        pending = next(
            (item for item in self._items if item.state == _ItemState.PENDING),
            None,
        )
        if pending is None:
            self._on_batch_complete(token)
            return

        pending.state = _ItemState.ANALYSING
        self._refresh_item_ui(pending)
        self._analysing_count += 1

        url = pending.url

        def on_done(info: MediaInfo) -> None:
            if token != self._batch_token:
                return
            ui_bridge.post(lambda i=pending, m=info: self._on_item_done(i, m, token))

        def on_error(err: str) -> None:
            if token != self._batch_token:
                return
            ui_bridge.post(lambda i=pending, e=err: self._on_item_error(i, e, token))

        def _delayed_analyse() -> None:
            delay = self._get_analysis_delay(url)
            is_first = all(
                i.state in (_ItemState.PENDING, _ItemState.ANALYSING) for i in self._items if i is not pending
            )
            if not is_first and delay > 0:
                _time.sleep(delay)
            if token != self._batch_token:
                return
            self._app.service.analyse_url(url=url, on_done=on_done, on_error=on_error)

        threading.Thread(target=_delayed_analyse, daemon=True, name=f"omnidl-batch-{url[:30]}").start()

    def _on_item_done(self, item: _BatchItem, info: MediaInfo, token: int) -> None:
        if token != self._batch_token:
            return
        self._analysing_count -= 1
        item.state = _ItemState.READY
        item.media_info = info
        item.checked = True
        self._refresh_item_ui(item)
        self._analyse_next(token)

    def _on_item_error(self, item: _BatchItem, err: str, token: int) -> None:
        if token != self._batch_token:
            return
        self._analysing_count -= 1
        item.state = _ItemState.ERROR
        item.error_msg = err[:120]
        item.checked = False
        self._refresh_item_ui(item)
        self._analyse_next(token)

    def _on_batch_complete(self, token: int) -> None:
        if token != self._batch_token:
            return
        ready = sum(1 for i in self._items if i.state == _ItemState.READY and i.checked)
        total = len(self._items)
        errors = sum(1 for i in self._items if i.state == _ItemState.ERROR)

        self._analyse_btn.setEnabled(True)
        self._analyse_btn.setText("Phân tích lại")

        if errors > 0:
            self._retry_btn.setEnabled(True)
            self._retry_btn.setText(f"Retry {errors} lỗi")
        else:
            self._retry_btn.setEnabled(False)
            self._retry_btn.setText("Retry errors")

        if ready == 0:
            self._status_lbl.setText(f"Không có URL nào hợp lệ ({errors} lỗi)")
            self._status_lbl.setStyleSheet(f"color: {T.error_text}; font-size: 12px;")
            self._queue_all_btn.setEnabled(False)
        else:
            err_note = f"  ·  {errors} lỗi" if errors else ""
            self._status_lbl.setText(f"✓  {ready}/{total} sẵn sàng{err_note}")
            self._status_lbl.setStyleSheet(f"color: {T.success_text}; font-size: 12px;")
            self._queue_all_btn.setEnabled(True)
            self._queue_all_btn.setText(f"Queue {ready} video")

    # ── Spinner ───────────────────────────────────────────────────────────────

    def _tick_spinner(self, token: int) -> None:
        if token != self._batch_token:
            return
        if self._analysing_count == 0:
            return
        self._spinner_idx = (self._spinner_idx + 1) % len(_SPINNER_FRAMES)
        frame = _SPINNER_FRAMES[self._spinner_idx]
        for item in self._items:
            if item.state == _ItemState.ANALYSING and item.state_lbl:
                item.state_lbl.setText(frame)
                item.state_lbl.setStyleSheet(
                    f"color: {T.primary_text}; font-size: 14px; background: transparent;"
                )
        QTimer.singleShot(100, lambda: self._tick_spinner(token))

    # ── Result UI ─────────────────────────────────────────────────────────────

    def _rebuild_results_ui(self) -> None:
        while self._items_layout.count():
            item_w = self._items_layout.takeAt(0)
            if item_w.widget():
                item_w.widget().deleteLater()

        if not self._items:
            self._add_empty_label()
            return

        for item in self._items:
            self._build_item_row(item)

    def _build_item_row(self, item: _BatchItem) -> None:
        row = QFrame()
        row.setStyleSheet(f"QFrame {{ background-color: {T.surface2}; border-radius: 8px; }}")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(10, 6, 6, 6)
        row_layout.setSpacing(6)
        item.row_frame = row

        check_box = QCheckBox()
        check_box.setChecked(item.checked)
        check_box.stateChanged.connect(lambda state, i=item: self._on_check_change(i, bool(state)))
        row_layout.addWidget(check_box)
        item.check_box = check_box

        state_lbl = QLabel("…")
        state_lbl.setFixedWidth(22)
        state_lbl.setStyleSheet(f"color: {T.text3}; font-size: 14px; background: transparent;")
        row_layout.addWidget(state_lbl)
        item.state_lbl = state_lbl

        title_lbl = QLabel(self._short_url(item.url))
        title_lbl.setStyleSheet(f"color: {T.text2}; font-size: 12px; background: transparent;")
        title_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        row_layout.addWidget(title_lbl)
        item.title_lbl = title_lbl

        platform_lbl = QLabel("")
        platform_lbl.setFixedWidth(80)
        platform_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        platform_lbl.setStyleSheet(f"color: {T.text3}; font-size: 10px; background: transparent;")
        row_layout.addWidget(platform_lbl)
        item.platform_lbl = platform_lbl

        remove_btn = QPushButton("x")
        remove_btn.setFixedSize(28, 28)
        remove_btn.setStyleSheet(
            f"background: transparent; color: {T.text3}; border: none; border-radius: 6px;"
        )
        remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        remove_btn.clicked.connect(lambda _=False, i=item: self._remove_item(i))
        row_layout.addWidget(remove_btn)
        item.remove_btn = remove_btn

        self._items_layout.addWidget(row)
        self._refresh_item_ui(item)

    def _refresh_item_ui(self, item: _BatchItem) -> None:
        if not item.row_frame:
            return

        state = item.state

        if state == _ItemState.PENDING:
            item.row_frame.setStyleSheet(f"QFrame {{ background-color: {T.surface2}; border-radius: 8px; }}")
            item.state_lbl.setText("…")
            item.state_lbl.setStyleSheet(f"color: {T.text3}; font-size: 14px; background: transparent;")
            item.title_lbl.setText(self._short_url(item.url))
            item.title_lbl.setStyleSheet(f"color: {T.text3}; font-size: 12px; background: transparent;")

        elif state == _ItemState.ANALYSING:
            item.row_frame.setStyleSheet(f"QFrame {{ background-color: {T.surface2}; border-radius: 8px; }}")
            item.title_lbl.setText(self._short_url(item.url))
            item.title_lbl.setStyleSheet(f"color: {T.text2}; font-size: 12px; background: transparent;")

        elif state == _ItemState.READY:
            item.row_frame.setStyleSheet(
                f"QFrame {{ background-color: {T.success_bg}; border-radius: 8px; }}"
            )
            item.state_lbl.setText("✓")
            item.state_lbl.setStyleSheet(f"color: {T.success}; font-size: 14px; background: transparent;")
            title = item.media_info.title[:70] if item.media_info and item.media_info.title else item.url[:70]
            item.title_lbl.setText(title)
            item.title_lbl.setStyleSheet(f"color: {T.text}; font-size: 12px; background: transparent;")
            if item.media_info:
                item.platform_lbl.setText(item.media_info.platform)
            if item.check_box:
                item.check_box.setEnabled(True)

        elif state == _ItemState.ERROR:
            item.row_frame.setStyleSheet(f"QFrame {{ background-color: {T.error_bg}; border-radius: 8px; }}")
            item.state_lbl.setText("✗")
            item.state_lbl.setStyleSheet(f"color: {T.error}; font-size: 14px; background: transparent;")
            short_url = self._short_url(item.url, 65)
            err_preview = item.error_msg[:80] if item.error_msg else "Analysis failed"
            item.title_lbl.setText(f"{short_url}  ·  {err_preview}")
            item.title_lbl.setStyleSheet(f"color: {T.error_text}; font-size: 12px; background: transparent;")
            if item.check_box:
                item.check_box.setEnabled(False)
                item.check_box.setChecked(False)

        elif state == _ItemState.QUEUED:
            item.row_frame.setStyleSheet(
                f"QFrame {{ background-color: {T.primary_dim}; border-radius: 8px; }}"
            )
            item.state_lbl.setText("⬇")
            item.state_lbl.setStyleSheet(
                f"color: {T.primary_text}; font-size: 14px; background: transparent;"
            )
            title = item.media_info.title[:70] if item.media_info and item.media_info.title else item.url[:70]
            item.title_lbl.setText(title)
            item.title_lbl.setStyleSheet(
                f"color: {T.primary_text}; font-size: 12px; background: transparent;"
            )

    def _on_check_change(self, item: _BatchItem, checked: bool) -> None:
        item.checked = checked
        self._update_queue_btn_count()

    def _update_queue_btn_count(self) -> None:
        ready = sum(1 for i in self._items if i.state == _ItemState.READY and i.checked)
        if ready == 0:
            self._queue_all_btn.setEnabled(False)
            self._queue_all_btn.setText("Queue All")
        else:
            self._queue_all_btn.setEnabled(True)
            self._queue_all_btn.setText(f"Queue {ready} video")

    def _remove_item(self, item: _BatchItem) -> None:
        if item.row_frame:
            self._items_layout.removeWidget(item.row_frame)
            item.row_frame.deleteLater()
            item.row_frame = None
        if item in self._items:
            self._items.remove(item)
        if not self._items:
            self._add_empty_label()
            self._queue_all_btn.setEnabled(False)
            self._queue_all_btn.setText("Queue All")
            self._status_lbl.setText("")
        self._update_queue_btn_count()

    # ── Queue All ─────────────────────────────────────────────────────────────

    def _queue_all(self) -> None:
        quality_label = self._quality_combo.currentText()
        format_id = self._quality_map.get(quality_label, "bestvideo+bestaudio/best")
        output_ext = self._format_combo.currentText()

        queued = 0
        for item in self._items:
            if item.state != _ItemState.READY or not item.checked:
                continue
            if item.media_info is None:
                continue
            try:
                self._app.service.start_download(
                    url=item.url,
                    media_info=item.media_info,
                    format_id=format_id,
                    output_ext=output_ext,
                )
                item.state = _ItemState.QUEUED
                self._refresh_item_ui(item)
                queued += 1
            except Exception as exc:
                logger.warning("Batch queue failed for %s: %s", item.url, exc)
                item.state = _ItemState.ERROR
                item.error_msg = str(exc)[:80]
                item.checked = False
                self._refresh_item_ui(item)

        if queued:
            self._app.toast(f"Đã thêm {queued} video vào queue.", "success")
            self._queue_all_btn.setEnabled(False)
            self._queue_all_btn.setText("✓  Đã thêm vào queue")
            self._status_lbl.setText(f"✓  {queued} video da duoc them vao queue")
            self._status_lbl.setStyleSheet(f"color: {T.success_text}; font-size: 12px;")
            self._app.navigate_to("queue")

    # ── Retry errors ──────────────────────────────────────────────────────────

    def _retry_errors(self) -> None:
        error_items = [i for i in self._items if i.state == _ItemState.ERROR]
        if not error_items:
            return

        self._batch_token += 1
        my_token = self._batch_token

        for item in error_items:
            item.state = _ItemState.PENDING
            item.error_msg = ""
            item.checked = True
            self._refresh_item_ui(item)

        self._analysing_count = 0
        self._retry_btn.setEnabled(False)
        self._retry_btn.setText("Retry errors")
        self._analyse_btn.setEnabled(False)
        self._analyse_btn.setText("Đang thử lại…")
        self._queue_all_btn.setEnabled(False)
        self._status_lbl.setText(f"Đang thử lại {len(error_items)} URL lỗi…")
        self._status_lbl.setStyleSheet(f"color: {T.text3}; font-size: 12px;")

        self._analyse_next(my_token)
        self._tick_spinner(my_token)

    # ── Clear all ─────────────────────────────────────────────────────────────

    def _clear_all(self) -> None:
        self._batch_token += 1
        self._analysing_count = 0
        self._text_area.clear()

        while self._items_layout.count():
            item_w = self._items_layout.takeAt(0)
            if item_w.widget():
                item_w.widget().deleteLater()
        self._items.clear()

        self._analyse_btn.setEnabled(False)
        self._analyse_btn.setText("Phân tích")
        self._queue_all_btn.setEnabled(False)
        self._queue_all_btn.setText("Queue All")
        self._retry_btn.setEnabled(False)
        self._retry_btn.setText("Retry errors")
        self._url_count_lbl.setText("")
        self._status_lbl.setText("")
        self._add_empty_label()

    # ── Public API (called by HomeTab for playlist redirect) ──────────────────

    def load_playlist(self, urls: list[str], playlist_title: str = "") -> None:
        if not urls:
            return
        capped = urls[:MAX_BATCH_URLS]
        truncated = len(urls) > MAX_BATCH_URLS

        self._clear_all()
        self._text_area.setPlainText("\n".join(capped))
        self._on_text_change()

        label = f'"{playlist_title}"' if playlist_title else "playlist"
        msg = f"Đã tải {len(capped)} video từ {label} vào Batch."
        if truncated:
            msg += f" (giới hạn {MAX_BATCH_URLS}, bỏ qua {len(urls) - MAX_BATCH_URLS})"
        self._app.toast(msg, "info")
        self._start_batch_analyse()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._fade_anim.stop()
        self._fade_anim.start()
