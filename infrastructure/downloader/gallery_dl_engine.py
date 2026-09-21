"""
infrastructure/downloader/gallery_dl_engine.py
Thin subprocess wrapper around gallery-dl for image/gallery downloads.

Used as a fallback when yt-dlp finds no video formats — e.g. Instagram
photo posts, Twitter image tweets.  Activated automatically via
source_engine="gallery_dl" on MediaInfo when yt-dlp raises a photo error.

Cookie file:  shares the same file configured in Settings → Network.
Output dir:   same download_dir as yt-dlp downloads.
Progress:     file-count based (gallery-dl has no byte-level hook).
Cancel:       proc.kill() when task.is_cancellation_requested is set.
"""

from __future__ import annotations

import datetime
import json
import logging
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Optional

from domain.enums.download_status import DownloadStatus
from domain.models.download_task import DownloadTask, MediaInfo
from infrastructure.config.config_manager import ConfigManager
from utils.i18n import t

logger = logging.getLogger(__name__)

# Suppress console window on Windows for all subprocess calls.
_WIN_NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Platforms gallery-dl handles better than yt-dlp for image content.
# BUG-FB-PHOTO FIX: facebook.com added so photo-only posts (yt-dlp reports
# "no video in this post") fall back to gallery-dl instead of failing outright.
# Safe: facebook_story_engine's is_facebook_story_url() check runs BEFORE
# this fallback in download_manager routing, so Story/fb.watch URLs are
# never affected; plain video posts never hit this path (fallback only fires
# on a photo-only error, which video posts don't raise).
_SUPPORTED_RE = re.compile(
    r"instagram\.com|twitter\.com|x\.com|pinterest\.|pixiv\.net|deviantart\.com|facebook\.com",
    re.I,
)


def is_gallery_dl_url(url: str) -> bool:
    """True when the URL belongs to a platform gallery-dl supports for images."""
    return bool(_SUPPORTED_RE.search(url))


# Facebook photo / album URLs.  yt-dlp's FacebookIE._VALID_URL matches none of
# these forms, so it raises "Unsupported URL" — a hard error that stopped the
# photo-error fallback from ever running.  These go straight to gallery-dl.
# /<user>/posts/<id> is deliberately absent: those can hold a video, so they
# stay on the yt-dlp-first path with the photo-error fallback behind it.
_FB_PHOTO_RE = re.compile(
    r"facebook\.com/(?:"
    r"photo(?:\.php)?/?\?|"
    r"media/set/?\?|"
    r"[^/?#]+/photos(?:_by|_albums)?(?:/|\?|$)"
    r")|facebook\.com/[^?#]*\?[^#]*\bset=a\.",
    re.I,
)


def is_facebook_photo_url(url: str) -> bool:
    """True for a Facebook photo or album URL that only gallery-dl can fetch."""
    return bool(_FB_PHOTO_RE.search(url))


# BUG-FB-SETID FIX: a /share/p/ link resolves to story.php?story_fbid=X&id=Y, and
# gallery-dl's USER_PATTERN excludes only "permalink.php" and "photo.php" — so
# "story.php" is captured as a *profile name*, routed to FacebookUserExtractor, and
# dies with "An unexpected error occurred: KeyError - 'set_id'" (log 2026-09-09
# 15:18:12/15:18:21, task 7f2b7e61, both attempts).  The /<owner>/posts/<id> form
# routes to FacebookSetExtractor, which parses the post page and falls back to the
# single-photo path when the post holds no photo set.
_FB_STORY_PHP_RE = re.compile(r"facebook\.com/story\.php\?", re.I)

# BUG-FB-SHARE: /share/{p,v,r}/<token> is Facebook's own short form.  gallery-dl
# has no extractor for it and exits with "Unsupported URL"; the resolution to the
# canonical story.php URL used to happen only inside yt_dlp_engine.extract_info,
# so a photo post routed to gallery-dl still arrived as a /share/ link.
_FB_SHARE_RE = re.compile(r"facebook\.com/share/(?:p|v|r)/", re.I)

# BUG-FB-POST: a Facebook feed post (story.php / permalink.php / <user>/posts/)
# can hold photos, photos with a music track, or photos plus a video.
# gallery-dl's FacebookSetExtractor yields the photos only, so these URLs need
# both engines: gallery-dl for the images, then a yt-dlp pass for the videos.
_FB_POST_RE = re.compile(
    r"facebook\.com/(?:"
    r"story\.php\?[^#]*\bstory_fbid=|"
    r"permalink\.php\?[^#]*\bstory_fbid=|"
    r"(?:[^/?#]+/)+posts/|"
    r"share/p/"
    r")",
    re.I,
)


def is_facebook_post_url(url: str) -> bool:
    """True for a Facebook feed post that may mix photos, music and video."""
    return bool(_FB_POST_RE.search(url))


# BUG-FB-ADVID: yt-dlp's FacebookIE builds its entry list from every relay
# payload on the post page (parse_attachment over `nodes`), and Facebook injects
# suggested / sponsored story nodes into that same payload.  On a photo-only post
# the requested story yields nothing, so the rescue pass below happily returned
# the *advert* video instead of the photos.  Log evidence (omnidl_debug.log,
# 2026-09-20): the same advert 1767163571189463 came back for two unrelated
# posts -- 1750153570452113 owned by 100063724590889 (09:06:01, task a09e9e9f)
# and 122230506620352435 owned by 61560573071079 (19:29:48, task b8e31171).
#
# A *playlist* entry carries uploader_id = owner.id, so the post owner taken from
# the URL discriminates those.  A lone entry does NOT: FacebookIE returns
# merge_dicts(webpage_info, video_info) for a single result and webpage_info
# carries the *post* owner, which wins -- so the advert inherits the post owner's
# id and slips past an owner-only filter.  strict_story_id below covers that case.
_FB_OWNER_POSTS_RE = re.compile(r"facebook\.com/(\d+)/posts/", re.I)
_FB_OWNER_QUERY_RE = re.compile(r"facebook\.com/[^#]*[?&]id=(\d+)", re.I)
_FB_STORY_POSTS_RE = re.compile(r"facebook\.com/(?:[^/?#]+/)+posts/(\d+)", re.I)
_FB_STORY_QUERY_RE = re.compile(r"facebook\.com/[^#]*[?&]story_fbid=(\d+)", re.I)


