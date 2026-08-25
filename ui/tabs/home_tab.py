"""Home/Download tab — shows media analysis results and download options."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from domain.models.download_task import MediaInfo
from ui.signals import ui_bridge
from ui.themes.tokens import T
from utils.i18n import t

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = logging.getLogger(__name__)

PLATFORM_COLORS = {
    "YouTube": "#FF0000",
    "TikTok": "#EE1D52",
    "Instagram": "#E1306C",
    "Twitter/X": "#1D9BF0",
    "Facebook": "#1877F2",
    "Twitch": "#9146FF",
    "Threads": "#101010",
    "Vimeo": "#1AB7EA",
    "Dailymotion": "#0066DC",
}

# (i18n label key, yt-dlp format selector, icon) — labels resolve at render time
QUALITY_PRESETS = [
    ("home.quality.best", "bestvideo+bestaudio/best", "🏆"),
    ("home.quality.4k", "bestvideo[height<=2160]+bestaudio/best", "4K"),
    ("home.quality.1080", "bestvideo[height<=1080]+bestaudio/best", "HD"),
    ("home.quality.720", "bestvideo[height<=720]+bestaudio/best", "720"),
    ("home.quality.480", "bestvideo[height<=480]+bestaudio/best", "480"),
    ("home.quality.360", "bestvideo[height<=360]+bestaudio/best", "360"),
    ("home.quality.audio_mp3", "bestaudio/best", "♪"),
    ("home.quality.audio_m4a", "bestaudio[ext=m4a]/bestaudio", "♪"),
]

FORMATS = ["mp4", "mkv", "webm", "mp3", "m4a", "flac"]


class HomeTab(QWidget):
    def __init__(self, app: "MainWindow") -> None:
        super().__init__()
        self._app = app
        self._media_info: Optional[MediaInfo] = None
        self._selected_quality = QUALITY_PRESETS[0][1]
        self._selected_quality_idx = 0
        self._quality_cards: list[QFrame] = []
        self._thumb_token: int = 0
        self._custom_output_dir: Optional[Path] = None
        self._build()

    def _build(self) -> None:
        from PySide6.QtCore import QPropertyAnimation
        from PySide6.QtWidgets import QGraphicsOpacityEffect

        self._fade_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._fade_effect)
        self._fade_anim = QPropertyAnimation(self._fade_effect, b"opacity", self)
        self._fade_anim.setDuration(150)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._scroll_inner = QWidget()
        self._scroll_inner.setStyleSheet("background: transparent;")
        inner_layout = QVBoxLayout(self._scroll_inner)
        inner_layout.setContentsMargins(28, 20, 28, 20)
        inner_layout.setSpacing(0)
        inner_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Welcome state
        self._welcome = self._build_welcome()
        inner_layout.addWidget(self._welcome)

        # Loading state
        self._loading = self._build_loading()
        self._loading.hide()
        inner_layout.addWidget(self._loading)

        # Result card
        self._result_card = self._build_result_card()
        self._result_card.hide()
        inner_layout.addWidget(self._result_card)

        inner_layout.addStretch()
        scroll.setWidget(self._scroll_inner)
        layout.addWidget(scroll)

    def _build_welcome(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(w)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(12)

        icon = QLabel("↓")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet(f"color: {T.primary}; font-size: 56px; font-weight: 300; background: transparent;")
        layout.addWidget(icon)

        self._welcome_title_lbl = QLabel(t("home.welcome_title"))
        title = self._welcome_title_lbl
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"color: {T.text}; font-size: 24px; font-weight: 700; background: transparent;")
        layout.addWidget(title)

        layout.addSpacing(4)

        self._welcome_sub_lbl = QLabel(t("home.welcome_sub"))
        sub = self._welcome_sub_lbl
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet(f"color: {T.text2}; font-size: 14px; background: transparent;")
        layout.addWidget(sub)

        layout.addSpacing(16)

        chips_row = QHBoxLayout()
        chips_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        chips_row.setSpacing(6)
        platforms = [
            ("YouTube", "#FF0000"),
            ("TikTok", "#EE1D52"),
            ("Instagram", "#E1306C"),
            ("Twitter/X", "#1D9BF0"),
            ("Facebook", "#1877F2"),
            ("Twitch", "#9146FF"),
            (t("home.more_platforms"), None),
        ]
        self._platform_chips: list[QLabel] = []
        for label, color in platforms:
            chip = QLabel(label)
            self._platform_chips.append(chip)
            chip.setStyleSheet(f"""
                color: {color or T.text3};
                background-color: {T.surface2};
                border-radius: 6px;
                font-size: 11px;
                font-weight: 600;
                padding: 5px 10px;
            """)
            chips_row.addWidget(chip)
        chips_widget = QWidget()
        chips_widget.setStyleSheet("background: transparent;")
        chips_widget.setLayout(chips_row)
        layout.addWidget(chips_widget)

        return w

    def _build_loading(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(w)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._loading_lbl = QLabel(f"⠋  {t('home.loading')}")
        self._loading_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_lbl.setStyleSheet(f"color: {T.text2}; font-size: 14px; background: transparent;")
        layout.addWidget(self._loading_lbl)

        self._loading_sub_lbl = QLabel(t("home.loading_sub"))
        sub = self._loading_sub_lbl
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet(f"color: {T.text3}; font-size: 12px; background: transparent;")
        layout.addWidget(sub)

        return w

    def _build_result_card(self) -> QWidget:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(14)

        # Media info row: thumbnail + meta
        info_row = QHBoxLayout()
        info_row.setSpacing(18)

        self._thumb_lbl = QLabel()
        self._thumb_lbl.setFixedSize(180, 102)
        self._thumb_lbl.setStyleSheet(f"background-color: {T.surface2}; border-radius: 8px;")
        self._thumb_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb_lbl.setText("...")
        info_row.addWidget(self._thumb_lbl)

        meta = QVBoxLayout()
        meta.setSpacing(6)

        self._platform_badge = QLabel("")
        self._platform_badge.setStyleSheet(
            f"background-color: {T.primary}; color: white; border-radius: 4px; font-size: 9px; font-weight: bold; padding: 3px 10px;"
        )
        self._platform_badge.setMaximumHeight(22)
        meta.addWidget(self._platform_badge)

        self._title_lbl = QLabel("")
        self._title_lbl.setWordWrap(True)
        self._title_lbl.setStyleSheet(
            f"color: {T.text}; font-size: 15px; font-weight: bold; background: transparent;"
        )
        meta.addWidget(self._title_lbl)

        meta_sub = QHBoxLayout()
        self._uploader_lbl = QLabel("")
        self._uploader_lbl.setStyleSheet(f"color: {T.text3}; font-size: 11px; background: transparent;")
        meta_sub.addWidget(self._uploader_lbl)
        self._duration_lbl = QLabel("")
        self._duration_lbl.setStyleSheet(f"color: {T.text3}; font-size: 11px; background: transparent;")
        meta_sub.addWidget(self._duration_lbl)
        self._live_badge = QLabel("  🔴 LIVE  ")
        self._live_badge.setStyleSheet(
            f"background-color: {T.error}; color: white; border-radius: 4px; font-size: 9px; font-weight: bold; padding: 3px 0;"
        )
        self._live_badge.hide()
        meta_sub.addWidget(self._live_badge)
        meta_sub.addStretch()
        meta.addLayout(meta_sub)
        meta.addStretch()

        meta_widget = QWidget()
        meta_widget.setStyleSheet("background: transparent;")
        meta_widget.setLayout(meta)
        info_row.addWidget(meta_widget, 1)
        layout.addLayout(info_row)

        # Divider
        div1 = QFrame()
        div1.setFrameShape(QFrame.Shape.HLine)
        div1.setStyleSheet(f"background-color: {T.border}; max-height: 1px;")
        layout.addWidget(div1)

        # Quality picker
        self._q_sec = QWidget()
        self._q_sec.setStyleSheet("background: transparent;")
        q_layout = QVBoxLayout(self._q_sec)
        q_layout.setContentsMargins(0, 0, 0, 0)
        q_layout.setSpacing(8)

        self._q_section_lbl = QLabel(t("home.quality_section"))
        qlbl = self._q_section_lbl
        qlbl.setStyleSheet(f"color: {T.text3}; font-size: 9px; font-weight: bold; background: transparent;")
        q_layout.addWidget(qlbl)

        # Horizontal scroll for quality cards
        q_scroll = QScrollArea()
        q_scroll.setFixedHeight(96)
        q_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        q_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        q_scroll.setWidgetResizable(True)
        q_scroll.setFrameShape(QFrame.Shape.NoFrame)
        q_scroll.setStyleSheet("background: transparent;")

        q_cards_widget = QWidget()
        q_cards_widget.setStyleSheet("background: transparent;")
        q_cards_row = QHBoxLayout(q_cards_widget)
        q_cards_row.setContentsMargins(0, 0, 0, 0)
        q_cards_row.setSpacing(8)
        q_cards_row.setAlignment(Qt.AlignmentFlag.AlignLeft)

        self._quality_text_labels: list[QLabel] = []
        for idx, (label_key, fmt_id, icon) in enumerate(QUALITY_PRESETS):
            card_w = self._make_quality_card(t(label_key), fmt_id, icon, idx)
            q_cards_row.addWidget(card_w)

        q_cards_row.addStretch()
        q_scroll.setWidget(q_cards_widget)
        q_layout.addWidget(q_scroll)
        layout.addWidget(self._q_sec)

        # Divider
        div2 = QFrame()
        div2.setFrameShape(QFrame.Shape.HLine)
        div2.setStyleSheet(f"background-color: {T.border}; max-height: 1px;")
        layout.addWidget(div2)

        # Options row
        opts = QHBoxLayout()
        opts.setSpacing(8)

        self._format_lbl = QLabel(t("home.format_label"))
        opts.addWidget(self._format_lbl)
        self._format_combo = QComboBox()
        self._format_combo.addItems(FORMATS)
        self._format_combo.setFixedWidth(90)
        opts.addWidget(self._format_combo)

        opts.addSpacing(16)

        self._folder_title_lbl = QLabel(t("home.folder_label"))
        opts.addWidget(self._folder_title_lbl)
        self._folder_lbl = QLabel(self._short_path(self._app.service.get_download_dir()))
        self._folder_lbl.setStyleSheet(f"color: {T.primary_text}; font-size: 11px; background: transparent;")
        opts.addWidget(self._folder_lbl)

        self._browse_btn = QPushButton(t("home.browse"))
        browse_btn = self._browse_btn
        browse_btn.setFixedSize(72, 34)
        browse_btn.clicked.connect(self._browse_folder)
        opts.addWidget(browse_btn)

        opts.addSpacing(16)

        self._download_btn = QPushButton(f"↓  {t('home.add_to_queue')}")
        self._download_btn.setObjectName("primary")
        self._download_btn.setFixedHeight(48)
        self._download_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._download_btn.clicked.connect(self._add_to_queue)
        opts.addWidget(self._download_btn)

        opts.addStretch()
        layout.addLayout(opts)

        # Status
        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(f"color: {T.text3}; font-size: 11px; background: transparent;")
        layout.addWidget(self._status_lbl)

        return card

    def _make_quality_card(self, label: str, fmt_id: str, icon: str, idx: int) -> QFrame:
        card = QFrame()
        card.setObjectName("qualityCard")
        card.setFixedSize(120, 76)
        is_selected = idx == 0
        card.setStyleSheet(f"""
            QFrame#qualityCard {{
                background-color: {"" + T.primary_dim if is_selected else T.surface2};
                border: 1px solid {T.primary if is_selected else T.border};
                border-radius: 12px;
            }}
            QFrame#qualityCard:hover {{
                border-color: {T.primary};
            }}
        """)
        card.setCursor(Qt.CursorShape.PointingHandCursor)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(8, 8, 8, 8)
        card_layout.setSpacing(2)
        card_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon_lbl = QLabel(icon)
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        is_ascii = len(icon) <= 3 and icon.isascii()
        icon_style = "font-size: 14px; font-weight: bold;" if is_ascii else "font-size: 16px;"
        icon_lbl.setStyleSheet(f"color: {T.primary_text}; {icon_style} background: transparent;")
        card_layout.addWidget(icon_lbl)

        text_lbl = QLabel(label)
        text_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text_lbl.setStyleSheet(f"color: {T.text3}; font-size: 9px; background: transparent;")
        text_lbl.setWordWrap(True)
        card_layout.addWidget(text_lbl)
        self._quality_text_labels.append(text_lbl)

        def on_click(_=None, fi=fmt_id, ci=idx) -> None:
            self._selected_quality = fi
            self._selected_quality_idx = ci
            for i, c in enumerate(self._quality_cards):
                if i == ci:
                    c.setStyleSheet(f"""
                        QFrame#qualityCard {{
                            background-color: {T.primary_dim};
                            border: 1px solid {T.primary};
                            border-radius: 12px;
                        }}
                        QFrame#qualityCard:hover {{
                            border-color: {T.primary};
                        }}
                    """)
                else:
                    c.setStyleSheet(f"""
                        QFrame#qualityCard {{
                            background-color: {T.surface2};
                            border: 1px solid {T.border};
                            border-radius: 12px;
                        }}
                        QFrame#qualityCard:hover {{
                            border-color: {T.primary};
                        }}
                    """)

        card.mousePressEvent = on_click
        self._quality_cards.append(card)
        return card

    # ── Public API ────────────────────────────────────────────────────────

    def on_analysis_start(self) -> None:
        self._welcome.hide()
        self._result_card.hide()
        self._loading.show()

    def on_analysis_done(self, info: MediaInfo) -> None:
        self._loading.hide()
        self._welcome.hide()
        try:
            if not info or not info.title:
                self.on_analysis_error(t("home.no_media_info"))
                return

            if info.playlist_entries:
                self._welcome.show()
                self._media_info = None
                batch_tab = self._app.get_tab("batch")
                if batch_tab is not None:
                    self._app.navigate_to("batch")
                    QTimer.singleShot(
                        50,
                        lambda: batch_tab.load_playlist(
                            urls=info.playlist_entries,
                            playlist_title=info.playlist_title or info.uploader or "",
                        ),
                    )
                else:
                    self.on_analysis_error(t("home.batch_unavailable"))
                return

            self._media_info = info
            self._populate_card(info)
            self._result_card.show()
        except Exception as exc:
            logger.exception("on_analysis_done crashed")
            self._set_status(t("home.display_error", err=exc), T.error)

    def on_analysis_error(self, err: str) -> None:
        self._loading.hide()
        self._welcome.show()
        self._set_status(f"  {err[:120]}", T.error)

    def clear_result(self) -> None:
        self._result_card.hide()
        self._loading.hide()
        self._welcome.show()
        self._media_info = None
        self._set_status("", T.text3)

    def refresh(self) -> None:
        self._folder_lbl.setText(self._short_path(self._app.service.get_download_dir()))

    # ── Internal ──────────────────────────────────────────────────────────

    def _populate_card(self, info: MediaInfo) -> None:
        platform_color = PLATFORM_COLORS.get(info.platform, T.primary)

        self._platform_badge.setText(f"  {info.platform}  ")
        self._platform_badge.setStyleSheet(
            f"background-color: {platform_color}; color: white; border-radius: 4px; font-size: 9px; font-weight: bold; padding: 3px 10px;"
        )

        if info.is_live:
            self._live_badge.show()
        else:
            self._live_badge.hide()

        title = info.title[:90] + ("..." if len(info.title) > 90 else "")
        self._title_lbl.setText(title)
        self._uploader_lbl.setText(f"👤  {info.uploader}" if info.uploader else "")

        if info.duration:
            m, s = divmod(info.duration, 60)
            h, m = divmod(m, 60)
            self._duration_lbl.setText(f"⏱  {h}:{m:02d}:{s:02d}" if h else f"⏱  {m:02d}:{s:02d}")
        else:
            self._duration_lbl.setText("")

        is_photo = not info.is_live and not info.formats and info.duration == 0
        if is_photo:
            self._selected_quality = "best"
            self._q_sec.hide()
            self._set_status(f"🖼  {t('home.photo_note')}", T.text2)
        else:
            self._selected_quality = QUALITY_PRESETS[self._selected_quality_idx][1]
            self._q_sec.show()
            if not info.is_live:
                self._set_status("", T.text3)

        # Thumbnail — setPixmap clears any text, so set the placeholder after
        self._thumb_lbl.setPixmap(QPixmap())
        self._thumb_lbl.setText("...")

        if info.thumbnail:
            self._thumb_token += 1
            token = self._thumb_token
            self._app.service.fetch_thumbnail(
                url=info.thumbnail,
                width=180,
                height=102,
                on_done=lambda img, t=token: ui_bridge.post(lambda: self._apply_thumb(img, t)),
                on_error=lambda _, t=token: ui_bridge.post(lambda: self._apply_thumb_error(t)),
            )
        else:
            self._thumb_lbl.setText(t("home.no_preview"))

    def _apply_thumb(self, img, token: int) -> None:
        if token != self._thumb_token:
            return
        try:
            from io import BytesIO

            buf = BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            pixmap = QPixmap()
            pixmap.loadFromData(buf.read())
            scaled = pixmap.scaled(
                180, 102, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
            self._thumb_lbl.setPixmap(scaled)
            self._thumb_lbl.setText("")
        except Exception:
            self._thumb_lbl.setText(t("home.no_preview"))

    def _apply_thumb_error(self, token: int) -> None:
        if token != self._thumb_token:
            return
        self._thumb_lbl.setText(t("home.no_preview"))

    def _add_to_queue(self) -> None:
        if not self._media_info:
            return
        self._app.service.start_download(
            url=self._media_info.url,
            media_info=self._media_info,
            format_id=self._selected_quality,
            output_ext=self._format_combo.currentText(),
            output_dir=self._custom_output_dir,
        )
        self._app.toast(t("home.added_toast", title=self._media_info.title[:40]), "success")
        self._app.navigate_to("queue")
        self._result_card.hide()
        self._welcome.show()
        self._media_info = None
        self._custom_output_dir = None
        self._folder_lbl.setText(self._short_path(self._app.service.get_download_dir()))
        toolbar = self._app.get_toolbar()
        if toolbar:
            toolbar.set_url("")

    def _browse_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self,
            t("home.choose_dir"),
            str(self._app.service.get_download_dir()),
        )
        if chosen:
            self._custom_output_dir = Path(chosen)
            self._folder_lbl.setText(self._short_path(Path(chosen)))

    def _set_status(self, text: str, color: str) -> None:
        self._status_lbl.setText(text)
        self._status_lbl.setStyleSheet(f"color: {color}; font-size: 11px; background: transparent;")

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._fade_anim.stop()
        self._fade_anim.start()

    # ── i18n ─────────────────────────────────────────────────────────────

    def retranslate(self) -> None:
        self._welcome_title_lbl.setText(t("home.welcome_title"))
        self._welcome_sub_lbl.setText(t("home.welcome_sub"))
        if self._platform_chips:
            self._platform_chips[-1].setText(t("home.more_platforms"))
        self._loading_lbl.setText(f"⠋  {t('home.loading')}")
        self._loading_sub_lbl.setText(t("home.loading_sub"))
        self._q_section_lbl.setText(t("home.quality_section"))
        for lbl, (label_key, _fmt, _icon) in zip(self._quality_text_labels, QUALITY_PRESETS, strict=False):
            lbl.setText(t(label_key))
        self._format_lbl.setText(t("home.format_label"))
        self._folder_title_lbl.setText(t("home.folder_label"))
        self._browse_btn.setText(t("home.browse"))
        self._download_btn.setText(f"↓  {t('home.add_to_queue')}")
        # The status line carries transient, already-formatted text; only the
        # thumbnail placeholder is safe to rewrite here.
        if not self._thumb_lbl.pixmap() or self._thumb_lbl.pixmap().isNull():
            if self._media_info is not None and not self._media_info.thumbnail:
                self._thumb_lbl.setText(t("home.no_preview"))

    @staticmethod
    def _short_path(p: Path) -> str:
        s = str(p)
        return s if len(s) <= 40 else "..." + s[-37:]
