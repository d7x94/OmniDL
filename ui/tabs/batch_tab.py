"""
ui/tabs/batch_tab.py
Batch Download tab — paste multiple URLs or import from .txt file,
analyse each one, then queue all valid items with a single click.

Architecture notes:
  • Zero changes to infrastructure/downloader or app/services layers.
  • Uses only ServiceFacade methods already present: analyse_url(),
    start_download(), get_download_dir(), config.default_quality,
    config.default_format.
  • Thread safety: _batch_token pattern (identical to HomeTab._thumb_token)
    ensures stale analyse callbacks are silently discarded when the user
    resets the batch or navigates away.
  • Sequential analysis: URLs are analysed one at a time to avoid
    hammering platform rate-limiters (Instagram, TikTok are sensitive).
  • Hard cap: MAX_BATCH_URLS = 50 prevents accidental DoS via huge .txt files.
"""
from __future__ import annotations

import logging
import queue
import tkinter as tk
import tkinter.filedialog as fd
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import customtkinter as ctk

from domain.models.download_task import MediaInfo
from ui.themes.tokens import T
from utils.helpers import is_valid_url

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
MAX_BATCH_URLS: int = 50    # hard cap — above this range → tell user to use a playlist
_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


class _ItemState(Enum):
    PENDING   = auto()   # queued for analysis, not started yet
    ANALYSING = auto()   # background thread running
    READY     = auto()   # MediaInfo received, ready to download
    ERROR     = auto()   # analysis failed
    QUEUED    = auto()   # successfully sent to download queue


@dataclass
class _BatchItem:
    """State for one URL in the batch list."""
    url: str
    state: _ItemState = _ItemState.PENDING
    media_info: Optional[MediaInfo] = None
    error_msg: str = ""
    checked: bool = True          # user can uncheck to skip
    # UI widgets — set after the row is built
    row_frame:   Optional[ctk.CTkFrame]  = field(default=None, repr=False)
    state_lbl:   Optional[ctk.CTkLabel]  = field(default=None, repr=False)
    title_lbl:   Optional[ctk.CTkLabel]  = field(default=None, repr=False)
    platform_lbl:Optional[ctk.CTkLabel]  = field(default=None, repr=False)
    check_var:   Optional[tk.BooleanVar] = field(default=None, repr=False)
    check_btn:   Optional[ctk.CTkCheckBox] = field(default=None, repr=False)
    remove_btn:  Optional[ctk.CTkButton] = field(default=None, repr=False)


# ── Main tab widget ────────────────────────────────────────────────────────────