def facebook_owner_id(url: str) -> str:
    """Numeric owner id of a Facebook post URL, or "" when it cannot be derived.

    Group posts are excluded: /groups/<gid>/posts/<id> puts the group id where
    the owner id would be, and the real owner is the posting member.
    """
    if re.search(r"facebook\.com/groups/", url, re.I):
        return ""
    m = _FB_OWNER_POSTS_RE.search(url) or _FB_OWNER_QUERY_RE.search(url)
    return m.group(1) if m else ""


def facebook_story_id(url: str) -> str:
    """Numeric story id of a Facebook post URL, or "" when there is none.

    An opaque "pfbid..." story token is deliberately not returned: yt-dlp's
    info["id"] is always numeric, so such a token can neither confirm nor deny
    a match and a strict comparison against it would reject every video.
    """
    m = _FB_STORY_QUERY_RE.search(url) or _FB_STORY_POSTS_RE.search(url)
    return m.group(1) if m else ""


def normalize_gallery_dl_url(url: str) -> str:
    """Rewrite URL forms gallery-dl mis-dispatches. Returns *url* unchanged otherwise."""
    if _FB_SHARE_RE.search(url):
        from infrastructure.downloader.yt_dlp_engine import (  # noqa: PLC0415
            _resolve_facebook_share_url,
        )

        url = _resolve_facebook_share_url(url)
    if not _FB_STORY_PHP_RE.search(url):
        return url
    from urllib.parse import parse_qs, urlparse

    q = parse_qs(urlparse(url).query)
    story_fbid = (q.get("story_fbid") or [""])[0]
    owner_id = (q.get("id") or [""])[0]
    if story_fbid.isdigit() and owner_id.isdigit():
        rewritten = f"https://www.facebook.com/{owner_id}/posts/{story_fbid}"
        logger.debug("gallery-dl: rewrote story.php URL %s -> %s", url, rewritten)
        return rewritten
    return url


# BUG-FB-PCB: gallery-dl's FacebookSetExtractor reaches a feed post by two
# different routes, and only one of them works for a multi-photo post.
#
#   "/<owner>/posts/<story_fbid>"  -> parses the post page itself.  Facebook
#       serves that page without any photo payload, so parse_post_page() finds
#       no '"__isMedia":"Photo"' block, post_photo stays empty and items()
#       dies on `params["fbid"]` with KeyError 'fbid'.
#   "/media/set/?set=pcb.<story_fbid>" -> asks for the post's photo set
#       directly and never needs the post page.
#
# Log evidence (omnidl_debug.log 2026-09-20 22:06:11 and 22:13:28, story
# 122182130744968412): the posts route returned no content on both attempts,
# so the BUG-FB-ADVID guard in yt_dlp_engine had nothing to compare against
# and kept the injected advert video 1789050085345855.  The pcb route resolves
# the same story to set pcb.122182130744968412 / first photo
# 122182130522968412.
#
# Try the set route first and keep the posts route behind it: a single-photo
# post has no pcb set, and there parse_post_page's post_photo fallback is what
# finds the image.
def gallery_dl_url_candidates(url: str) -> list[str]:
    """URL forms to hand gallery-dl for *url*, best first (never empty)."""
    primary = normalize_gallery_dl_url(url)
    story_id = facebook_story_id(primary)
    if not story_id:
        return [primary]
    return [f"https://www.facebook.com/media/set/?set=pcb.{story_id}", primary]


def _parse_dump_json(stdout: str) -> list[dict[str, Any]]:
    """Collect the image entries from gallery-dl ``--dump-json`` output.

    gallery-dl emits one JSON array per line:
        [1, "url", {metadata}]  → type 1 = image URL
        [0, {...}]              → type 0 = message / count info
    """
    items: list[dict[str, Any]] = []
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line.startswith("["):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, list) and len(obj) >= 3 and obj[0] == 1:
            items.append(obj[2])
    return items


