"""
app/services/download_service.py
Application service — the only entry point the UI is allowed to call.
Orchestrates use-cases, wires infrastructure, never touches CTk widgets.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from app.event_bus import EventBus
from app.event_bus import bus as global_bus
from app.services.ffmpeg_convert_service import ConvertQueue
from app.services.taildrop_service import TaildropService
from app.services.thumbnail_service import ThumbnailService
from domain.enums.download_status import DownloadStatus
from domain.models.download_task import DownloadTask, MediaInfo
from infrastructure.config.config_manager import ConfigManager
from infrastructure.downloader.download_manager import DownloadManager
from infrastructure.downloader.yt_dlp_engine import (
    YtDlpEngine,
    _prepare_cookie_for_use,
    _resolve_cookie,
)
from infrastructure.storage.history_repository import HistoryRepository
from utils.helpers import is_valid_url, sanitise_filename

if TYPE_CHECKING:
    from infrastructure.downloader.gallery_dl_engine import GalleryDlEngine

logger = logging.getLogger(__name__)

# yt-dlp errors that mean the post is image-only.
# When these occur on a supported image platform, we fall back to gallery-dl.
_PHOTO_ERRORS = (
    "no video in this post",
    "no video formats found",
    "no formats found",
)

_tt_bughxx_until: dict[str, float] = {}


def _should_fallback_to_gallery_dl(url: str, error_msg: str) -> bool:
    """
    Return True when a yt-dlp failure should be retried with gallery-dl.
    Only triggers for photo-specific errors on supported image platforms.
    Auth / rate-limit errors won't be helped by gallery-dl — don't fall back.
    """
    from infrastructure.downloader.gallery_dl_engine import is_gallery_dl_url

    if not is_gallery_dl_url(url):
        return False
    return any(k in error_msg.lower() for k in _PHOTO_ERRORS)


class DownloadService:
    """
    Facade for the UI layer.
    Thread-safe; all heavy work is dispatched to background threads.
    """

    def __init__(
        self,
        config: ConfigManager,
        download_manager: DownloadManager,
        history_repo: HistoryRepository,
        engine: YtDlpEngine,
        event_bus: Optional[EventBus] = None,
        gallery_engine: Optional[GalleryDlEngine] = None,
    ) -> None:
        self._config = config
        self._manager = download_manager
        self._history = history_repo
        self._engine = engine
        self._bus = event_bus or global_bus
        # Optional gallery-dl engine for image platform fallback.
        # Typed as object to avoid circular imports; duck-typed at call site.
        self._gallery_engine = gallery_engine

        # DEF-005: single-threaded executor so history writes survive shutdown
        self._history_executor: ThreadPoolExecutor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="omnidl-history"
        )

        self._convert_queue = ConvertQueue(max_concurrent=2)
        self._thumbnail_svc = ThumbnailService()

        # Wire completion → history save (DEF-018: one handler for all terminal states)
        self._bus.subscribe(EventBus.DOWNLOAD_COMPLETED, self._save_to_history)
        self._bus.subscribe(EventBus.DOWNLOAD_FAILED, self._save_to_history)
        self._bus.subscribe(EventBus.DOWNLOAD_CANCELLED, self._save_to_history)

        # ── Taildrop (optional, lazy-initialised) ────────────────────────
        # TaildropService subscribes to DOWNLOAD_COMPLETED and sends the
        # finished file to the iPhone when taildrop_enabled=True in config.
        # Constructed here so it shares the same EventBus instance and is
        # torn down together with DownloadService.close().
        self._taildrop = TaildropService(config=self._config, event_bus=self._bus)
        self._bus.subscribe(EventBus.DOWNLOAD_COMPLETED, self._taildrop.on_download_completed)

    # ── Analysis (async) ──────────────────────────────────────────────────

    def analyse_url(
        self,
        url: str,
        on_done: Callable[[MediaInfo], None],
        on_error: Callable[[str], None],
    ) -> threading.Event:
        """
        Fetch metadata for *url* in a daemon thread.
        Calls *on_done* or *on_error* on completion (still background thread —
        UI must use .after() to marshal to main thread).

        Returns a threading.Event that can be set to cancel the in-flight request.
        Setting it causes the worker to raise RuntimeError("Kuaishou: đã huỷ.")
        and call on_error, except for Kuaishou where it raises before on_error.
        """
        cancel_event = threading.Event()

        if not is_valid_url(url):
            on_error("Invalid URL — must start with http:// or https://")
            return cancel_event

        def _worker() -> None:
            try:
                # Instagram live URLs cannot be resolved by yt-dlp or gallery-dl.
                # Short-circuit: build synthetic MediaInfo immediately.
                from infrastructure.downloader.instagram_live_engine import (  # noqa: PLC0415
                    is_instagram_live_url,
                )

                if is_instagram_live_url(url):
                    import re as _re  # noqa: PLC0415

                    _m = _re.search(r"instagram\.com/([A-Za-z0-9._]+)/live", url, _re.I)
                    _username = _m.group(1) if _m else ""
                    info = MediaInfo(
                        url=url,
                        title=f"@{_username} \u2014 Instagram Live" if _username else "Instagram Live",
                        uploader=_username,
                        platform="instagram",
                        source_engine="instagram_live",
                        is_live=True,
                    )
                    self._bus.publish(EventBus.ANALYSIS_DONE, info=info)
                    on_done(info)
                    return

                # Facebook Story URLs are not handled by yt-dlp or gallery-dl;
                # skip extract_info and return synthetic MediaInfo immediately.
                from infrastructure.downloader.facebook_story_engine import (  # noqa: PLC0415
                    is_facebook_story_url,
                )

                if is_facebook_story_url(url):
                    import re as _re  # noqa: PLC0415

                    _sm = _re.search(r"/stories/(\d+)", url)
                    _sid = _sm.group(1) if _sm else ""
                    info = MediaInfo(
                        url=url,
                        title=f"Facebook Story {_sid}" if _sid else "Facebook Story",
                        platform="facebook",
                        source_engine="facebook_story",
                    )
                    self._bus.publish(EventBus.ANALYSIS_DONE, info=info)
                    on_done(info)
                    return

                # waaw.ac: skip extract_info entirely; CDN URL only obtainable during download
                from infrastructure.downloader.waaw_engine import (  # noqa: PLC0415
                    is_waaw_cdn_link,
                    is_waaw_url,
                )

                if is_waaw_url(url) or is_waaw_cdn_link(url):
                    import re as _re  # noqa: PLC0415

                    _wm = _re.search(r"waaw\.ac/f/([A-Za-z0-9_-]+)", url, _re.I)
                    if _wm:
                        _wid = _wm.group(1)
                    else:
                        _wid = url.split("?", 1)[0].rsplit("/", 1)[-1]
                        for _suf in (".m3u8", ".mp4"):
                            if _wid.endswith(_suf):
                                _wid = _wid[: -len(_suf)]
                    info = MediaInfo(
                        url=url,
                        title=f"waaw_{_wid}" if _wid else "waaw video",
                        platform="Waaw",
                        source_engine="waaw",
                        video_id=_wid,
                    )
                    self._bus.publish(EventBus.ANALYSIS_DONE, info=info)
                    on_done(info)
                    return

                # Anonymous pre-signed Instagram/Facebook CDN URL pasted directly
                # by the user (IDM parity): the URL already carries its own oh=/oe=
                # signature, so no Instagram API call and no session cookie are
                # needed -- skip extract_info entirely.
                from infrastructure.downloader.instagram_cdn_engine import (  # noqa: PLC0415
                    is_ig_cdn_url,
                )

                if is_ig_cdn_url(url):
                    _cdn_vid = Path(url.split("?", 1)[0]).stem or "instagram_cdn"
                    info = MediaInfo(
                        url=url,
                        title=f"Instagram {_cdn_vid}",
                        platform="Instagram",
                        source_engine="ig_cdn",
                        is_live=False,
                        video_id=_cdn_vid,
                    )
                    self._bus.publish(EventBus.ANALYSIS_DONE, info=info)
                    on_done(info)
                    return

                # Pass cancel_event to Kuaishou engine so it can abort between
                # strategies and inside the CDP poll loop. Other engines ignore it.
                from infrastructure.downloader.kuaishou_engine import (  # noqa: PLC0415
                    is_kuaishou_url,
                )

                if is_kuaishou_url(url):
                    from infrastructure.downloader.kuaishou_engine import (  # noqa: PLC0415
                        KuaishouEngine,
                    )

                    _ks_engine = KuaishouEngine(self._config)
                    info = _ks_engine.extract_info(url, cancel_event=cancel_event)
                else:
                    info = self._engine.extract_info(url)
                self._bus.publish(EventBus.ANALYSIS_DONE, info=info)
                on_done(info)
            except Exception as exc:
                err = str(exc)
                # TikTok live short-link fallback: yt-dlp's tiktok:live extractor
                # does a fresh API check at extract_info time and may return
                # "not currently live" even when the stream is active (TikTok
                # API race / stale CDN response).  When the URL is a short link
                # (vt/vm.tiktok.com) and the error is "not currently live",
                # resolve the redirect and check if it lands on a /live path.
                # If it does, return a synthetic MediaInfo(is_live=True) so the
                # download attempt proceeds — yt-dlp will re-check during download
                # and the stream may be accessible by then.
                err_l = err.lower()
                # BUG-TT-06 FIX: yt-dlp's TikTokLiveIE scrapes the profile page
                # to obtain room_id, then calls webcast API to verify live status.
                # TikTok now frequently returns profile pages without room_id even
                # during active streams (bot-detection / schema change), causing
                # UserNotLive. This was originally only triggered for vt/vm short
                # links, but the same failure now occurs with canonical /live URLs.
                #
                # Fix: when "not currently live" is returned for any TikTok live
                # URL, use our own profile scraper (_check_tiktok_live_with_room_id)
                # which uses different headers / parsing. If we find room_id, build
                # the mobile share URL m.tiktok.com/share/live/<room_id> — yt-dlp's
                # TikTokLiveIE accepts this pattern and uses room_id directly,
                # bypassing the profile-page scrape entirely.
                import re as _re  # noqa: PLC0415

                _tiktok_any_live_re = _re.compile(
                    r"(?:(?:vt|vm)\.tiktok\.com/|tiktok\.com/@[A-Za-z0-9_.]+/live)",
                    _re.I,
                )
                if _tiktok_any_live_re.search(url) and (
                    "not currently live" in err_l
                    or "channel is not currently live" in err_l
                    or "429" in err_l
                    or "too many requests" in err_l
                ):
                    try:
                        from utils.tiktok_live_checker import (  # noqa: PLC0415
                            _check_tiktok_live_with_room_id,
                            _resolve_short_link,
                        )

                        proxy = getattr(self._config, "proxy", "") or ""
                        # BUG-TT-07 FIX: resolve TikTok cookie and pass it to
                        # _check_tiktok_live_with_room_id so the profile page
                        # fetch carries a valid session cookie.  TikTok now
                        # strips liveRoomInfo for unauthenticated requests.
                        _tt_cookie_raw = _resolve_cookie("https://www.tiktok.com/", self._config) or ""
                        if not _tt_cookie_raw:
                            # BUG-TT-30 FIX: when the user has cleared the configured
                            # TikTok cookie, fall back to a pool account cookie so
                            # detection still runs with valid session cookies.
                            try:
                                _pool30 = getattr(self._manager, "_tiktok_pool", None)
                                if _pool30 is not None:
                                    _acct30 = _pool30._pick_account()
                                    if _acct30 and _acct30.cookie_file:
                                        _tt_cookie_raw = _acct30.cookie_file
                                        logger.debug(
                                            "BUG-TT-30: main cookie absent"
                                            " -- using pool account '%s' for detection",
                                            _acct30.name,
                                        )
                            except Exception:
                                pass
                        _tt_cookie_txt = ""
                        _tt_cookie_is_temp = False
                        if _tt_cookie_raw:
                            _tt_cookie_txt, _tt_cookie_is_temp = _prepare_cookie_for_use(_tt_cookie_raw)
                        # Resolve short link first if needed.
                        # BUG-TT-09: keep the resolved URL (which contains
                        # sec_user_id in its query string) so pass-0 webcast
                        # API can extract sec_user_id without page scraping.
                        resolved = url
                        if "vt.tiktok.com" in url or "vm.tiktok.com" in url:
                            resolved = _resolve_short_link(url, proxy=proxy)

                        _tiktok_live_re = _re.compile(r"tiktok\.com/@([A-Za-z0-9_.]+)/live", _re.I)
                        m = _tiktok_live_re.search(resolved)
                        if m:
                            _username = m.group(1)
                            # BUG-TT-09: pass resolved URL as share_url so
                            # pass-0 can extract sec_user_id from query params.
                            _room_result = _check_tiktok_live_with_room_id(
                                _username,
                                proxy=proxy,
                                cookie_file=_tt_cookie_txt,
                                share_url=resolved,
                            )
                            if _room_result:
                                _live_url, _room_id = _room_result
                                # BUG-TT-12 FIX: use canonical @user/live URL instead of
                                # m.tiktok.com/share/live/<roomId>. With the mobile-share
                                # form, TikTokLiveIE has no 'uploader' from the URL regex
                                # and raises ExtractorError("This livestream has ended")
                                # on any non-2 API status -- not retryable by BUG-TT-12.
                                # With @user/live, yt-dlp raises UserNotLive which maps to
                                # "not currently live" -- caught and retried by BUG-TT-12.
                                _download_url = _live_url
                                logger.info(
                                    "BUG-TT-06: TikTok live @%s roomId=%s"
                                    " -- using canonical live URL for download",
                                    _username,
                                    _room_id,
                                )
                                info = MediaInfo(
                                    url=_download_url,
                                    title=f"@{_username} -- TikTok Live",
                                    uploader=_username,
                                    platform="TikTok",
                                    source_engine="yt_dlp",
                                    is_live=True,
                                    tiktok_room_id=_room_id,
                                )
                                self._bus.publish(EventBus.ANALYSIS_DONE, info=info)
                                on_done(info)
                                return
                            else:
                                # BUG-TT-19 FIX: checker returned None -- could be
                                # bot-detection blocking the page scrape, not a
                                # confirmed "not live" signal. One 20s retry to
                                # handle brief TikTok API race; then BUG-TT-XX.
                                import time as _time_tt19  # noqa: PLC0415

                                for _retry_delay in (10, 20):
                                    if _time_tt19.monotonic() < _tt_bughxx_until.get(_username, 0.0):
                                        break
                                    logger.info(
                                        "TikTok live checker: @%s -- roomId not found"
                                        " (bot-detection or API race);"
                                        " waiting %ds, retrying",
                                        _username,
                                        _retry_delay,
                                    )
                                    _time_tt19.sleep(_retry_delay)
                                    _room_result2 = _check_tiktok_live_with_room_id(
                                        _username,
                                        proxy=proxy,
                                        cookie_file=_tt_cookie_txt,
                                        share_url=resolved,
                                    )
                                    if _room_result2:
                                        _live_url2, _room_id2 = _room_result2
                                        logger.info(
                                            "BUG-TT-19: checker succeeded on %ds retry @%s roomId=%s",
                                            _retry_delay,
                                            _username,
                                            _room_id2,
                                        )
                                        _info2 = MediaInfo(
                                            url=_live_url2,
                                            title=f"@{_username} -- TikTok Live",
                                            uploader=_username,
                                            platform="TikTok",
                                            source_engine="yt_dlp",
                                            is_live=True,
                                            tiktok_room_id=_room_id2,
                                        )
                                        self._bus.publish(EventBus.ANALYSIS_DONE, info=_info2)
                                        on_done(_info2)
                                        return
                                    try:
                                        # BUG-TT-19: use resolved (canonical @user/live) not the
                                        # original short URL -- avoids 429 from vm.tiktok re-expansion
                                        _retry_info = self._engine.extract_info(resolved)
                                        self._bus.publish(EventBus.ANALYSIS_DONE, info=_retry_info)
                                        on_done(_retry_info)
                                        return
                                    except Exception as _retry_exc:
                                        logger.debug(
                                            "BUG-TT-19: retry after %ds also failed: %s",
                                            _retry_delay,
                                            _retry_exc,
                                        )
                                # BUG-TT-XX: Detection fully blocked by bot-detection
                                # on all 4 strategies + yt-dlp.  Instead of on_error(),
                                # proceed optimistically — download phase (BUG-TT-16)
                                # uses pool cookies and can succeed when analysis cookies
                                # are rate-limited. If stream is truly dead the download
                                # task fails quickly; if live the download succeeds.
                                _now_tt = _time_tt19.monotonic()
                                for _u in [k for k, v in _tt_bughxx_until.items() if v <= _now_tt]:
                                    del _tt_bughxx_until[_u]
                                _tt_bughxx_until[_username] = _now_tt + 60.0
                                logger.info(
                                    "BUG-TT-XX: detection fully blocked @%s"
                                    " -- optimistic download with pool cookies",
                                    _username,
                                )
                                _opt_info = MediaInfo(
                                    url=f"https://www.tiktok.com/@{_username}/live",
                                    title=f"@{_username} -- TikTok Live",
                                    uploader=_username,
                                    platform="TikTok",
                                    source_engine="yt_dlp",
                                    is_live=True,
                                )
                                self._bus.publish(EventBus.ANALYSIS_DONE, info=_opt_info)
                                on_done(_opt_info)
                                return
                    except Exception as _tt_exc:
                        logger.debug("TikTok live short-link resolve failed: %s", _tt_exc)
                    finally:
                        # BUG-TT-07: clean up decrypted temp cookie file
                        if _tt_cookie_is_temp and _tt_cookie_txt:
                            try:
                                import os as _os  # noqa: PLC0415

                                _os.unlink(_tt_cookie_txt)
                            except OSError:
                                pass
                # Auto-routing: when yt-dlp finds no video formats on a
                # supported image platform, retry silently with gallery-dl.
                # The user never sees this fallback happen — they just get
                # the correct MediaInfo (source_engine="gallery_dl").
                if self._gallery_engine and _should_fallback_to_gallery_dl(url, err):
                    logger.info(
                        "yt-dlp returned no video formats for %s — falling back to gallery-dl",
                        url,
                    )
                    try:
                        info = self._gallery_engine.extract_info(url)
                        self._bus.publish(EventBus.ANALYSIS_DONE, info=info)
                        on_done(info)
                        return
                    except Exception as gdl_exc:
                        err = str(gdl_exc)
                if cancel_event.is_set():
                    logger.debug("analyse_url: cancelled — suppressing on_error")
                    return
                self._bus.publish(EventBus.ANALYSIS_FAILED, error=err)
                on_error(err)

        threading.Thread(target=_worker, daemon=True, name="omnidl-analyse").start()
        return cancel_event

    # ── Download lifecycle ────────────────────────────────────────────────

    def start_download(
        self,
        url: str,
        media_info: MediaInfo,
        format_id: str,
        output_ext: str,
        output_dir: Optional[Path] = None,
    ) -> DownloadTask:
        """Create a DownloadTask and submit it to the manager.

        If the same URL is already QUEUED, DOWNLOADING, or PROCESSING the
        existing task is returned immediately — no duplicate is created.
        Re-downloading a COMPLETED/FAILED/CANCELLED URL always starts a new job.
        """
        # Duplicate guard: only block active (not terminal) duplicates.
        for existing in self._manager.get_all_tasks():
            if existing.url == url and existing.status in DownloadStatus.active_states():
                logger.info(
                    "Duplicate URL ignored — task %s already active: %s",
                    existing.id,
                    url,
                )
                return existing

        resolved_dir = output_dir or self._config.download_dir
        resolved_dir.mkdir(parents=True, exist_ok=True)

        # If media_info carries a resolved canonical URL (e.g. TikTok short-link
        # resolved to /@user/live during analyse), use it as task.url so yt-dlp
        # receives the canonical URL directly instead of re-resolving the short
        # link and potentially hitting the same "not currently live" API race.
        task_url = url
        if (
            media_info is not None
            and media_info.url
            and media_info.url != url
            and media_info.url.startswith("http")
        ):
            task_url = media_info.url

        task = DownloadTask(
            url=task_url,
            media_info=media_info,
            format_id=format_id,
            output_ext=output_ext,
            output_dir=str(resolved_dir),
        )
        self._manager.enqueue(task)
        return task

    def pause_download(self, task_id: str) -> None:
        self._manager.pause(task_id)

    def resume_download(self, task_id: str) -> None:
        self._manager.resume(task_id)

    def cancel_download(self, task_id: str) -> None:
        self._manager.cancel(task_id)

    def clear_finished(self, exclude_ids: "frozenset[str] | None" = None) -> None:
        self._manager.clear_terminal(exclude_ids=exclude_ids)

    def clear_specific(self, ids: list[str]) -> None:
        self._manager.clear_specific(ids)

    def rebuild_tiktok_pool(self) -> None:
        self._manager.rebuild_tiktok_pool()

    # ── Query ─────────────────────────────────────────────────────────────

    def get_task(self, task_id: str) -> Optional[DownloadTask]:
        """Return a single task by ID, or None if not found."""
        return self._manager.get_task(task_id)

    def get_all_tasks(self) -> list[DownloadTask]:
        return self._manager.get_all_tasks()

    def get_history(self) -> list[dict]:
        return self._history.all()

    def search_history(self, query: str) -> list[dict]:
        return self._history.search(query)

    def clear_history(self) -> None:
        self._history.clear()

    def delete_history_entry(self, task_id: str) -> None:
        self._history.remove(task_id)

    def rename_download(self, task_id: str, new_name: str) -> str:
        """
        Rename the output file of a task on disk and sync both the in-memory
        task (if still queued) and the history entry (if already persisted).

        Raises FileNotFoundError / FileExistsError / ValueError on failure.
        """
        task = self._manager.get_task(task_id)
        entry = self._history.get_by_id(task_id)
        raw = (task.filename if task else "") or ((entry or {}).get("filename") or "")
        if not raw:
            raise FileNotFoundError("No file recorded for this task")

        old_path = Path(raw)
        if not old_path.is_absolute():
            out_dir = (task.output_dir if task else "") or (entry or {}).get("output_dir", "")
            if out_dir:
                old_path = Path(out_dir) / old_path
        old_path = old_path.resolve()

        if old_path.is_dir():
            raise ValueError("Cannot rename a multi-file (gallery-dl) download")
        if not old_path.exists():
            raise FileNotFoundError(f"File not found: {old_path}")

        safe_name = sanitise_filename(new_name)
        new_path = old_path.parent / safe_name
        if new_path == old_path:
            return str(new_path)
        if new_path.exists():
            raise FileExistsError(f"A file named '{safe_name}' already exists")

        old_path.rename(new_path)

        if task:
            task.filename = str(new_path)
        self._history.update_filename(task_id, str(new_path))
        return str(new_path)

    def get_history_stats(self) -> dict:
        entries = self._history.all()
        by_platform: dict[str, int] = {}
        by_status: dict[str, int] = {}
        total_bytes = 0
        for e in entries:
            p = e.get("platform", "unknown")
            s = e.get("status", "unknown")
            by_platform[p] = by_platform.get(p, 0) + 1
            by_status[s] = by_status.get(s, 0) + 1
            total_bytes += e.get("downloaded_bytes", 0)
        return {
            "total": len(entries),
            "total_bytes": total_bytes,
            "by_platform": by_platform,
            "by_status": by_status,
        }

    @property
    def taildrop(self) -> "TaildropService":
        """Expose TaildropService for on-demand transfers via Remote API."""
        return self._taildrop

    def convert_to_mp4(
        self,
        source: Path,
        target_ext: str = "mp4",
        encode_settings=None,
        on_progress: Optional[Callable[[float], None]] = None,
        on_done: Optional[Callable[[Path], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> Callable[[], None]:
        """Convert *source* to the requested format via the shared ConvertQueue.

        Returns a cancel callable; calling it stops the job as soon as possible.
        Callbacks fire on the worker thread — UI callers must marshal to the
        main thread (e.g. via ``_ui_queue``).
        """
        return self._convert_queue.submit(
            source=source,
            target_ext=target_ext,
            encode_settings=encode_settings,
            on_progress=on_progress,
            on_done=on_done,
            on_error=on_error,
        )

    def fetch_thumbnail(
        self,
        url: str,
        width: int,
        height: int,
        on_done: Callable,
        on_error: Callable[[str], None],
    ) -> None:
        """Fetch and resize a thumbnail in a background thread.

        Delegates to ThumbnailService which validates the URL against
        SSRF patterns (RFC-1918, loopback, link-local) before fetching.
        Callbacks fire on the worker thread — callers must use after() to
        marshal widget updates to the UI thread.
        """
        self._thumbnail_svc.fetch_async(
            url=url,
            width=width,
            height=height,
            on_done=on_done,
            on_error=on_error,
        )

    def check_profile_live(
        self,
        url: str,
        on_done: "Callable[[Optional[str]], None]",
        on_error: "Callable[[str], None]",
        deep: bool = False,
    ) -> None:
        """Check if an Instagram profile URL is currently live.

        Spawns a daemon thread (same pattern as analyse_url).
        Calls on_done(live_url_or_None) or on_error(message).
        Callers must use after() to marshal UI updates.
        """
        import threading

        from utils.instagram_live_checker import (
            check_instagram_live,
            extract_instagram_username,
        )

        username = extract_instagram_username(url)
        if not username:
            on_error("Không thể lấy username từ URL.")
            return

        # Use Instagram-specific cookie when available — instagram_live_checker
        # requires a valid sessionid from Instagram (not from another platform).
        cookie_raw = _resolve_cookie("https://www.instagram.com/", self._config) or ""
        proxy = self._config.proxy

        def _worker() -> None:
            # FIX: _resolve_cookie returns the encrypted .enc path; decrypt it
            # before check_instagram_live calls MozillaCookieJar.load, otherwise
            # jar.load reads encrypted binary as text and crashes on Windows with
            # "'charmap' codec can't decode byte 0x9d". Same as BUG-TT-07 below.
            cookie_txt = ""
            cookie_is_temp = False
            try:
                if cookie_raw:
                    cookie_txt, cookie_is_temp = _prepare_cookie_for_use(cookie_raw)
                live_url = check_instagram_live(
                    username=username,
                    cookie_file=cookie_txt,
                    proxy=proxy,
                    deep=deep,
                )
                on_done(live_url)
            except Exception as exc:
                on_error(str(exc))
            finally:
                if cookie_is_temp and cookie_txt:
                    try:
                        import os as _os  # noqa: PLC0415

                        _os.unlink(cookie_txt)
                    except OSError:
                        pass

        threading.Thread(target=_worker, daemon=True).start()

    def check_tiktok_profile_live(
        self,
        url: str,
        on_done: "Callable[[Optional[str]], None]",
        on_error: "Callable[[str], None]",
    ) -> None:
        """Check if a TikTok profile URL is currently live.

        Spawns a daemon thread (same pattern as check_profile_live).
        Calls on_done(live_url_or_None) or on_error(message).
        Callers must use after() / _ui_queue to marshal UI updates.

        Does NOT require cookies — TikTok's live-check API is public.
        """
        import threading

        from utils.tiktok_live_checker import (
            _resolve_short_link,
            check_tiktok_live,
            extract_tiktok_username,
            extract_tiktok_username_from_live_url,
        )

        proxy = self._config.proxy

        def _worker() -> None:
            _tt_cookie_txt = ""
            _tt_cookie_is_temp = False
            try:
                # Username extraction stays in the worker: vt/vm short links
                # resolve over the network and must not block the UI thread.
                target = url
                if "vt.tiktok.com" in url or "vm.tiktok.com" in url:
                    target = _resolve_short_link(url, proxy=proxy)
                username = extract_tiktok_username(target) or extract_tiktok_username_from_live_url(target)
                if not username:
                    on_error("Không thể lấy username từ URL TikTok.")
                    return

                # BUG-TT-07 FIX: resolve and decrypt TikTok cookie so
                # check_tiktok_live can authenticate the profile page fetch.
                _tt_cookie_raw = _resolve_cookie("https://www.tiktok.com/", self._config) or ""
                if _tt_cookie_raw:
                    _tt_cookie_txt, _tt_cookie_is_temp = _prepare_cookie_for_use(_tt_cookie_raw)

                live_url = check_tiktok_live(
                    username=username,
                    proxy=proxy,
                    cookie_file=_tt_cookie_txt,
                )
                on_done(live_url)
            except Exception as exc:
                on_error(str(exc))
            finally:
                if _tt_cookie_is_temp and _tt_cookie_txt:
                    try:
                        import os as _os  # noqa: PLC0415

                        _os.unlink(_tt_cookie_txt)
                    except OSError:
                        pass

        threading.Thread(target=_worker, daemon=True).start()

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def close(self) -> None:
        """Flush pending history writes and release resources (DEF-005).

        Call this AFTER manager.shutdown(wait=True) to ensure all
        completion events have already been published before the
        executor is shut down.
        """
        # Taildrop must close first: its executor may still be sending a
        # file triggered by the last DOWNLOAD_COMPLETED event.
        self._taildrop.close()
        from utils.tiktok_live_checker import get_health_daemon as _get_hd  # noqa: PLC0415

        daemon = _get_hd()
        if daemon is not None:
            daemon.stop()

        from utils.instagram_http import close_shared_session as _close_ig  # noqa: PLC0415

        _close_ig()
        self._history_executor.shutdown(wait=True)

    # ── Internal ──────────────────────────────────────────────────────────

    def _save_to_history(self, task: DownloadTask) -> None:
        """Submit a history-write job (DEF-005, DEF-018).

        Replaces three identical daemon-thread handlers with one method
        backed by a non-daemon ThreadPoolExecutor so writes complete
        before process exit.
        """
        self._history_executor.submit(self._history.add, task)