class BatchTab(ctk.CTkFrame):
    """Batch Download tab — Level 1 (textarea) + Level 2 (import .txt)."""

    def __init__(self, master, app: "MainWindow") -> None:
        super().__init__(master, fg_color=T.bg, corner_radius=0)
        self._app = app

        # ── State ──────────────────────────────────────────────────────────
        self._items:           list[_BatchItem] = []
        self._batch_token:     int = 0      # incremented on every new batch
        self._analysing_count: int = 0      # threads currently running
        self._spinner_idx:     int = 0

        # Thread-safe callback queue (Python 3.14: self.after() unsafe from bg thread).
        # analyse_url() callbacks post here; _drain_ui_queue() flushes on UI thread.
        self._ui_queue: queue.Queue = queue.Queue()

        self._build()
        T.register(self._on_theme)
        self._drain_ui_queue()   # start poller

    # ── Thread-safe UI callback pump ─────────────────────────────────────

    def _drain_ui_queue(self) -> None:
        """Drain _ui_queue on the UI thread (Python 3.14 thread-safety).

        analyse_url() callbacks fire on background threads and must not
        call self.after() directly.  They post callables here instead;
        this poller flushes them every 100 ms while the widget exists.
        """
        if not self.winfo_exists():
            return
        try:
            while True:
                fn = self._ui_queue.get_nowait()
                try:
                    fn()
                except Exception as exc:
                    logger.warning("_ui_queue callback raised: %s", exc)
        except queue.Empty:
            pass
        self.after(100, self._drain_ui_queue)

    # ── Build ─────────────────────────────────────────────────────────────

    def _build(self) -> None:
        # ── Title bar ─────────────────────────────────────────────────────
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.pack(fill="x", padx=24, pady=(20, 0))

        ctk.CTkLabel(
            hdr, text="Batch Download",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=T.text,
        ).pack(side="left")

        self._status_lbl = ctk.CTkLabel(
            hdr, text="",
            font=ctk.CTkFont(size=12),
            text_color=T.text3,
        )
        self._status_lbl.pack(side="left", padx=(16, 0))

        # ── Input area ────────────────────────────────────────────────────
        input_card = ctk.CTkFrame(self, fg_color=T.surface, corner_radius=12)
        input_card.pack(fill="x", padx=24, pady=(14, 0))

        ctk.CTkLabel(
            input_card,
            text="Dán URL vào đây — mỗi dòng một link  (tối đa 50)",
            font=ctk.CTkFont(size=11),
            text_color=T.text3,
        ).pack(anchor="w", padx=16, pady=(12, 4))

        self._text_area = ctk.CTkTextbox(
            input_card,
            height=130,
            fg_color=T.input,
            border_color=T.border2,
            border_width=1,
            corner_radius=8,
            font=ctk.CTkFont(size=12),
            text_color=T.text,
            wrap="none",
        )
        self._text_area.pack(fill="x", padx=12, pady=(0, 8))
        self._text_area.bind("<KeyRelease>", self._on_text_change)
        self._text_area.bind("<ButtonRelease-1>", self._on_text_change)

        # ── Button row under textarea ─────────────────────────────────────
        btn_row = ctk.CTkFrame(input_card, fg_color="transparent")
        btn_row.pack(fill="x", padx=12, pady=(0, 12))

        self._import_btn = ctk.CTkButton(
            btn_row, text="📂  Import .txt",
            width=130, height=34, corner_radius=8,
            fg_color=T.surface2, hover_color=T.surface3,
            text_color=T.text2, font=ctk.CTkFont(size=12),
            command=self._import_file,
        )
        self._import_btn.pack(side="left")

        self._clear_input_btn = ctk.CTkButton(
            btn_row, text="🗑  Xoá tất cả",
            width=110, height=34, corner_radius=8,
            fg_color=T.surface2, hover_color=T.error_bg,
            text_color=T.text3, font=ctk.CTkFont(size=12),
            command=self._clear_all,
        )
        self._clear_input_btn.pack(side="left", padx=(8, 0))

        self._analyse_btn = ctk.CTkButton(
            btn_row, text="🔍  Phân tích",
            width=140, height=34, corner_radius=8,
            fg_color=T.primary, hover_color=T.primary_hover,
            text_color="white", font=ctk.CTkFont(size=13, weight="bold"),
            state="disabled",
            command=self._start_batch_analyse,
        )
        self._analyse_btn.pack(side="right")

        self._url_count_lbl = ctk.CTkLabel(
            btn_row, text="",
            font=ctk.CTkFont(size=11),
            text_color=T.text3,
        )
        self._url_count_lbl.pack(side="right", padx=(0, 12))

        # ── Results list ──────────────────────────────────────────────────
        ctk.CTkFrame(self, height=1, fg_color=T.border).pack(
            fill="x", padx=24, pady=(16, 0))

        results_hdr = ctk.CTkFrame(self, fg_color="transparent")
        results_hdr.pack(fill="x", padx=24, pady=(10, 4))

        self._results_lbl = ctk.CTkLabel(
            results_hdr, text="Kết quả phân tích",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=T.text2,
        )
        self._results_lbl.pack(side="left")

        # Queue All button — right side of results header
        self._queue_all_btn = ctk.CTkButton(
            results_hdr,
            text="⬇  Queue All",
            width=160, height=36, corner_radius=8,
            fg_color=T.success_bg, hover_color=T.success,
            text_color=T.success_text,
            font=ctk.CTkFont(size=13, weight="bold"),
            state="disabled",
            command=self._queue_all,
        )
        self._queue_all_btn.pack(side="right")

        # Retry Errors button — shows only when there are failed items
        self._retry_btn = ctk.CTkButton(
            results_hdr,
            text="🔄  Thử lại lỗi",
            width=130, height=36, corner_radius=8,
            fg_color=T.warning_bg if hasattr(T, "warning_bg") else T.surface2,
            hover_color=T.surface3,
            text_color=T.warning_text,
            font=ctk.CTkFont(size=12),
            state="disabled",
            command=self._retry_errors,
        )
        self._retry_btn.pack(side="right", padx=(0, 8))

        # Quality + format selectors — right of results header
        self._format_var  = ctk.StringVar(value=self._app.config.default_format)
        self._quality_var = ctk.StringVar(value=self._app.config.default_quality)

        ctk.CTkOptionMenu(
            results_hdr,
            variable=self._format_var,
            values=["mp4", "mkv", "webm", "mp3", "m4a"],
            width=80, fg_color=T.surface2,
            button_color=T.border2, button_hover_color=T.surface3,
            font=ctk.CTkFont(size=11), corner_radius=7,
        ).pack(side="right", padx=(0, 8))

        ctk.CTkLabel(
            results_hdr, text="Format:",
            font=ctk.CTkFont(size=11), text_color=T.text3,
        ).pack(side="right", padx=(0, 4))

        _quality_presets = [
            ("Best",   "bestvideo+bestaudio/best"),
            ("1080p",  "bestvideo[height<=1080]+bestaudio/best"),
            ("720p",   "bestvideo[height<=720]+bestaudio/best"),
            ("480p",   "bestvideo[height<=480]+bestaudio/best"),
        ]
        self._quality_display_var = ctk.StringVar(value="Best")
        self._quality_map = {label: fmt for label, fmt in _quality_presets}
        self._quality_reverse = {fmt: label for label, fmt in _quality_presets}
        # Set display from current default_quality
        best_label = self._quality_reverse.get(
            self._app.config.default_quality, "Best")
        self._quality_display_var.set(best_label)

        ctk.CTkOptionMenu(
            results_hdr,
            variable=self._quality_display_var,
            values=[label for label, _ in _quality_presets],
            width=90, fg_color=T.surface2,
            button_color=T.border2, button_hover_color=T.surface3,
            font=ctk.CTkFont(size=11), corner_radius=7,
        ).pack(side="right", padx=(0, 8))

        ctk.CTkLabel(
            results_hdr, text="Quality:",
            font=ctk.CTkFont(size=11), text_color=T.text3,
        ).pack(side="right", padx=(0, 4))

        # Scrollable results list
        self._scroll = ctk.CTkScrollableFrame(
            self,
            fg_color=T.surface,
            corner_radius=12,
            scrollbar_button_color=T.scrollbar,
            scrollbar_button_hover_color=T.scrollbar_hover,
        )
        self._scroll.pack(fill="both", expand=True, padx=24, pady=(0, 16))

        self._empty_lbl = ctk.CTkLabel(
            self._scroll,
            text="Chưa có URL nào — dán link ở trên hoặc import file .txt",
            font=ctk.CTkFont(size=13),
            text_color=T.text3,
        )
        self._empty_lbl.pack(expand=True, pady=60)

    # ── Text input handlers ───────────────────────────────────────────────

    def _on_text_change(self, _event=None) -> None:
        """Update URL count label and enable/disable Analyse button."""
        urls = self._parse_textarea()
        count = len(urls)
        if count == 0:
            self._url_count_lbl.configure(text="")
            self._analyse_btn.configure(state="disabled")
        elif count > MAX_BATCH_URLS:
            self._url_count_lbl.configure(
                text=f"⚠ {count} URL — chỉ lấy {MAX_BATCH_URLS} đầu tiên",
                text_color=T.warning,
            )
            self._analyse_btn.configure(state="normal")
        else:
            self._url_count_lbl.configure(
                text=f"{count} URL", text_color=T.text3)
            self._analyse_btn.configure(state="normal")

    def _parse_textarea(self) -> list[str]:
        """Extract valid URLs from the textarea, deduplicated, capped."""
        raw = self._text_area.get("1.0", "end")
        seen: set[str] = set()
        result: list[str] = []
        for line in raw.splitlines():
            url = line.strip()
            if url and is_valid_url(url) and url not in seen:
                seen.add(url)
                result.append(url)
        return result[:MAX_BATCH_URLS]

    # ── Import from .txt file ─────────────────────────────────────────────

    def _import_file(self) -> None:
        """Open file dialog, read URLs from .txt, populate textarea."""
        path_str = fd.askopenfilename(
            title="Import danh sách URL",
            filetypes=[
                ("Text files", "*.txt"),
                ("All files", "*.*"),
            ],
        )
        if not path_str:
            return
        try:
            content = Path(path_str).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            self._app.toast(f"Không đọc được file: {exc}", "error")
            return

        # Extract valid URLs from file
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

        # Insert into textarea (append, not replace, so user can combine)
        existing = self._text_area.get("1.0", "end").strip()
        separator = "\n" if existing else ""
        self._text_area.insert("end", separator + "\n".join(urls))

        msg_parts = [f"Đã import {len(urls)} URL"]
        if capped:
            msg_parts.append(f"(giới hạn {MAX_BATCH_URLS}, bỏ qua {total - MAX_BATCH_URLS})")
        if invalid:
            msg_parts.append(f"· {invalid} dòng không hợp lệ bỏ qua")
        self._app.toast(" ".join(msg_parts), "success" if not capped else "info")
        self._on_text_change()

    # ── Batch analysis ────────────────────────────────────────────────────

    def _start_batch_analyse(self) -> None:
        """Parse textarea → build _items list → start sequential analysis."""
        urls = self._parse_textarea()
        if not urls:
            return

        # Cancel any previous batch via token
        self._batch_token += 1
        my_token = self._batch_token

        # Reset state
        self._items = [_BatchItem(url=u) for u in urls]
        self._analysing_count = 0

        # Lock UI during analysis
        self._analyse_btn.configure(state="disabled", text="⟳  Đang phân tích…")
        self._queue_all_btn.configure(state="disabled")
        self._status_lbl.configure(text="", text_color=T.text3)

        # Rebuild result rows
        self._rebuild_results_ui()

        # Start first item
        self._analyse_next(my_token)
        self._tick_spinner(my_token)

    # Per-platform delay between batch analyses (seconds).
    # Prevents rate-limiting: Instagram/TikTok are most aggressive.
    _PLATFORM_ANALYSIS_DELAY: dict[str, float] = {
        "instagram.com": 2.5,
        "tiktok.com":    2.0,
        "facebook.com":  1.5,
        "fb.watch":      1.5,
        "twitter.com":   1.5,
        "x.com":         1.5,
        "threads.net":   2.0,
        "youtube.com":   0.8,
        "youtu.be":      0.8,
    }

    @staticmethod
    def _get_analysis_delay(url: str) -> float:
        """Return the recommended delay (seconds) before analysing *url*."""
        from urllib.parse import urlparse
        try:
            host = urlparse(url).hostname or ""
        except Exception:
            host = ""
        for domain, delay in BatchTab._PLATFORM_ANALYSIS_DELAY.items():
            if host == domain or host.endswith("." + domain):
                return delay
        return 0.3  # default small delay for unknown platforms

    def _analyse_next(self, token: int) -> None:
        """Find next PENDING item and analyse it (with per-platform delay)."""
        if token != self._batch_token:
            return

        # Find first PENDING item
        pending = next(
            (item for item in self._items if item.state == _ItemState.PENDING),
            None,
        )
        if pending is None:
            # All done
            self._ui_queue.put(lambda: self._on_batch_complete(token))
            return

        pending.state = _ItemState.ANALYSING
        self._ui_queue.put(lambda i=pending: self._refresh_item_ui(i))
        self._analysing_count += 1

        url = pending.url

        def on_done(info: MediaInfo) -> None:
            if token != self._batch_token:
                return
            self._ui_queue.put(lambda i=pending, m=info: self._on_item_done(i, m, token))

        def on_error(err: str) -> None:
            if token != self._batch_token:
                return
            self._ui_queue.put(lambda i=pending, e=err: self._on_item_error(i, e, token))

        # Apply per-platform delay in a daemon thread so UI stays responsive.
        # The delay fires BEFORE analyse_url() — not inside service layer.
        import threading, time as _time

        def _delayed_analyse():
            delay = self._get_analysis_delay(url)
            # Only delay if this is not the very first item in the batch
            is_first = all(
                i.state in (_ItemState.PENDING, _ItemState.ANALYSING)
                for i in self._items
                if i is not pending
            )
            if not is_first and delay > 0:
                _time.sleep(delay)
            if token != self._batch_token:
                return
            self._app.service.analyse_url(url=url, on_done=on_done, on_error=on_error)

        threading.Thread(
            target=_delayed_analyse,
            daemon=True,
            name=f"omnidl-batch-delay-{url[:30]}",
        ).start()

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
        """Called when all URLs have been analysed."""
        if token != self._batch_token:
            return
        ready = sum(1 for i in self._items
                    if i.state == _ItemState.READY and i.checked)
        total = len(self._items)
        errors = sum(1 for i in self._items if i.state == _ItemState.ERROR)

        self._analyse_btn.configure(state="normal", text="🔍  Phân tích lại")

        # Show "Retry errors" button when there are failed items
        if errors > 0:
            self._retry_btn.configure(
                state="normal",
                text=f"🔄  Thử lại {errors} lỗi",
            )
        else:
            self._retry_btn.configure(state="disabled", text="🔄  Thử lại lỗi")

        if ready == 0:
            self._status_lbl.configure(
                text=f"Không có URL nào hợp lệ ({errors} lỗi)",
                text_color=T.error_text,
            )
            self._queue_all_btn.configure(state="disabled")
        else:
            err_note = f"  ·  {errors} lỗi" if errors else ""
            self._status_lbl.configure(
                text=f"✓  {ready}/{total} sẵn sàng{err_note}",
                text_color=T.success_text,
            )
            self._queue_all_btn.configure(
                state="normal",
                text=f"⬇  Queue {ready} video",
            )

    # ── Spinner ───────────────────────────────────────────────────────────

    def _tick_spinner(self, token: int) -> None:
        """Animate spinner on items currently being analysed."""
        if token != self._batch_token:
            return
        if self._analysing_count == 0:
            return
        self._spinner_idx = (self._spinner_idx + 1) % len(_SPINNER_FRAMES)
        frame = _SPINNER_FRAMES[self._spinner_idx]
        for item in self._items:
            if item.state == _ItemState.ANALYSING and item.state_lbl:
                if item.state_lbl.winfo_exists():
                    item.state_lbl.configure(text=frame, text_color=T.primary_text)
        self.after(100, lambda: self._tick_spinner(token))

    # ── Result UI ─────────────────────────────────────────────────────────

    def _rebuild_results_ui(self) -> None:
        """Destroy existing rows and rebuild from _items."""
        # Destroy old widgets
        for item in self._items:
            if item.row_frame and item.row_frame.winfo_exists():
                item.row_frame.destroy()

        if self._empty_lbl.winfo_ismapped():
            self._empty_lbl.pack_forget()

        for item in self._items:
            self._build_item_row(item)

    def _build_item_row(self, item: _BatchItem) -> None:
        """Build one result row for an item."""
        row = ctk.CTkFrame(
            self._scroll,
            fg_color=T.surface2,
            corner_radius=8,
        )
        row.pack(fill="x", padx=4, pady=(0, 4))
        item.row_frame = row

        # Checkbox
        var = tk.BooleanVar(value=item.checked)
        item.check_var = var

        def _on_check(i=item, v=var) -> None:
            i.checked = v.get()
            self._update_queue_btn_count()

        cb = ctk.CTkCheckBox(
            row, text="", variable=var,
            width=24, height=24,
            checkbox_width=18, checkbox_height=18,
            fg_color=T.primary, hover_color=T.primary_hover,
            command=_on_check,
        )
        cb.pack(side="left", padx=(10, 4), pady=8)
        item.check_btn = cb

        # State indicator
        state_lbl = ctk.CTkLabel(
            row, text="…",
            font=ctk.CTkFont(size=14),
            width=22, text_color=T.text3,
        )
        state_lbl.pack(side="left", padx=(0, 8))
        item.state_lbl = state_lbl

        # URL / title label
        title_lbl = ctk.CTkLabel(
            row,
            text=self._short_url(item.url),
            font=ctk.CTkFont(size=12),
            text_color=T.text2,
            anchor="w",
        )
        title_lbl.pack(side="left", fill="x", expand=True)
        item.title_lbl = title_lbl

        # Platform badge
        platform_lbl = ctk.CTkLabel(
            row, text="",
            font=ctk.CTkFont(size=10),
            text_color=T.text3,
            width=80, anchor="e",
        )
        platform_lbl.pack(side="right", padx=(4, 6))
        item.platform_lbl = platform_lbl

        # Remove button
        def _remove(i=item) -> None:
            self._remove_item(i)

        remove_btn = ctk.CTkButton(
            row, text="✕",
            width=28, height=28, corner_radius=6,
            fg_color="transparent", hover_color=T.error_bg,
            text_color=T.text3, font=ctk.CTkFont(size=12),
            command=_remove,
        )
        remove_btn.pack(side="right", padx=(0, 6))
        item.remove_btn = remove_btn

        self._refresh_item_ui(item)

    def _refresh_item_ui(self, item: _BatchItem) -> None:
        """Update colours and text of one row based on current state."""
        if not item.row_frame or not item.row_frame.winfo_exists():
            return

        state = item.state

        if state == _ItemState.PENDING:
            item.state_lbl.configure(text="…", text_color=T.text3)
            item.row_frame.configure(fg_color=T.surface2)
            item.title_lbl.configure(
                text=self._short_url(item.url), text_color=T.text3)

        elif state == _ItemState.ANALYSING:
            # Spinner text set in _tick_spinner
            item.row_frame.configure(fg_color=T.surface2)
            item.title_lbl.configure(
                text=self._short_url(item.url), text_color=T.text2)

        elif state == _ItemState.READY:
            item.state_lbl.configure(text="✓", text_color=T.success)
            item.row_frame.configure(fg_color=T.success_bg)
            title = (item.media_info.title[:70]
                     if item.media_info and item.media_info.title
                     else item.url[:70])
            item.title_lbl.configure(text=title, text_color=T.text)
            if item.media_info:
                item.platform_lbl.configure(
                    text=item.media_info.platform, text_color=T.text3)
            if item.check_btn:
                item.check_btn.configure(state="normal")

        elif state == _ItemState.ERROR:
            item.state_lbl.configure(text="✗", text_color=T.error)
            item.row_frame.configure(fg_color=T.error_bg)
            # Show URL first so user knows which link failed, then error below
            short_url = self._short_url(item.url, 65)
            err_preview = item.error_msg[:80] if item.error_msg else "Analysis failed"
            item.title_lbl.configure(
                text=f"{short_url}  ·  {err_preview}",
                text_color=T.error_text,
            )
            if item.check_btn:
                item.check_btn.configure(state="disabled")
                item.check_var.set(False)

        elif state == _ItemState.QUEUED:
            item.state_lbl.configure(text="⬇", text_color=T.primary_text)
            item.row_frame.configure(fg_color=T.primary_dim)
            title = (item.media_info.title[:70]
                     if item.media_info and item.media_info.title
                     else item.url[:70])
            item.title_lbl.configure(text=title, text_color=T.primary_text)

    def _update_queue_btn_count(self) -> None:
        """Refresh the count shown on Queue All button."""
        ready = sum(1 for i in self._items
                    if i.state == _ItemState.READY and i.checked)
        if ready == 0:
            self._queue_all_btn.configure(
                state="disabled", text="⬇  Queue All")
        else:
            self._queue_all_btn.configure(
                state="normal", text=f"⬇  Queue {ready} video")

    def _remove_item(self, item: _BatchItem) -> None:
        """Remove one item from the batch list."""
        if item.row_frame and item.row_frame.winfo_exists():
            item.row_frame.destroy()
        if item in self._items:
            self._items.remove(item)
        if not self._items:
            self._show_empty()
        self._update_queue_btn_count()

    def _show_empty(self) -> None:
        if not self._empty_lbl.winfo_ismapped():
            self._empty_lbl.pack(expand=True, pady=60)
        self._queue_all_btn.configure(state="disabled", text="⬇  Queue All")
        self._status_lbl.configure(text="")

    # ── Queue All ─────────────────────────────────────────────────────────

    def _queue_all(self) -> None:
        """Send all checked READY items to the download queue."""
        quality_label = self._quality_display_var.get()
        format_id = self._quality_map.get(quality_label, "bestvideo+bestaudio/best")
        output_ext = self._format_var.get()

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
            self._queue_all_btn.configure(state="disabled", text="✓  Đã thêm vào queue")
            self._status_lbl.configure(
                text=f"✓  {queued} video đã được thêm vào queue",
                text_color=T.success_text,
            )
            # Navigate to queue so user can see downloads starting
            self._app.navigate_to("queue")

    # ── Clear all ─────────────────────────────────────────────────────────

    def _retry_errors(self) -> None:
        """Reset ERROR items back to PENDING and re-run analysis on them.

        Keeps READY / QUEUED items intact — only failed URLs are retried.
        Applies the same per-platform delay so retries don't immediately
        hit the rate limiter again.
        """
        error_items = [i for i in self._items if i.state == _ItemState.ERROR]
        if not error_items:
            return

        # Cancel any in-flight batch, start a new token for retry pass
        self._batch_token += 1
        my_token = self._batch_token

        for item in error_items:
            item.state = _ItemState.PENDING
            item.error_msg = ""
            item.checked = True
            self._refresh_item_ui(item)

        self._analysing_count = 0
        self._retry_btn.configure(state="disabled", text="🔄  Thử lại lỗi")
        self._analyse_btn.configure(state="disabled", text="⟳  Đang thử lại…")
        self._queue_all_btn.configure(state="disabled")
        self._status_lbl.configure(
            text=f"Đang thử lại {len(error_items)} URL lỗi…",
            text_color=T.text3,
        )

        self._analyse_next(my_token)
        self._tick_spinner(my_token)

    def _clear_all(self) -> None:
        """Reset everything — textarea + results."""
        # Cancel any in-flight batch
        self._batch_token += 1
        self._analysing_count = 0

        # Clear textarea
        self._text_area.delete("1.0", "end")

        # Destroy result rows
        for item in self._items:
            if item.row_frame and item.row_frame.winfo_exists():
                item.row_frame.destroy()
        self._items.clear()

        # Reset UI
        self._analyse_btn.configure(state="disabled", text="🔍  Phân tích")
        self._queue_all_btn.configure(state="disabled", text="⬇  Queue All")
        self._retry_btn.configure(state="disabled", text="🔄  Thử lại lỗi")
        self._url_count_lbl.configure(text="")
        self._status_lbl.configure(text="")
        self._show_empty()

    # ── Helpers ───────────────────────────────────────────────────────────

    def load_playlist(self, urls: "list[str]", playlist_title: str = "") -> None:
        """Load a list of URLs from a playlist/channel analysis into BatchTab.

        Called by HomeTab when analyse_url() returns a MediaInfo with
        non-empty playlist_entries. Clears any existing batch, populates
        the textarea with the new URLs, and auto-starts analysis.

        Must be called on the UI thread (called via self.after(0, ...)).

        Parameters
        ----------
        urls:            List of direct video URLs to analyse and queue.
        playlist_title:  Display name of the channel/playlist (for the toast).
        """
        if not urls:
            return

        # Respect the hard cap — silently truncate if needed
        capped = urls[:MAX_BATCH_URLS]
        truncated = len(urls) > MAX_BATCH_URLS

        # Clear existing content first
        self._clear_all()

        # Populate textarea (one URL per line)
        self._text_area.insert("1.0", "\n".join(capped))
        self._on_text_change()

        # Show informational toast
        label = f'"{playlist_title}"' if playlist_title else "playlist"
        msg = f"Đã tải {len(capped)} video từ {label} vào Batch."
        if truncated:
            msg += f" (giới hạn {MAX_BATCH_URLS}, bỏ qua {len(urls) - MAX_BATCH_URLS})"
        self._app.toast(msg, "info")

        # Auto-start analysis
        self._start_batch_analyse()

    @staticmethod
    def _short_url(url: str, max_len: int = 70) -> str:
        """Truncate long URL for display."""
        if len(url) <= max_len:
            return url
        return url[:max_len - 1] + "…"

    # ── Theme ─────────────────────────────────────────────────────────────

    def _on_theme(self) -> None:
        """Re-apply theme colours when dark/light mode changes."""
        self.configure(fg_color=T.bg)
        # Refresh all item rows
        for item in self._items:
            if item.row_frame and item.row_frame.winfo_exists():
                self._refresh_item_ui(item)
        if self._empty_lbl.winfo_ismapped():
            self._empty_lbl.configure(text_color=T.text3)