def _find_executable() -> Optional[str]:
    """
    Locate gallery-dl binary.
    Search order:
      1. Frozen app: sys._MEIPASS/gallery-dl/ (bundled binary)
      2. python -m gallery_dl — works in uv/venv where the gallery-dl script
         wrapper is a uv trampoline that fails with "canonicalize script path"
         on Windows when the path contains spaces or non-ASCII characters.
      3. System PATH (shutil.which)
      4. Scripts dir of the current Python environment (venv / dev)
    Returns None if not found — callers raise RuntimeError with install hint.
    """
    # 1. PyInstaller frozen bundle — gallery-dl binary lives in _MEIPASS/gallery-dl/
    if getattr(sys, "frozen", False):
        meipass = Path(getattr(sys, "_MEIPASS", ""))
        for name in ("gallery-dl", "gallery-dl.exe"):
            candidate = meipass / "gallery-dl" / name
            if candidate.is_file():
                return str(candidate)
        # Frozen: do not fall through to python -m (no venv in frozen app)
        return None

    # 2. python -m gallery_dl — preferred in uv/venv dev environments.
    # The gallery-dl script entry point on Windows is a uv trampoline (.exe
    # wrapper) that can fail with "uv trampoline failed to canonicalize script
    # path" when the venv path contains spaces or non-ASCII chars.  Running via
    # the Python interpreter directly bypasses the trampoline entirely.
    try:
        result = subprocess.run(
            [sys.executable, "-m", "gallery_dl", "--version"],
            capture_output=True,
            timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode == 0:
            # Return a sentinel that _exe() / _base_cmd() recognize as
            # "use python -m gallery_dl" invocation.
            return f"{sys.executable}::gallery_dl_module"
    except Exception:
        pass

    # 3. System PATH
    found = shutil.which("gallery-dl")
    if found:
        return found

    # 4. Scripts dir alongside Python executable (venv Scripts / bin)
    scripts = Path(sys.executable).parent
    for name in ("gallery-dl", "gallery-dl.exe", "gallery_dl", "gallery_dl.exe"):
        candidate = scripts / name
        if candidate.is_file():
            return str(candidate)
    return None


def _exe_cmd(exe: str) -> list[str]:
    """Convert the value returned by _find_executable() to a command prefix.

    When exe is the sentinel "python_path::gallery_dl_module", returns
    [python, "-m", "gallery_dl"].  Otherwise returns [exe].
    """
    if exe.endswith("::gallery_dl_module"):
        python = exe[: -len("::gallery_dl_module")]
        return [python, "-m", "gallery_dl"]
    return [exe]


def _friendly_error(msg: str) -> str:
    """Map gallery-dl stderr messages to user-readable Vietnamese/English text.

    Each message intentionally starts with an English keyword that matches
    DownloadManager._HARD_ERROR_KEYWORDS so hard errors are never retried.
    """
    m = msg.lower()
    if "login" in m or "401" in m or "cookie" in m or "authentication" in m:
        return "login: " + t("err.gdl_login_required")
    if "404" in m or "not found" in m:
        return "not found: " + t("err.not_found")
    if "429" in m or "rate" in m or "too many" in m:
        return "blocked: " + t("err.gdl_rate_limited")
    if "gallery-dl" in m and ("not found" in m or "no such" in m):
        return "unsupported url: " + t("err.gdl_not_installed_short")
    if "private" in m:
        return "private: " + t("err.gdl_private")
    return msg[:300] if msg else t("err.gdl_unknown")


# ── BUG-BW helpers ────────────────────────────────────────────────────────────


def _has_audio(video_path: Path, ffmpeg_dir: Optional[str] = None) -> bool:
    """Return True when *video_path* contains at least one audio stream.

    Uses ffprobe (bundled with FFmpeg) with a fast stream-count query.
    Returns True on any error so that callers do NOT attempt a rescue pass
    when the check itself fails (fail-open is safer than false negatives).
    """
    ffprobe = "ffprobe"
    if ffmpeg_dir:
        candidate = Path(ffmpeg_dir) / "ffprobe"
        if not candidate.is_file():
            candidate = Path(ffmpeg_dir) / "ffprobe.exe"
        if candidate.is_file():
            ffprobe = str(candidate)

    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "quiet",
                "-select_streams",
                "a",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "csv=p=0",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=_WIN_NO_WINDOW,
        )
        # ffprobe outputs one "audio" line per audio stream; empty = no audio
        return bool(result.stdout.strip())
    except Exception as exc:
        logger.debug("_has_audio: ffprobe check failed for %s — %s", video_path.name, exc)
        return True  # fail-open: assume audio OK, skip rescue pass


