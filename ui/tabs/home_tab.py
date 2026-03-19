"""
ui/tabs/home_tab.py
New Download panel — shows media analysis results after Toolbar triggers analysis.
URL input has moved to the persistent Toolbar component.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import customtkinter as ctk

from domain.models.download_task import MediaInfo
from ui.themes.tokens import T

if TYPE_CHECKING:
    from PIL.Image import Image

    from ui.main_window import MainWindow

logger = logging.getLogger(__name__)

PLATFORM_COLORS = {
    "YouTube":     "#FF0000",
    "TikTok":      "#EE1D52",
    "Instagram":   "#E1306C",
    "Twitter/X":   "#1D9BF0",
    "Facebook":    "#1877F2",
    "Twitch":      "#9146FF",
    "Threads":     "#101010",
    "Vimeo":       "#1AB7EA",
    "Dailymotion": "#0066DC",
    "Web":         None,   # falls back to T.primary
}

QUALITY_PRESETS = [
    ("Best Quality",  "bestvideo+bestaudio/best",               "🏆"),
    ("4K / 2160p",    "bestvideo[height<=2160]+bestaudio/best",  "4K"),
    ("1080p Full HD", "bestvideo[height<=1080]+bestaudio/best",  "HD"),
    ("720p HD",       "bestvideo[height<=720]+bestaudio/best",   "720"),
    ("480p",          "bestvideo[height<=480]+bestaudio/best",   "480"),
    ("360p",          "bestvideo[height<=360]+bestaudio/best",   "360"),
    ("Audio Only MP3","bestaudio/best",                          "♪"),
    ("Audio Only M4A","bestaudio[ext=m4a]/bestaudio",            "♪"),
]

FORMATS = ["mp4", "mkv", "webm", "mp3", "m4a", "flac"]


class HomeTab(ctk.CTkFrame):

    def __init__(self, master, app: "MainWindow") -> None:
        super().__init__(master, fg_color=T.bg, corner_radius=0)
        self._app = app
        self._media_info: Optional[MediaInfo] = None
        self._selected_quality = ctk.StringVar(value=QUALITY_PRESETS[0][1])
        self._selected_format  = ctk.StringVar(value="mp4")
        self._quality_cards: list[ctk.CTkFrame] = []
        self._thumb_ref = None
        self._thumb_token: int = 0
        self._custom_output_dir: Optional[Path] = None
        # Thumbnail fetching is now routed via self._app.service.fetch_thumbnail()
        # (ServiceFacade) instead of a local ThumbnailService instance, keeping
        # the architecture rule that tabs never instantiate infrastructure directly.
        self._build()
        T.register(self._on_theme)

    # ── Build ─────────────────────────────────────────────────────────────

    def _build(self) -> None:
        self._scroll = ctk.CTkScrollableFrame(
            self, fg_color="transparent",
            scrollbar_button_color=T.scrollbar,
            scrollbar_button_hover_color=T.scrollbar_hover,
        )
        self._scroll.pack(fill="both", expand=True)

        # ── Empty / welcome state ─────────────────────────────────────────
        self._welcome = self._build_welcome(self._scroll)
        self._welcome.pack(fill="both", expand=True)

        # ── Loading skeleton (shown while analysing) ──────────────────────
        self._loading = self._build_loading(self._scroll)

        # ── Result card (shown after analysis) ───────────────────────────
        self._result_card = ctk.CTkFrame(
            self._scroll, fg_color="transparent",
            corner_radius=0,
        )
        self._build_result_card(self._result_card)

    def _build_welcome(self, parent) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(parent, fg_color="transparent")

        # Centre content vertically
        inner = ctk.CTkFrame(frame, fg_color="transparent")
        inner.place(relx=0.5, rely=0.45, anchor="center")

        ctk.CTkLabel(
            inner, text="⬇",
            font=ctk.CTkFont(size=48),
            text_color=T.primary,
        ).pack(pady=(0, 8))

        ctk.CTkLabel(
            inner, text="Ready to Download",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=T.text,
        ).pack()

        ctk.CTkLabel(
            inner,
            text="Paste a URL in the toolbar above and click Analyze",
            font=ctk.CTkFont(size=13),
            text_color=T.text3,
        ).pack(pady=(6, 20))

        # Supported platforms chips
        chips = ctk.CTkFrame(inner, fg_color="transparent")
        chips.pack()

        platforms = [
            ("▶ YouTube", "#FF0000"),
            ("♪ TikTok",  "#EE1D52"),
            ("📸 Instagram", "#E1306C"),
            ("𝕏 Twitter",   "#1D9BF0"),
            ("f Facebook",  "#1877F2"),
            ("🎮 Twitch",   "#9146FF"),
            ("+ 1000 more", None),
        ]
        for label, color in platforms:
            ctk.CTkLabel(
                chips,
                text=f"  {label}  ",
                font=ctk.CTkFont(size=10, weight="bold"),
                text_color=color or T.text3,
                fg_color=T.surface2,
                corner_radius=20,
                padx=4, pady=4,
            ).pack(side="left", padx=3)

        return frame

    def _build_loading(self, parent) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(parent, fg_color="transparent")

        inner = ctk.CTkFrame(frame, fg_color="transparent")
        inner.place(relx=0.5, rely=0.4, anchor="center")

        self._loading_lbl = ctk.CTkLabel(
            inner, text="⠋  Fetching media info…",
            font=ctk.CTkFont(size=14),
            text_color=T.text2,
        )
        self._loading_lbl.pack()

        ctk.CTkLabel(
            inner,
            text="This may take a few seconds",
            font=ctk.CTkFont(size=11),
            text_color=T.text3,
        ).pack(pady=(4, 0))

        return frame

    def _build_result_card(self, parent) -> None:
        """Build the media info + quality picker + action row."""
        p = parent

        # ── Page header ───────────────────────────────────────────────────
        hdr = ctk.CTkFrame(p, fg_color="transparent")
        hdr.pack(fill="x", padx=28, pady=(20, 0))

        self._page_title = ctk.CTkLabel(
            hdr, text="New Download",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=T.text,
        )
        self._page_title.pack(side="left")

        # ── Media info card ───────────────────────────────────────────────
        media_card = ctk.CTkFrame(
            p, fg_color=T.surface,
            corner_radius=12,
            border_width=1, border_color=T.border,
        )
        media_card.pack(fill="x", padx=28, pady=(14, 0))
        self._media_card_frame = media_card

        top = ctk.CTkFrame(media_card, fg_color="transparent")
        top.pack(fill="x", padx=18, pady=(16, 14))

        # Thumbnail
        self._thumb_lbl = ctk.CTkLabel(
            top, text="",
            width=180, height=102,
            fg_color=T.surface2,
            corner_radius=8,
        )
        self._thumb_lbl.pack(side="left", padx=(0, 18))

        meta = ctk.CTkFrame(top, fg_color="transparent")
        meta.pack(side="left", fill="both", expand=True)

        self._platform_badge = ctk.CTkLabel(
            meta, text="",
            font=ctk.CTkFont(size=9, weight="bold"),
            fg_color=T.primary, corner_radius=4,
            text_color="white", padx=10, pady=3,
        )
        self._platform_badge.pack(anchor="w")

        self._title_lbl = ctk.CTkLabel(
            meta, text="",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=T.text,
            wraplength=480, justify="left", anchor="w",
        )
        self._title_lbl.pack(anchor="w", pady=(8, 4), fill="x")

        info_row = ctk.CTkFrame(meta, fg_color="transparent")
        info_row.pack(anchor="w", fill="x")

        self._uploader_lbl = ctk.CTkLabel(
            info_row, text="",
            font=ctk.CTkFont(size=11), text_color=T.text3,
        )
        self._uploader_lbl.pack(side="left")

        self._duration_lbl = ctk.CTkLabel(
            info_row, text="",
            font=ctk.CTkFont(size=11), text_color=T.text3,
        )
        self._duration_lbl.pack(side="left", padx=(16, 0))

        self._live_badge = ctk.CTkLabel(
            info_row, text="  🔴 LIVE  ",
            font=ctk.CTkFont(size=9, weight="bold"),
            fg_color=T.error, corner_radius=4,
            text_color="white",
        )
        # packed conditionally

        # ── Divider ───────────────────────────────────────────────────────
        ctk.CTkFrame(media_card, height=1, fg_color=T.border).pack(
            fill="x", padx=18)

        # ── Quality picker ────────────────────────────────────────────────
        q_sec = ctk.CTkFrame(media_card, fg_color="transparent")
        q_sec.pack(fill="x", padx=18, pady=(14, 8))
        self._q_sec = q_sec  # stored ref for photo-mode hide/show

        ctk.CTkLabel(
            q_sec, text="QUALITY",
            font=ctk.CTkFont(size=9, weight="bold"),
            text_color=T.text3,
        ).pack(anchor="w", pady=(0, 10))

        q_scroll = ctk.CTkScrollableFrame(
            q_sec, orientation="horizontal", height=82,
            fg_color="transparent",
            scrollbar_button_color=T.scrollbar,
            scrollbar_button_hover_color=T.scrollbar_hover,
        )
        q_scroll.pack(fill="x")

        for label, fmt_id, icon in QUALITY_PRESETS:
            self._make_quality_card(q_scroll, label, fmt_id, icon)
        if self._quality_cards:
            self._quality_cards[0].configure(
                border_color=T.primary, fg_color=T.primary_dim)

        # ── Divider ───────────────────────────────────────────────────────
        ctk.CTkFrame(media_card, height=1, fg_color=T.border).pack(
            fill="x", padx=18)

        # ── Options row ───────────────────────────────────────────────────
        opts = ctk.CTkFrame(media_card, fg_color="transparent")
        opts.pack(fill="x", padx=18, pady=(12, 16))

        # Container format
        ctk.CTkLabel(opts, text="Format",
                     font=ctk.CTkFont(size=11), text_color=T.text3,
                     ).pack(side="left")
        ctk.CTkOptionMenu(
            opts, variable=self._selected_format, values=FORMATS,
            width=90, fg_color=T.surface2,
            button_color=T.border2, button_hover_color=T.surface3,
            font=ctk.CTkFont(size=12), corner_radius=8,
        ).pack(side="left", padx=(8, 24))

        # Output folder
        ctk.CTkLabel(opts, text="Folder",
                     font=ctk.CTkFont(size=11), text_color=T.text3,
                     ).pack(side="left")
        self._folder_lbl = ctk.CTkLabel(
            opts,
            text=self._short_path(self._app.service.get_download_dir()),
            font=ctk.CTkFont(size=11), text_color=T.primary_text,
        )
        self._folder_lbl.pack(side="left", padx=(8, 8))

        ctk.CTkButton(
            opts, text="Browse", width=72, height=34, corner_radius=8,
            fg_color=T.surface2, hover_color=T.surface3,
            text_color=T.text2, font=ctk.CTkFont(size=11),
            command=self._browse_folder,
        ).pack(side="left", padx=(0, 16))

        # Add to Queue button
        self._download_btn = ctk.CTkButton(
            opts, text="⬇  Add to Queue",
            font=ctk.CTkFont(size=14, weight="bold"),
            height=44, corner_radius=10,
            fg_color=T.primary, hover_color=T.primary_hover,
            text_color="white",
            command=self._add_to_queue,
        )
        self._download_btn.pack(side="left")

        # Status below options
        self._status_lbl = ctk.CTkLabel(
            media_card, text="",
            font=ctk.CTkFont(size=11), text_color=T.text3,
        )
        self._status_lbl.pack(padx=18, pady=(0, 4), anchor="w")

    def _make_quality_card(self, parent, label: str, fmt_id: str, icon: str) -> None:
        card = ctk.CTkFrame(
            parent, fg_color=T.surface2,
            corner_radius=10,
            border_width=1, border_color=T.border,
            width=116, height=72,
        )
        card.pack(side="left", padx=(0, 8))
        card.pack_propagate(False)

        is_ascii_badge = len(icon) <= 3 and icon.isascii()
        icon_font = (ctk.CTkFont(size=9, weight="bold")
                     if is_ascii_badge else ctk.CTkFont(size=16))

        ctk.CTkLabel(
            card, text=icon, font=icon_font,
            text_color=T.primary_text,
        ).pack(pady=(10, 0))
        ctk.CTkLabel(
            card, text=label,
            font=ctk.CTkFont(size=9), text_color=T.text3,
        ).pack(pady=(2, 8))

        def on_click() -> None:
            self._selected_quality.set(fmt_id)
            for c in self._quality_cards:
                c.configure(border_color=T.border, fg_color=T.surface2)
            card.configure(border_color=T.primary, fg_color=T.primary_dim)

        card.bind("<Button-1>", lambda _: on_click())
        for child in card.winfo_children():
            child.bind("<Button-1>", lambda _: on_click())

        self._quality_cards.append(card)

    # ── Public API (called by Toolbar) ────────────────────────────────────

    def on_analysis_start(self) -> None:
        """Toolbar is beginning analysis — show loading state."""
        self._welcome.pack_forget()
        self._result_card.pack_forget()
        self._loading.pack(fill="both", expand=True)

    def on_analysis_done(self, info: MediaInfo) -> None:
        """Toolbar finished analysis successfully."""
        self._loading.pack_forget()
        self._welcome.pack_forget()
        try:
            if not info or not info.title:
                self.on_analysis_error("No media information returned.")
                return

            # ── Playlist / channel detection ──────────────────────────────
            # When yt-dlp returns multiple entries (TikTok @username, YouTube
            # channel, playlist URL etc.), redirect to BatchTab instead of
            # showing a single-video download card.  The user gets full
            # per-video control: inspect, uncheck, queue individually.
            if info.playlist_entries:
                self._welcome.pack(fill="both", expand=True)  # reset HomeTab
                self._media_info = None
                batch_tab = self._app.get_tab("batch")
                if batch_tab is not None:
                    self._app.navigate_to("batch")
                    # Defer one frame so BatchTab is fully mapped before we
                    # populate it — prevents CTk layout glitch on first visit.
                    self.after(50, lambda: batch_tab.load_playlist(
                        urls=info.playlist_entries,
                        playlist_title=info.playlist_title or info.uploader or "",
                    ))
                else:
                    self.on_analysis_error(
                        "BatchTab not available — cannot show playlist."
                    )
                return
            # ── Single video / photo / live (existing behaviour) ──────────
            self._media_info = info
            self._populate_card(info)
            self._result_card.pack(fill="x", padx=0, pady=(0, 16))
        except Exception as exc:
            logger.exception("on_analysis_done crashed")
            self._set_status(f"⚠  Display error: {exc}", T.error)

    def on_analysis_error(self, err: str) -> None:
        """Toolbar reported an analysis error."""
        self._loading.pack_forget()
        self._welcome.pack(fill="both", expand=True)
        self._set_status(f"⚠  {err[:120]}", T.error)

    def clear_result(self) -> None:
        """URL was cleared — go back to welcome state."""
        self._result_card.pack_forget()
        self._loading.pack_forget()
        self._welcome.pack(fill="both", expand=True)
        self._media_info = None
        self._set_status("", T.text3)

    def refresh(self) -> None:
        """Called by MainWindow when navigating to this tab."""
        self._folder_lbl.configure(
            text=self._short_path(self._app.service.get_download_dir()))

    # ── Internal ──────────────────────────────────────────────────────────

    def _populate_card(self, info: MediaInfo) -> None:
        platform_color = PLATFORM_COLORS.get(info.platform)
        if platform_color is None:
            platform_color = T.primary

        if info.is_live:
            self._platform_badge.configure(
                text=f"  {info.platform}  ", fg_color=platform_color)
            if not self._live_badge.winfo_ismapped():
                self._live_badge.pack(side="left", padx=(8, 0))
        else:
            self._platform_badge.configure(
                text=f"  {info.platform}  ", fg_color=platform_color)
            if self._live_badge.winfo_ismapped():
                self._live_badge.pack_forget()

        title = info.title[:90] + ("…" if len(info.title) > 90 else "")
        self._title_lbl.configure(text=title)
        self._uploader_lbl.configure(
            text=f"👤  {info.uploader}" if info.uploader else "")

        if info.duration:
            m, s = divmod(info.duration, 60)
            h, m = divmod(m, 60)
            self._duration_lbl.configure(
                text=f"⏱  {h}:{m:02d}:{s:02d}" if h else f"⏱  {m:02d}:{s:02d}")

        if info.is_live:
            self._selected_quality.set("best")
            self._set_status(
                "🔴 Livestream — quality locked to Best Available", T.warning)

        # ── Photo / image detection (Fix 3) ──────────────────────────────
        # Instagram photos, Facebook images, and similar posts return
        # formats=[] because they have no video streams.  We detect this
        # condition and switch to format="best" so yt-dlp downloads the
        # highest-resolution image directly.  The quality picker is hidden
        # since quality options only apply to video.
        is_photo = (
            not info.is_live
            and not info.formats
            and info.duration == 0
        )
        if is_photo:
            self._selected_quality.set("best")
            # Hide quality picker section — not relevant for photos
            if hasattr(self, '_q_sec') and self._q_sec.winfo_ismapped():
                self._q_sec.pack_forget()
            self._set_status(
                "🖼  Photo / image — downloading at best available resolution",
                T.text2,
            )
        else:
            # Ensure quality picker is visible (may have been hidden for a prior photo)
            if hasattr(self, '_q_sec') and not self._q_sec.winfo_ismapped():
                self._q_sec.pack(fill="x", padx=18, pady=(14, 8))
            if not info.is_live:
                self._set_status("", T.text3)

        # Thumbnail
        try:
            self._thumb_lbl._label.configure(image="")
        except Exception:
            pass
        self._thumb_ref = None
        self._thumb_lbl.configure(image=None, text="…")

        if info.thumbnail:
            self._thumb_token += 1
            token = self._thumb_token
            self._app.service.fetch_thumbnail(
                url=info.thumbnail,
                width=180,
                height=102,
                on_done=lambda img, t=token: self.after(
                    0, lambda: self._apply_thumb(img, t)
                ),
                on_error=lambda _, t=token: self.after(
                    0, lambda: self._apply_thumb_error(t)
                ),
            )
        else:
            self._thumb_lbl.configure(text="No Preview")

    def _apply_thumb(self, img: "Image.Image", token: int) -> None:
        if token != self._thumb_token:
            return
        try:
            self._thumb_lbl._label.configure(image="")
        except Exception:
            pass
        ctkimg = ctk.CTkImage(light_image=img, dark_image=img, size=(180, 102))
        self._thumb_ref = ctkimg
        self._thumb_lbl.configure(image=ctkimg, text="")

    def _apply_thumb_error(self, token: int) -> None:
        if token != self._thumb_token:
            return
        self._thumb_lbl.configure(text="No Preview")

    def _add_to_queue(self) -> None:
        if not self._media_info:
            return
        self._app.service.start_download(
            url=self._media_info.url,
            media_info=self._media_info,
            format_id=self._selected_quality.get(),
            output_ext=self._selected_format.get(),
            output_dir=self._custom_output_dir,
        )
        self._app.toast(f"Added: {self._media_info.title[:40]}", "success")
        self._app.navigate_to("queue")
        # Clear state
        self._result_card.pack_forget()
        self._welcome.pack(fill="both", expand=True)
        self._media_info = None
        self._custom_output_dir = None
        # Clear toolbar URL
        toolbar = self._app.get_toolbar()
        if toolbar:
            toolbar.set_url("")

    def _browse_folder(self) -> None:
        import tkinter.filedialog as fd
        chosen = fd.askdirectory(
            title="Select download folder",
            initialdir=str(self._app.service.get_download_dir()),
        )
        if chosen:
            self._custom_output_dir = Path(chosen)
            self._folder_lbl.configure(text=self._short_path(Path(chosen)))

    def _set_status(self, text: str, color: str) -> None:
        if hasattr(self, "_status_lbl"):
            self._status_lbl.configure(text=text, text_color=color or T.text3)

    # ── Theme ──────────────────────────────────────────────────────────────

    def _on_theme(self) -> None:
        if not self.winfo_exists():
            return
        self.configure(fg_color=T.bg)
        self._scroll.configure(
            scrollbar_button_color=T.scrollbar,
            scrollbar_button_hover_color=T.scrollbar_hover,
        )
        if hasattr(self, "_media_card_frame"):
            self._media_card_frame.configure(
                fg_color=T.surface, border_color=T.border)
        if hasattr(self, "_title_lbl"):
            self._title_lbl.configure(text_color=T.text)
        if hasattr(self, "_thumb_lbl"):
            self._thumb_lbl.configure(fg_color=T.surface2)
        if hasattr(self, "_live_badge"):
            self._live_badge.configure(fg_color=T.error)
        if hasattr(self, "_download_btn"):
            self._download_btn.configure(
                fg_color=T.primary, hover_color=T.primary_hover)
        for card in self._quality_cards:
            if card.winfo_exists():
                if card.cget("border_color") == T.primary:
                    card.configure(fg_color=T.primary_dim)
                else:
                    card.configure(fg_color=T.surface2, border_color=T.border)

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _short_path(p: Path) -> str:
        s = str(p)
        return s if len(s) <= 40 else "…" + s[-37:]