def _ytdlp_carousel_videos(
    url: str,
    output_dir: Path,
    dl_start_ts: float,
    cookie_file: Optional[str] = None,
    proxy: Optional[str] = None,
    ffmpeg_dir: Optional[str] = None,
    max_retries: int = 0,
    rescue_dir: Optional[Path] = None,
    owner_id: str = "",
    strict_story_id: str = "",
) -> list[str]:
    """Download all VIDEO items in an Instagram carousel with proper audio.

    Uses yt-dlp with noplaylist=False + ignoreerrors=True so:
    - All video items are fetched with bestvideo+bestaudio merge (audio OK).
    - Image items silently raise "no video formats" and are skipped.

    Returns a list of newly created video file paths.
    Returns [] on any failure so callers can treat it as a no-op.
    """
    import yt_dlp  # noqa: PLC0415 — lazy import, yt-dlp may not always be present

    # BUG-BT: when rescue_dir is provided (the gallery-dl output subdir), place
    # rescued files directly there — no uploader subdir — so they share the same
    # directory as the gallery-dl images and task.filename already points there.
    if rescue_dir is not None:
        outtmpl = str(rescue_dir / "%(title).60B [%(id).30B].%(ext)s")
    else:
        outtmpl = str(
            output_dir / "%(uploader,channel|instagram_rescue)s" / "%(title).60B [%(id).30B].%(ext)s"
        )
    opts: dict[str, object] = {
        # BUG-BX / BUG-BY: Instagram carousel videos may be:
        #   (a) separate DASH streams (video-only + audio-only) → need mux
        #   (b) combined stream with embedded audio (background music posts)
        # The old "bestvideo[acodec=none]+bestaudio[vcodec=none]" selector
        # only matched case (a). For case (b), acodec=none rejects the combined
        # stream, the fallback bestvideo*+bestaudio* picks the combined stream
        # as "best video" but pairs it with a separate audio stream — FFmpeg
        # then muxes them and the result has duplicated/wrong audio or silence.
        # Fix: prefer separate streams for proper mux, but fall back to the
        # combined stream directly (no forced re-mux) when no separate pair
        # exists. bestvideo+bestaudio covers both: yt-dlp uses separate streams
        # when available, and the combined stream when not.
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        # BUG-BW: expand carousel so all video items are fetched
        "noplaylist": False,
        "socket_timeout": 30,
        "retries": max_retries,
        "windowsfilenames": True,
        "trim_file_name": 180,
    }
    if cookie_file:
        opts["cookiefile"] = cookie_file
    if proxy:
        opts["proxy"] = proxy
    if ffmpeg_dir:
        opts["ffmpeg_location"] = ffmpeg_dir

    # BUG-FB-ADVID: drop the suggested / sponsored videos Facebook injects into
    # the post page (see the comment on _FB_OWNER_POSTS_RE above).
    #
    # strict_story_id is set only when gallery-dl saved no image for a Facebook
    # post — the post is then known to hold no photo set, so the sole legitimate
    # video is the requested story itself and anything else is page furniture.
    # Otherwise fall back to the owner comparison, which is what discriminates
    # the entries of a multi-video post.
    # ponytail: a profile owner can surface as an opaque "pfbid..." uploader_id
    # that no numeric post URL can be compared against, so those are let through
    # rather than risk dropping a real video.
    if owner_id or strict_story_id:

        def _reject_advert(info: dict, *, incomplete: bool = False) -> Optional[str]:
            if incomplete:
                return None
            vid = str(info.get("id") or "")
            if strict_story_id:
                if vid == strict_story_id:
                    return None
                return f"not this post's video (id {vid} != story {strict_story_id})"
            uid = str(info.get("uploader_id") or info.get("channel_id") or "")
            if not uid.isdigit() or uid == owner_id:
                return None
            return f"not part of this post (uploader {uid} != {owner_id})"

        opts["match_filter"] = _reject_advert

    # BUG-BT: when rescue_dir is provided, scan only that directory with a tight
    # window (dl_start_ts, no -5s offset) so gallery-dl's just-written silent
    # files (same dir, slightly older mtime) are excluded.  Without this, both
    # the 5 silent gallery-dl files and 5 new yt-dlp files are returned (10
    # total), and the stale paths persist in task.gallery_dl_files after the
    # silent files are deleted on disk.
    if rescue_dir is not None:
        _scan_root = rescue_dir
        rescue_start = dl_start_ts  # tight — gallery-dl files pre-date this
    else:
        _scan_root = output_dir
        rescue_start = dl_start_ts - 5.0

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except Exception as exc:
        logger.warning("BUG-BW rescue: yt-dlp failed for %s — %s", url, exc)
        return []

    # Collect files created during the rescue pass
    _vid_exts = frozenset({".mp4", ".mov", ".webm", ".mkv", ".m4v"})
    new_files: list[str] = []
    try:
        for f in _scan_root.rglob("*"):
            if (
                f.is_file()
                and f.suffix.lower() in _vid_exts
                and f.stat().st_mtime >= rescue_start
                and f.stat().st_size > 10_000  # skip tiny/corrupt files
            ):
                new_files.append(str(f))
    except Exception as scan_exc:
        logger.warning("BUG-BW rescue: post-rescue scan failed — %s", scan_exc)

    logger.info(
        "BUG-BW rescue: yt-dlp rescued %d video file(s) with audio from %s",
        len(new_files),
        url,
    )
    return new_files


class GalleryDlEngine:
    """
    Wraps gallery-dl CLI as a subprocess.

    Public interface mirrors YtDlpEngine so DownloadManager can route to
    either engine without needing to know the difference:
        extract_info(url) → MediaInfo
        download(task, on_progress, on_postprocess) → None
    """

    def __init__(self, config: ConfigManager) -> None:
        self._config = config

    # ── Internal helpers ──────────────────────────────────────────────────

    def _exe(self) -> str:
        """Return gallery-dl path (or sentinel) or raise RuntimeError with install hint."""
        exe = _find_executable()
        if not exe:
            raise RuntimeError(t("err.gdl_not_installed"))
        return exe

    def _base_cmd(self, url: str = "") -> "tuple[list[str], str | None]":
        """Build base command with shared options (cookie, proxy, quiet).

        Returns (cmd, cookie_temp_path).  cookie_temp_path is the path of a
        decrypted plaintext temp file that the caller MUST delete after the
        subprocess exits.  It is None when no temp file was created (no cookie
        configured, or cookie was already a plaintext .txt file).

        Gallery-dl cannot parse Windows DPAPI-encrypted .enc cookie files
        directly — _prepare_cookie_for_use() decrypts them to a temp file
        first, mirroring the approach used by yt_dlp_engine.download().

        url is used for per-platform cookie resolution when provided.
        Callers that know the target URL (download, extract_info) should
        pass it so the correct platform cookie is selected automatically.
        Callers without a URL (e.g. version check) may omit it — the global
        cookie_file fallback is used in that case.
        """
        # -q suppresses info/progress output — gallery-dl does not have
        # --no-progress; that flag is yt-dlp only and causes an arg-parse error.
        cmd: list[str] = _exe_cmd(self._exe()) + ["-q"]
        cookie_temp: str | None = None

        # Per-platform cookie resolution — same logic as yt_dlp_engine.
        # Import inline to avoid circular imports at module level.
        from infrastructure.downloader.yt_dlp_engine import (  # noqa: PLC0415
            _prepare_cookie_for_use,
            _resolve_cookie,
            platform_for_url,
        )

        cookie_path = _resolve_cookie(url, self._config) if url else None
        # Fallback: if no URL or per-platform cookie not found, use global
        if not cookie_path:
            from infrastructure.downloader.yt_dlp_engine import _validate_cookie_path  # noqa: PLC0415

            cookie_path = _validate_cookie_path(self._config)
        if cookie_path:
            # Decrypt DPAPI-encrypted .enc files to a temp plaintext file so
            # gallery-dl can parse them as Netscape cookies.  gallery-dl cannot
            # read the binary .enc format directly.  When is_temp=True the
            # caller MUST delete the returned temp file after the subprocess
            # exits to avoid plaintext cookie files persisting on disk.
            usable, is_temp = _prepare_cookie_for_use(cookie_path)
            if is_temp:
                cookie_temp = usable
            cmd += ["--cookies", usable]
            logger.debug("gallery-dl using cookie file: %s", usable)
        elif self._config.use_cookies and self._config.cookies_browser:
            # Same precedence as yt_dlp_engine: cookie file wins when set,
            # browser cookies only as the fallback. Previously gallery-dl
            # never saw this setting at all, so users who chose "cookies
            # from browser" (no cookie file) got logged-out gallery-dl runs
            # for Instagram/Twitter/Facebook photo posts.
            cmd += ["--cookies-from-browser", self._config.cookies_browser]
            logger.debug("gallery-dl using cookies from browser: %s", self._config.cookies_browser)

        if self._config.proxy:
            cmd += ["--proxy", self._config.proxy]

        # BUG-IG-ANTIBOT: gallery-dl runs bare otherwise — stock UA, no
        # request pacing. Instagram image/gallery posts get a jittered
        # inter-request delay and a full Chrome header profile.
        # "-o browser=chrome" (not "--user-agent") because gallery-dl's own
        # browser profiles pair the UA with matching sec-ch-ua client hints
        # and TLS cipher order (extractor/common.py HEADERS/CIPHERS) —
        # overriding only the UA string left those other headers on
        # gallery-dl's Firefox default, disagreeing with a Chrome UA.
        if url and platform_for_url(url) == "instagram":
            cmd += ["--sleep-request", "6.0-12.0", "-o", "browser=chrome"]

        return cmd, cookie_temp

    # ── Metadata extraction ───────────────────────────────────────────────

    def extract_info(self, url: str) -> MediaInfo:
        """
        Fetch gallery metadata via gallery-dl --dump-json.
        Returns MediaInfo(source_engine="gallery_dl", formats=[], duration=0).
        Raises RuntimeError on failure.
        """
        base_cmd, cookie_temp = self._base_cmd(url=url)
        # BUG-FB-PCB: walk the candidate forms until one yields items.
        items: list[dict[str, Any]] = []
        result: subprocess.CompletedProcess[str] | None = None
        try:
            for _gdl_url in gallery_dl_url_candidates(url):
                cmd = base_cmd + ["--dump-json", "--no-download", _gdl_url]
                logger.debug("gallery-dl extract_info: %s", cmd)
                try:
                    result = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        # 30 s was enough for a single Instagram post but truncated
                        # --dump-json on large Facebook albums (one JSON line per photo).
                        timeout=90,
                        encoding="utf-8",
                        errors="replace",
                        creationflags=_WIN_NO_WINDOW,
                    )
                except subprocess.TimeoutExpired:
                    raise RuntimeError(t("err.gdl_timeout")) from None
                except FileNotFoundError:
                    raise RuntimeError(t("err.gdl_missing_binary")) from None
                items = _parse_dump_json(result.stdout)
                if items:
                    break
                logger.debug("gallery-dl extract_info: no items for %s", _gdl_url)
        finally:
            # Always clean up decrypted temp cookie file, even on error.
            if cookie_temp:
                try:
                    Path(cookie_temp).unlink(missing_ok=True)
                    logger.debug(
                        "gallery-dl extract_info: cleaned up temp cookie: %s",
                        cookie_temp,
                    )
                except Exception:
                    pass

        if not items:
            stderr = result.stderr.strip() if result else ""
            if (result is not None and result.returncode != 0) or stderr:
                raise RuntimeError(_friendly_error(stderr or "No items found"))
            raise RuntimeError(t("err.gdl_no_content"))

        first = items[0]
        count = len(items)

        # Extract uploader/username — field name varies by platform
        username = (
            first.get("uploader")
            or first.get("username")
            or (
                first.get("user", {}).get("username", "")
                if isinstance(first.get("user"), dict)
                else str(first.get("user", ""))
            )
            or ""
        )

        # Build a human-readable title
        from infrastructure.downloader.yt_dlp_engine import _detect_platform

        platform = _detect_platform(url)
        raw_title = first.get("title") or first.get("description", "")[:80].split("\n")[0] or ""
        if not raw_title:
            # Hard-coding "Instagram Photo" here mislabelled every single-item
            # Facebook / Twitter / Pinterest gallery.  Use the detected platform.
            raw_title = t("gdl.photo_count", count=count) if count > 1 else f"{platform} Photo"

        post_id = str(first.get("post_id") or first.get("shortcode") or first.get("id", "") or "")

        # Use first image URL as thumbnail preview
        thumbnail = str(first.get("url") or first.get("thumbnail") or "")

        logger.info(
            "gallery-dl extract_info: %d item(s) | platform=%s | id=%s",
            count,
            platform,
            post_id,
        )

        return MediaInfo(
            url=url,
            title=raw_title,
            uploader=str(username),
            duration=0,
            thumbnail=thumbnail,
            platform=platform,
            formats=[],
            is_live=False,
            was_live=False,
            video_id=post_id,
            source_engine="gallery_dl",
        )

    # ── Download execution ────────────────────────────────────────────────

    def download(
        self,
        task: DownloadTask,
        on_progress: Optional[Callable[[DownloadTask], None]] = None,
        on_postprocess: Optional[Callable[[DownloadTask], None]] = None,
    ) -> None:
        """
        Execute gallery-dl download for *task*.
        Updates task.status / progress / eta / filename in-place.
        Raises RuntimeError on failure.
        """
        output_dir = (Path(task.output_dir) if task.output_dir else self._config.download_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        # BUG-BT / Issue-3: per-post output isolation for Instagram posts/reels.
        # Without this, all posts from the same account share one directory, so
        # successive downloads accumulate in the same folder and Taildrop can't
        # distinguish which files belong to which post.
        # Folder name: {username}_{YYYYMMDD}_{shortcode[:8]}
        _insta_post_re = re.compile(r"instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)", re.I)
        _sc_m = _insta_post_re.search(task.url)
        # BUG-BX: track whether we isolated output to a per-post slug folder.
        # When True, the fallback scan can include ALL files in output_dir
        # (no mtime filter needed) because the folder is post-specific.
        _is_post_isolated = bool(_sc_m)
        if _sc_m:
            _shortcode = _sc_m.group(1)[:8]
            _upl = ""
            if task.media_info and task.media_info.uploader:
                _upl = re.sub(r"[^\w.]", "_", task.media_info.uploader)[:32].strip("_")
            _date_str = datetime.date.today().strftime("%Y%m%d")
            _slug = f"{_upl}_{_date_str}_{_shortcode}" if _upl else f"instagram_{_date_str}_{_shortcode}"
            output_dir = (output_dir / _slug).resolve()
            output_dir.mkdir(parents=True, exist_ok=True)

        # Same isolation for Facebook photo posts, albums and feed posts: without
        # it a 60-photo album empties straight into the download root, mixing with
        # every other download and leaving Taildrop no way to group the set.
        elif is_facebook_photo_url(task.url) or is_facebook_post_url(task.url):
            _fb_id = ""
            for _rx in (
                r"fbid=(\d+)",
                r"set=a\.(\d+)",
                r"set=([\w.]+)",
                r"/photos/[^/]*/(\d+)",
                r"story_fbid=(\d+)",
                r"/posts/(\w+)",
                r"/share/p/(\w+)",
            ):
                _fm = re.search(_rx, task.url, re.I)
                if _fm:
                    _fb_id = _fm.group(1)[:24]
                    break
            _upl = ""
            if task.media_info and task.media_info.uploader:
                _upl = re.sub(r"[^\w.]", "_", task.media_info.uploader)[:32].strip("_")
            _date_str = datetime.date.today().strftime("%Y%m%d")
            _slug = "_".join(x for x in (_upl or "facebook", _date_str, _fb_id) if x)
            output_dir = (output_dir / _slug).resolve()
            output_dir.mkdir(parents=True, exist_ok=True)
            _is_post_isolated = True

        # ── Instagram carousel: images-only strategy ─────────────────────
        # gallery-dl fetches Instagram video CDN URLs which are video-only
        # DASH streams (no audio).  For carousel posts (/p/ URLs), tell
        # gallery-dl to skip video files entirely — yt-dlp will download
        # them afterwards with proper bestvideo+bestaudio merge.
        _is_ig_carousel = bool(_sc_m) and bool(re.search(r"instagram\.com/p/", task.url, re.I))

        # BUG-FB-POST: same two-engine treatment for a Facebook feed post — see
        # _FB_POST_RE.  No --filter is needed here: gallery-dl's set extractor
        # never writes a video for these URLs, it just skips the video items.
        _is_fb_post = is_facebook_post_url(task.url)

        base_cmd, cookie_temp = self._base_cmd(url=task.url)
        _gdl_url = normalize_gallery_dl_url(task.url)
        # BUG-FB-PCB: hand gallery-dl every candidate form in one run — it walks
        # them in order and the duplicate of a form that already worked is
        # skipped, since both write "{id}.{extension}" into the same directory.
        _gdl_urls = gallery_dl_url_candidates(task.url)
        if _is_ig_carousel:
            cmd = base_cmd + [
                "--filter",
                "extension in ('jpg', 'jpeg', 'png', 'gif', 'webp', 'avif', 'heic')",
                "-d",
                str(output_dir),
                "--directory",
                ".",
                *_gdl_urls,
            ]
        else:
            cmd = base_cmd + [
                "-d",
                str(output_dir),
                "--directory",
                ".",
                *_gdl_urls,
            ]
        logger.info("gallery-dl download: %s → %s", _gdl_urls, output_dir)

        # BUG-BV: capture wall-clock time before the subprocess starts so the
        # fallback scan can scope results to files created in THIS session only.
        # We subtract a 5-second buffer to absorb filesystem timestamp rounding
        # and NAS/network-drive clock skew.
        _dl_start_ts: float = time.time() - 5.0

        # ── Subprocess ────────────────────────────────────────────────────
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=_WIN_NO_WINDOW,
            )
        except FileNotFoundError:
            # Clean up temp cookie before propagating so plaintext files
            # never linger on disk when the binary is missing.
            if cookie_temp:
                try:
                    Path(cookie_temp).unlink(missing_ok=True)
                except Exception:
                    pass
            raise RuntimeError(t("err.gdl_missing_binary")) from None

        task.status = DownloadStatus.DOWNLOADING
        task.progress = 0.0
        task.eta = t("progress.gdl_preparing")
        if on_progress:
            on_progress(task)

        # Drain stderr in a daemon thread to prevent pipe deadlock
        stderr_lines: deque[str] = deque(maxlen=200)

        def _drain_stderr() -> None:
            assert proc.stderr is not None
            for line in proc.stderr:
                line = line.rstrip()
                stderr_lines.append(line)
                if line:
                    logger.debug("gallery-dl: %s", line)

        stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
        stderr_thread.start()

        # ── Progress tracking via stdout ──────────────────────────────────
        # gallery-dl writes a line to stdout for each completed file.
        # The line is either the file path directly, or "#: /path/file.jpg".
        downloaded_files: list[str] = []
        last_output_dir: Optional[Path] = None

        assert proc.stdout is not None
        for raw_line in proc.stdout:
            if task.is_cancellation_requested:
                proc.kill()
                break

            line = raw_line.rstrip()
            if not line:
                continue

            # File completion line: starts with "#:" or is an absolute/relative path
            filepath_str = ""
            if line.startswith("#:"):
                filepath_str = line[2:].strip()
            elif not line.startswith("["):
                # Plain path — not a log message
                filepath_str = line.strip()

            if filepath_str:
                fp = Path(filepath_str)
                if fp.exists() and fp.is_file():
                    downloaded_files.append(str(fp))
                    last_output_dir = fp.parent
                    task.downloaded_bytes = sum(
                        Path(f).stat().st_size for f in downloaded_files if Path(f).exists()
                    )
                    n = len(downloaded_files)
                    task.eta = t("progress.gdl_downloaded", count=n)
                    task.speed = ""
                    # Indeterminate — show pulse at 50% while files arrive
                    task.progress = min(50.0 + n * 5, 95.0)
                    if on_progress:
                        on_progress(task)
                    logger.debug("gallery-dl downloaded: %s", fp.name)

        proc.wait()
        stderr_thread.join(timeout=5.0)

        # ── Cancel handling ───────────────────────────────────────────────
        if task.is_cancellation_requested:
            from yt_dlp.utils import DownloadError

            raise DownloadError("Cancelled by user")

        # ── Error handling ────────────────────────────────────────────────
        # For Instagram carousels with --filter, gallery-dl may exit non-zero
        # when all items are videos (filter excludes everything).  That is OK
        # — yt-dlp will handle the videos below.
        # BUG-FB-POST: for a Facebook feed post this is not yet a failure — the
        # post may hold a video and no photo set at all.  Hold the message and
        # raise it after the yt-dlp pass below only if that finds nothing either.
        _deferred_err = ""
        if proc.returncode != 0 and not downloaded_files and not _is_ig_carousel:
            err = "\n".join(ln for ln in stderr_lines if ln and not ln.startswith("[debug]"))
            _deferred_err = _friendly_error(err or "gallery-dl exit code non-zero")
            if not _is_fb_post:
                raise RuntimeError(_deferred_err)
            logger.info("gallery-dl found no photo set for %s — trying the yt-dlp video pass", _gdl_url)

        # Partial success (some files downloaded, process exited non-zero)
        if proc.returncode != 0 and downloaded_files:
            logger.warning(
                "gallery-dl exited with code %d but %d file(s) were downloaded — treating as partial success",
                proc.returncode,
                len(downloaded_files),
            )

        # ── Resolve output path ───────────────────────────────────────────
        # For multi-file downloads, point to the directory so "Open Folder" works.
        # Record the specific files downloaded in this task so TaildropService
        # can zip only them instead of the entire account directory.
        if downloaded_files:
            task.gallery_dl_files = downloaded_files[:]
            if len(downloaded_files) == 1:
                task.filename = downloaded_files[0]
            else:
                # Point to the deepest directory gallery-dl created
                task.filename = str(last_output_dir if last_output_dir else Path(downloaded_files[0]).parent)
        else:
            # BUG-BV FIX: gallery-dl with -q suppresses stdout so downloaded_files
            # is always empty.  Scan the output subtree for ALL media types (images
            # AND videos) written after the download started.  Scoping by mtime
            # prevents picking up files from previous downloads of the same account.
            # Set task.gallery_dl_files so TaildropService zips only this session's
            # files rather than the accumulated account directory.
            try:
                _media_exts = frozenset(
                    {
                        # images
                        ".jpg",
                        ".jpeg",
                        ".png",
                        ".gif",
                        ".webp",
                        ".avif",
                        # videos — BUG-BV: previously missing, causing carousel videos
                        # to be excluded from gallery_dl_files and Taildrop sends
                        ".mp4",
                        ".mov",
                        ".webm",
                        ".mkv",
                        ".m4v",
                    }
                )
                candidates = [
                    f
                    for f in output_dir.rglob("*")
                    if f.is_file()
                    and f.suffix.lower() in _media_exts
                    and f.stat().st_size > 1_000
                    # BUG-BX: skip mtime filter when output_dir is a per-post
                    # slug folder (Instagram /p/ URLs).  gallery-dl may set file
                    # mtime from post metadata (e.g. user has mtime:true in their
                    # gallery-dl config), causing images to have historical
                    # timestamps that fail the >= _dl_start_ts check.  Since the
                    # slug folder is post-specific, all files here belong to this
                    # post regardless of their mtime.  For non-isolated dirs
                    # (other platforms), keep the mtime filter to avoid including
                    # files from previous downloads of the same account directory.
                    and (_is_post_isolated or f.stat().st_mtime >= _dl_start_ts)
                ]
                if candidates:
                    # Separate images and videos for logging; both go into gallery_dl_files
                    _vid_exts = frozenset({".mp4", ".mov", ".webm", ".mkv", ".m4v"})
                    n_imgs = sum(1 for f in candidates if f.suffix.lower() not in _vid_exts)
                    n_vids = sum(1 for f in candidates if f.suffix.lower() in _vid_exts)
                    logger.info(
                        "gallery-dl fallback scan: found %d image(s) + %d video(s) "
                        "in output subtree (mtime ≥ session start)",
                        n_imgs,
                        n_vids,
                    )
                    task.gallery_dl_files = [str(f) for f in sorted(candidates)]
                    if len(candidates) == 1:
                        task.filename = str(candidates[0])
                    else:
                        # Point to the common parent directory
                        _parent = max(candidates, key=lambda f: f.stat().st_mtime).parent
                        task.filename = str(_parent)
                else:
                    logger.warning(
                        "gallery-dl fallback scan: no new media files found in %s (mtime ≥ session start)",
                        output_dir,
                    )
            except Exception as scan_err:
                logger.warning("gallery-dl output scan failed: %s", scan_err)
                task.filename = str(output_dir)

        _gdl_files_attr = getattr(task, "gallery_dl_files", None)
        _total_files = len(_gdl_files_attr) if _gdl_files_attr else len(downloaded_files)
        logger.info(
            "gallery-dl complete: %d file(s) → %s",
            _total_files,
            task.filename,
        )

        # ── Instagram carousel: yt-dlp primary video download ────────────────
        # gallery-dl fetches Instagram video CDN URLs as video-only DASH
        # streams (no audio).  The --filter above tells gallery-dl to skip
        # videos, so we download them here with yt-dlp which performs a
        # proper bestvideo+bestaudio merge via FFmpeg.
        #
        # BUG-IG-MIX: the rescue must run for EVERY /p/ carousel.  A previous
        # version skipped it when all gallery-dl files were images, reasoning
        # that such a post contains no video.  That check can never be false:
        # the --filter above forbids gallery-dl from writing a video file, so
        # a mixed photo+video post looks exactly like a photo-only post and
        # its videos were silently dropped.  A photo-only post costs one extra
        # yt-dlp extraction here (ignoreerrors=True → returns []).
        _current_gdl_files: list[str] = getattr(task, "gallery_dl_files", None) or []

        _vid_exts_set = frozenset({".mp4", ".mov", ".webm", ".mkv", ".m4v"})

        if (_is_ig_carousel or _is_fb_post) and not task.is_cancellation_requested:
            from utils.ffmpeg_locator import get_ffmpeg_path  # noqa: PLC0415

            _ffmpeg_dir = get_ffmpeg_path()

            # Delete any silent gallery-dl videos that slipped through the
            # filter (e.g. gallery-dl version without --filter support).
            _gdl_videos = [
                Path(f)
                for f in _current_gdl_files
                if Path(f).suffix.lower() in _vid_exts_set and Path(f).is_file()
            ]
            if _gdl_videos:
                for sv in _gdl_videos:
                    try:
                        sv.unlink(missing_ok=True)
                        logger.debug("Deleted gallery-dl silent video: %s", sv.name)
                    except Exception:
                        pass
                _gdl_video_paths = {str(v) for v in _gdl_videos}
                _current_gdl_files = [f for f in _current_gdl_files if f not in _gdl_video_paths]

            task.eta = t("progress.gdl_video_audio")
            if on_progress:
                on_progress(task)

            # Determine output directory for yt-dlp videos — same folder as
            # gallery-dl images so Taildrop zips everything together.
            # BUG-IG-MIX: a video-only post gives gallery-dl nothing to write,
            # so there is no image to borrow the folder from.  Fall back to the
            # per-post slug folder instead of None — None makes yt-dlp create a
            # nested "<uploader>/" subdir and splits the post across two dirs.
            _image_files = [Path(f) for f in _current_gdl_files if Path(f).is_file()]
            _video_out_dir: Path | None = (
                _image_files[0].parent if _image_files else (output_dir if _is_post_isolated else None)
            )

            # Prepare cookie — reuse the decrypted temp if still alive.
            _vid_cookie: str | None = None
            _vid_cookie_is_temp: bool = False
            if cookie_temp:
                _vid_cookie = cookie_temp
            else:
                try:
                    from infrastructure.downloader.yt_dlp_engine import (  # noqa: PLC0415,E501
                        _prepare_cookie_for_use,
                        _resolve_cookie,
                    )

                    _rcp = _resolve_cookie(task.url, self._config)
                    if _rcp:
                        _vid_cookie, _vid_cookie_is_temp = _prepare_cookie_for_use(_rcp)
                except Exception:
                    pass

            _vid_dl_start = time.time()
            try:
                video_files = _ytdlp_carousel_videos(
                    url=_gdl_url,
                    output_dir=output_dir,
                    dl_start_ts=_vid_dl_start,
                    cookie_file=_vid_cookie,
                    proxy=self._config.proxy or None,
                    ffmpeg_dir=_ffmpeg_dir,
                    max_retries=0,
                    rescue_dir=_video_out_dir,
                    owner_id=(facebook_owner_id(_gdl_url) or facebook_owner_id(task.url))
                    if _is_fb_post
                    else "",
                    # BUG-FB-ADVID: no image saved => gallery-dl proved the post
                    # holds no photo set, so only the requested story's own video
                    # may be rescued.
                    strict_story_id=(facebook_story_id(_gdl_url) or facebook_story_id(task.url))
                    if (_is_fb_post and not _image_files)
                    else "",
                )
            finally:
                if _vid_cookie_is_temp and _vid_cookie:
                    try:
                        Path(_vid_cookie).unlink(missing_ok=True)
                    except Exception:
                        pass

            if video_files:
                task.gallery_dl_files = _current_gdl_files + video_files
                if len(task.gallery_dl_files) > 1:
                    _parent2 = Path(task.gallery_dl_files[-1]).parent
                    task.filename = str(_parent2)
                elif task.gallery_dl_files:
                    task.filename = task.gallery_dl_files[0]
                logger.info(
                    "%s: %d image(s) + %d video(s) with audio",
                    "Facebook post" if _is_fb_post else "Instagram carousel",
                    len(_current_gdl_files),
                    len(video_files),
                )
            else:
                logger.info(
                    "%s: no video items rescued for %s — post is image-only, or the video download failed",
                    "Facebook post" if _is_fb_post else "Instagram carousel",
                    _gdl_url,
                )

        # BUG-FB-POST: neither engine produced a file — surface gallery-dl's
        # own message rather than reporting a silent success with nothing saved.
        if _deferred_err and not (getattr(task, "gallery_dl_files", None) or downloaded_files):
            if cookie_temp:
                try:
                    Path(cookie_temp).unlink(missing_ok=True)
                except Exception:
                    pass
            raise RuntimeError(_deferred_err)

        # Always clean up the decrypted temp cookie file after subprocess exits.
        # On early-exit paths (cancel / error raises above) the atexit handler
        # registered by cookie_storage.decrypt_to_tempfile() provides a safety net.
        if cookie_temp:
            try:
                Path(cookie_temp).unlink(missing_ok=True)
                logger.debug("gallery-dl download: cleaned up temp cookie: %s", cookie_temp)
            except Exception:
                pass
