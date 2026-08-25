"""
infrastructure/downloader/facebook_story_engine.py
===================================================
Facebook Story downloader — CDP via Playwright, isolated from the main pipeline.

Architecture
────────────
1. Browser launch  — `subprocess.Popen` (user's Brave/Chrome, `--remote-debugging-port`)
2. CDP connection  — `playwright.sync_api.connect_over_cdp()` (no `playwright install`)
3. Pre-page inject — `page.add_init_script(_PRE_PAGE_JS)` patches fetch/XHR
4. Navigate        — `page.goto(story_url, wait_until="domcontentloaded")`
5. Intercept       — 3 layers (see below), deadline-based
6. URL cleaning    — strip `bytestart`/`byteend`/`range` params → full video URL
7. Download        — requests stream (300s deadline) → ffmpeg fallback
8. Validate        — MP4 magic bytes + min 100 KB

Platform support
────────────────
Windows — Brave/Chrome from C:\\Program Files\\...
macOS   — Brave/Chrome from /Applications/...app/Contents/MacOS/...
          Note: App Store builds do NOT support --remote-debugging-port.
          Users must install from the vendor website (brave.com / google.com/chrome).
Linux   — Not supported (raises RuntimeError immediately).

Why `connect_over_cdp` (not `playwright launch`)
─────────────────────────────────────────────────
- Uses the user's existing browser → preserves Facebook login session (cookies)
- No `playwright install` → no extra ~150 MB browser binary
- Playwright handles WS stability (replaces ~230 lines of raw WS code)

Security
────────
- CDP port bound to `127.0.0.1` only; open ~45s during download
- CDN URL logged at 80 chars max (no user-identifiable data)
- Browser crash flag cleared after `proc.terminate()`

Dependencies
────────────
- playwright >= 1.40 (pip install playwright — no playwright install needed)
"""

from __future__ import annotations

import logging
import re
import socket as _socket
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from infrastructure.config.config_manager import ConfigManager

logger = logging.getLogger(__name__)

_WIN_NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# ── Constants ─────────────────────────────────────────────────────────────────

_FB_VIDEO_RE = re.compile(
    r"(?:"
    r"/m1/v/t\d+"  # NEW (2024+) DASH:   /m1/v/t6/HASH
    r"|/o1/v/"  # newer DASH:          /o1/v/HASH
    r"|/v/t(?:42|64|66)"  # legacy video types
    r"|bytestart="  # any byte-range segment URL
    r")",
    re.I,
)

_FB_THUMB_RE = re.compile(r"/v/t(?:15|39|51)\b", re.I)

# Facebook audio-DASH CDN tracks use /o1/a/ or /m1/a/ in the path
# (vs /o1/v/ and /m1/v/ for video).  Both prefixes are matched here so that
# newer DASH delivery paths (introduced alongside /m1/v/ in 2024+) are also
# captured.  The pattern is intentionally narrow (anchored to known FB CDN
# prefixes) to avoid false-positives from other fbcdn.net asset URLs.
_FB_AUDIO_RE = re.compile(r"/(?:o1|m1)/a/", re.I)

# Facebook DASH CDN paths embed a manifest-session number in the form
# /f2/m{NUMBER}/ (e.g. /f2/m367/).  The video and audio tracks belonging
# to the SAME story always share the same manifest number — we use this to
# reject audio CDN URLs that belong to a different story (e.g. the next
# story that Facebook auto-advances to while we are still polling).
# Observed formats: /f2/m{N}/ and occasionally /f1/m{N}/.
_MANIFEST_NUM_RE = re.compile(r"/f[12]/m(\d+)/", re.I)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Pre-page script — runs BEFORE Facebook JS loads (via add_init_script).
# Captures video CDN URLs into window.__omni_urls[0] and audio CDN URLs
# into window.__omni_urls[1] so the Python poll can retrieve each stream.
#
# Also patches MediaSource.addSourceBuffer so Facebook's JS DASH player
# registers audio mime types before any user gesture — this is needed because
# with a "tap to view" overlay the browser never autoplay-fires until clicked,
# but MediaSource is initialised during page load regardless of play state.
_PRE_PAGE_JS = (
    "(function(){"
    "if(window.__omni_installed)return;"
    "window.__omni_installed=true;"
    "window.__omni_video_url=null;"
    "window.__omni_audio_url=null;"
    # Keep legacy array for backward compat with _POLL_JS
    "window.__omni_urls=[];"
    "function _isVideo(l){"
    " return l.indexOf('/m1/v/t')!==-1||l.indexOf('/o1/v/')!==-1"
    "  ||l.indexOf('/v/t42')!==-1||l.indexOf('/v/t64')!==-1"
    "  ||l.indexOf('/v/t66')!==-1||l.indexOf('bytestart=')!==-1;"
    "}"
    "function _isAudio(l){return l.indexOf('/o1/a/')!==-1||l.indexOf('/m1/a/')!==-1;}"
    "function _cap(u){"
    "  if(!u||typeof u!=='string'||u.indexOf('fbcdn.net')===-1)return;"
    "  var l=u.toLowerCase();"
    "  if(_isVideo(l)&&!window.__omni_video_url){"
    "   window.__omni_video_url=u;"
    "   window.__omni_urls[0]=u;"
    "  }else if(_isAudio(l)&&!window.__omni_audio_url){"
    "   window.__omni_audio_url=u;"
    "   window.__omni_urls[1]=u;"
    "  }"
    "}"
    # Patch fetch
    "var _f=window.fetch;"
    "window.fetch=function(i,o){_cap(typeof i==='string'?i:(i&&i.url));return _f.apply(this,arguments);};"
    # Patch XHR
    "var _x=XMLHttpRequest.prototype.open;"
    "XMLHttpRequest.prototype.open=function(m,u){_cap(u);return _x.apply(this,arguments);};"
    # Patch MediaSource.addSourceBuffer — called by Facebook DASH player when
    # attaching audio track; the first appendBuffer call on an audio SourceBuffer
    # will contain the audio CDN URL in the associated fetch, but we can also
    # detect the audio mime type registration here to know audio exists.
    # Store the SourceBuffer's mime type so _POLL_AUDIO_JS can check it.
    "try{"
    " var _msASB=MediaSource.prototype.addSourceBuffer;"
    " MediaSource.prototype.addSourceBuffer=function(mime){"
    "  var sb=_msASB.apply(this,arguments);"
    "  if(mime&&(mime.indexOf('audio')!==-1||mime.indexOf('mp4a')!==-1)){"
    "   window.__omni_audio_mime=mime;"
    "  }"
    "  return sb;"
    " };"
    "}catch(e){}"
    "})()"
)

# JS to poll the page for an already-resolved video URL
_POLL_JS = (
    "(function(){"
    # 1. Pre-page interceptor result (fetch/XHR patch)
    "if(window.__omni_urls&&window.__omni_urls.length>0)return window.__omni_urls[0];"
    # 2. Performance API (works even for Service Worker cached loads)
    "try{"
    " var e=performance.getEntriesByType('resource');"
    " for(var i=0;i<e.length;i++){"
    "  var u=e[i].name;"
    "  if(!u||u.indexOf('fbcdn.net')===-1)continue;"
    "  var l=u.toLowerCase();"
    "  if(l.indexOf('/m1/v/t')!==-1||l.indexOf('/o1/v/')!==-1"
    "   ||l.indexOf('/v/t42')!==-1||l.indexOf('/v/t64')!==-1"
    "   ||l.indexOf('/v/t66')!==-1||l.indexOf('bytestart=')!==-1)"
    "   return u;"
    " }"
    "}catch(e){}"
    # 3. video.currentSrc
    "var vs=document.querySelectorAll('video');"
    "for(var j=0;j<vs.length;j++){"
    " var src=vs[j].currentSrc||vs[j].src||'';"
    " if(src&&src.indexOf('fbcdn.net')!==-1)return src;"
    "}"
    "return vs.length>0?'VIDEO_FOUND_NO_SRC':'NO_VIDEO';"
    "})()"
)

# JS to read the separately-captured audio CDN URL (from _PRE_PAGE_JS).
# Returns empty string if not yet captured.
#
# Three-strategy search:
#   1. window.__omni_audio_url — set by _PRE_PAGE_JS fetch/XHR patch
#   2. Performance API         — catches audio if fetched on the main thread
#   3. Inline <script> scan   — Facebook embeds the complete stream manifest
#                                (including audio CDN URLs with correct oh= tokens)
#                                as JSON inside <script> tags in the initial page HTML.
#                                This is the only JS-accessible vector that works when
#                                a Service Worker intercepts CDN requests before they
#                                reach page-level Playwright hooks (Layers A/B/D).
_POLL_AUDIO_JS = (
    "(function(){"
    # Strategy 1: pre-page interceptor
    "if(window.__omni_audio_url)return window.__omni_audio_url;"
    # Strategy 2: Performance API
    "try{"
    " var e=performance.getEntriesByType('resource');"
    " for(var i=0;i<e.length;i++){"
    "  var u=e[i].name;"
    "  if(!u||u.indexOf('fbcdn.net')===-1)continue;"
    "  var l=u.toLowerCase();"
    "  if(l.indexOf('/o1/a/')!==-1||l.indexOf('/m1/a/')!==-1)return u;"
    " }"
    "}catch(x){}"
    # Strategy 3: scan inline <script> content for audio CDN URLs.
    # Facebook puts GraphQL/Relay response data (including stream manifests)
    # in large inline <script> tags on the initial page load.
    # The audio CDN URL may appear in three encodings:
    #   A. Plain:          https://...fbcdn.net/o1/a/...
    #   B. JSON-escaped:   https:\\/\\/...fbcdn.net\\/o1\\/a\\/...
    #   C. Unicode-escape: https:\\u002F\\u002F...fbcdn.net...\\u002Fo1\\u002Fa\\u002F...
    "try{"
    " var ss=document.querySelectorAll('script');"
    " for(var j=0;j<ss.length;j++){"
    "  var t=ss[j].textContent;"
    "  if(!t||t.length<200||t.indexOf('fbcdn.net')===-1)continue;"
    "  var mA=t.match(/https?:\\/\\/\\S{5,}fbcdn\\.net\\S{5,}\\/(?:o1|m1)\\/a\\/\\S{20,}/);"
    "  if(mA){var rA=mA[0].split(/[\"'\\\\<>\\s]/)[0];"
    "if(rA.indexOf('fbcdn.net')!==-1&&rA.indexOf('/a/')!==-1)return rA;}"
    "  var mB=t.match(/https?:\\\\\\/\\\\\\/\\S{5,}fbcdn\\.net"
    "\\S{5,}\\\\\\/(?:o1|m1)\\\\\\/a\\\\\\/\\S{20,}/);"
    "  if(mB){var rB=mB[0].replace(/\\\\\\/\\//g,'/').split(/[\"'<>\\s]/)[0];"
    "if(rB.indexOf('fbcdn.net')!==-1)return rB;}"
    "  var mC=t.match(/https?:\\\\u002F\\\\u002F\\S{5,}fbcdn\\.net"
    "\\S{5,}\\\\u002F(?:o1|m1)\\\\u002Fa\\\\u002F\\S{20,}/i);"
    "  if(mC){var rC=mC[0].replace(/\\\\u002F/gi,'/')."
    "replace(/\\\\u0026/gi,'&').split(/[\"'<>\\s]/)[0];"
    "if(rC.indexOf('fbcdn.net')!==-1)return rC;}"
    " }"
    "}catch(ex){}"
    # Strategy 4: extract audio CDN URL from DASH manifest XML embedded in the
    # Relay/GraphQL store inside inline <script> tags.  Facebook serialises the
    # manifest as a JSON-encoded string under one of several keys depending on
    # surface/version — "dash_manifest", "dash_manifest_xml_string",
    # "manifest_xml", or "playlist"; its <BaseURL> elements carry
    # correctly-tokenised audio CDN URLs — unlike the /o1/v/→/o1/a/ derivation
    # which reuses the video oh= token (403 always).
    "try{"
    " var ss4=document.querySelectorAll('script');"
    " for(var q4=0;q4<ss4.length;q4++){"
    "  var t4=ss4[q4].textContent;"
    "  if(!t4||(t4.indexOf('dash_manifest')===-1&&t4.indexOf('manifest_xml')===-1"
    "&&t4.indexOf('playlist')===-1))continue;"
    '  var dm=t4.match(/"(?:dash_manifest|dash_manifest_xml_string|manifest_xml|playlist)"'
    '\\s*:\\s*"((?:[^"\\\\]|\\\\.)*)"/m);'
    "  if(!dm)continue;"
    "  var xml=dm[1].replace(/\\\\\\//g,'/').replace(/\\\\u0026/gi,'&');"
    "  var pts=xml.split('<BaseURL>');"
    "  for(var pp=1;pp<pts.length;pp++){"
    "   var ei=pts[pp].indexOf('</BaseURL>');"
    "   if(ei===-1)continue;"
    "   var bu=pts[pp].substring(0,ei);"
    "   var bl=bu.toLowerCase();"
    "   if((bl.indexOf('/o1/a/')!==-1||bl.indexOf('/m1/a/')!==-1)&&bu.indexOf('fbcdn.net')!==-1)"
    "    return bu;"
    "  }"
    " }"
    "}catch(ex4){}"
    "return '';"
    "})()"
)

# JS to extract a progressive (muxed video+audio) MP4 URL from Facebook's
# inline page data.  Facebook serves ready-to-play progressive MP4 URLs
# (playable_url_quality_hd, browser_native_hd_url, browser_native_sd_url,
# playable_url, progressive_url) in the same Relay/GraphQL <script> blobs
# scanned by _POLL_AUDIO_JS Strategy 3/4.  A progressive URL already carries
# both audio and video — no muxing or DASH audio capture needed, making it
# the most robust source of a story WITH sound.
# Priority: first HD match (playable_url_quality_hd, browser_native_hd_url),
# else first SD match (browser_native_sd_url, playable_url, progressive_url).
_POLL_PROGRESSIVE_JS = (
    "(function(){"
    "function dec(s){return s.replace(/\\\\u002F/gi,'/')."
    "replace(/\\\\\\//g,'/').replace(/\\\\u0026/gi,'&');}"
    "try{"
    " var ssP=document.querySelectorAll('script');"
    " var hd=null,sd=null;"
    " for(var j=0;j<ssP.length;j++){"
    "  var t=ssP[j].textContent;"
    "  if(!t||t.length<50)continue;"
    "  if(!hd){"
    '   var m1=t.match(/"playable_url_quality_hd"\\s*:\\s*"((?:[^"\\\\]|\\\\.)*)"/);'
    "   if(m1){hd=m1[1];}else{"
    '    var m2=t.match(/"browser_native_hd_url"\\s*:\\s*"((?:[^"\\\\]|\\\\.)*)"/);'
    "    if(m2)hd=m2[1];"
    "   }"
    "  }"
    "  if(!sd){"
    '   var m3=t.match(/"browser_native_sd_url"\\s*:\\s*"((?:[^"\\\\]|\\\\.)*)"/);'
    "   if(m3){sd=m3[1];}else{"
    '    var m4=t.match(/"playable_url"\\s*:\\s*"((?:[^"\\\\]|\\\\.)*)"/);'
    "    if(m4){sd=m4[1];}else{"
    '     var m5=t.match(/"progressive_url"\\s*:\\s*"((?:[^"\\\\]|\\\\.)*)"/);'
    "     if(m5)sd=m5[1];"
    "    }"
    "   }"
    "  }"
    "  if(hd)break;"
    " }"
    " if(hd)return dec(hd);"
    " if(sd)return dec(sd);"
    "}catch(exP){}"
    "return '';"
    "})()"
)

# JS to dismiss Facebook's "tap to view" overlay and trigger audio DASH requests.
#
# Problem on low-RAM systems (4 GB) and Win 11 LTSC:
#   Facebook Stories show a "Nhấp để xem tin" (tap to view) overlay that covers
#   the video player.  This overlay is a <div> or <a> element positioned above
#   the video.  Until it is dismissed, the video element stays paused, MSE
#   SourceBuffers are not fed, and no audio CDN requests are made.
#   The overlay exists because Brave opened a new tab (not user-navigated) so
#   Facebook treats it as a background/auto-open context requiring explicit tap.
#
# Fix:
#   1. Find and click the overlay element (by aria-label, data-testid, or
#      positional heuristic — whichever exists).
#   2. Dispatch synthetic MouseEvent click on the video itself.
#   3. Unmute + play() with autoplay-policy bypass.
#
# The overlay selector list covers known Facebook Story overlay patterns
# across both Vietnamese ("Nhấp để xem tin") and other locales.
_PLAY_JS = (
    "(function(){"
    # Step 1: dismiss "tap to view" overlay
    "var overlaySelectors=["
    # Known Facebook tap-to-view overlay selectors
    " '[data-testid=\"story-viewer-pause-overlay\"]',"
    " '[aria-label=\"Nhấp để xem tin\"]',"
    " '[aria-label=\"Tap to view\"]',"
    " '[aria-label=\"Click to view\"]',"
    " '.x1i10hfl[role=\"button\"]',"
    " 'a[role=\"presentation\"]',"
    "];"
    "for(var s=0;s<overlaySelectors.length;s++){"
    " var ov=document.querySelector(overlaySelectors[s]);"
    " if(ov){try{"
    "  var oe={bubbles:true,cancelable:true,view:window};"
    "  ov.dispatchEvent(new MouseEvent('mousedown',oe));"
    "  ov.dispatchEvent(new MouseEvent('mouseup',oe));"
    "  ov.dispatchEvent(new MouseEvent('click',oe));"
    " }catch(e){} break;}"
    "}"
    # Step 2: click video elements with synthetic gesture (satisfies autoplay policy)
    "var vs=document.querySelectorAll('video');"
    "for(var i=0;i<vs.length;i++){"
    " (function(v){"
    "  try{"
    "   var opts={bubbles:true,cancelable:true,view:window};"
    "   v.dispatchEvent(new MouseEvent('mousedown',opts));"
    "   v.dispatchEvent(new MouseEvent('mouseup',opts));"
    "   v.dispatchEvent(new MouseEvent('click',opts));"
    "  }catch(e){}"
    # Step 3: unmute then play (gesture above should have satisfied autoplay policy)
    "  v.muted=false;"
    "  var p=v.paused?v.play():Promise.resolve();"
    "  if(p&&p.then){"
    "   p.catch(function(){"
    # Fallback: mute→play→unmute
    "    v.muted=true;"
    "    var p2=v.play();"
    "    if(p2&&p2.then){p2.then(function(){"
    "setTimeout(function(){v.muted=false;},150);}).catch(function(){});}"
    "   });"
    "  }"
    " })(vs[i]);"
    "}"
    "})()"
)


# ── Public helpers ─────────────────────────────────────────────────────────────


def is_facebook_story_url(url: str) -> bool:
    u = url.lower()
    return "facebook.com/stories/" in u or "fb.watch" in u


def is_facebook_story_permalink(url: str) -> bool:
    """True only for a real Story permalink, never a bare fb.watch short link.

    fb.watch is Facebook's generic short-link domain: it fronts ordinary videos
    and reels far more often than Stories, and yt-dlp downloads those natively
    (see yt_dlp_engine._COOKIE_PLATFORM_MAP).  Callers that must refuse a URL
    outright -- rather than merely try the CDP engine first -- have to use this
    narrower check so an ordinary fb.watch video is not rejected.
    """
    return "facebook.com/stories/" in url.lower()


# ── URL utilities ──────────────────────────────────────────────────────────────


def _normalize_url(url: str) -> str:
    """Ensure view_single=1 so Facebook opens the single-story viewer."""
    p = urllib.parse.urlparse(url)
    q = urllib.parse.parse_qs(p.query, keep_blank_values=True)
    q["view_single"] = ["1"]
    return urllib.parse.urlunparse(p._replace(query=urllib.parse.urlencode({k: v[0] for k, v in q.items()})))


def _is_fb_video_url(url: str) -> bool:
    if "fbcdn.net" not in url:
        return False
    if _FB_THUMB_RE.search(url):
        return False
    return bool(_FB_VIDEO_RE.search(url))


def _is_fb_audio_url(url: str) -> bool:
    """Return True if *url* looks like a Facebook audio-DASH CDN track."""
    if "fbcdn.net" not in url:
        return False
    return bool(_FB_AUDIO_RE.search(url))


def _audio_matches_video(audio_url: str, video_url: Optional[str]) -> bool:
    """Return True if *audio_url* belongs to the same story as *video_url*.

    Facebook DASH CDN paths embed a manifest-session number (/f2/m{N}/) that
    is identical for the video and audio tracks of the same story.  We use
    this as a cross-story guard: when the poll window is extended to wait
    longer for audio on slow machines, Facebook may auto-advance to the next
    story and begin fetching ITS audio CDN URLs.  Without this check those
    wrong-story audio URLs would be silently captured and muxed with the
    target story's video — producing a file with mismatched audio.

    Fail-open (returns True) when either URL does not contain a recognisable
    manifest number, so that unusual future CDN path formats are not dropped.
    """
    if not video_url:
        return True  # No reference — cannot filter; accept
    mv = _MANIFEST_NUM_RE.search(video_url)
    ma = _MANIFEST_NUM_RE.search(audio_url)
    if mv and ma:
        if mv.group(1) != ma.group(1):
            logger.debug(
                "_audio_matches_video: rejected m%s (video is m%s) — wrong story",
                ma.group(1),
                mv.group(1),
            )
            return False
    return True  # fail-open: one or both URLs lack manifest number — accept


def _full_video_url(cdn_url: str) -> str:
    """Strip bytestart/byteend params that restrict response to one DASH segment."""
    p = urllib.parse.urlparse(cdn_url)
    q = urllib.parse.parse_qs(p.query, keep_blank_values=True)
    q.pop("bytestart", None)
    q.pop("byteend", None)
    q.pop("range", None)
    return urllib.parse.urlunparse(p._replace(query=urllib.parse.urlencode({k: v[0] for k, v in q.items()})))


def _derive_audio_url(video_url: str) -> Optional[str]:
    """Derive the Facebook audio-DASH CDN URL from a video-DASH CDN URL.

    Facebook's DASH delivery uses a symmetric CDN path structure: the video
    track lives under /o1/v/ or /m1/v/, and the corresponding audio track
    lives under /o1/a/ or /m1/a/ with identical path segments and query
    parameters.  This deterministic substitution lets us locate the audio URL
    without waiting for the browser to request it — which is unreliable
    because Facebook Stories use MSE (Media Source Extensions) and audio
    segment fetching is driven by JS SourceBuffer logic, not HTMLMediaElement,
    so play()/muted tricks cannot reliably trigger audio CDN requests.

    Returns the derived candidate URL, or None if the video URL does not
    contain a known substitutable path segment.

    IMPORTANT: The caller MUST validate the derived URL with a HEAD request
    before using it — not all stories have a separate audio track, and if
    the substituted path does not exist the CDN returns 404.  Treat a None
    or failed-HEAD result as "video-only story" and skip muxing.
    """
    for v_seg, a_seg in (("/o1/v/", "/o1/a/"), ("/m1/v/", "/m1/a/")):
        if v_seg in video_url:
            return video_url.replace(v_seg, a_seg, 1)
    return None


def _probe_audio_url(candidate: str) -> Optional[str]:
    """Verify a derived audio URL actually serves audio data.

    Facebook CDN returns 403 with a 12-byte body when a HEAD request is made
    with a video stream's `oh` token against the audio URL (token mismatch).
    Using GET with Range: bytes=0-8191 fetches the audio init segment directly;
    a real audio track returns ≥8 bytes, a missing track returns 403 + ≤64 bytes.
    """
    import requests as _req

    headers = {
        "User-Agent": _UA,
        "Referer": "https://www.facebook.com/",
        "Range": "bytes=0-8191",
    }
    try:
        resp = _req.get(
            candidate,
            headers=headers,
            timeout=8,
            allow_redirects=True,
            stream=True,
        )
        chunk = b""
        for c in resp.iter_content(chunk_size=64):
            chunk += c
            if len(chunk) >= 64:
                break
        resp.close()
        cl_hdr = int(resp.headers.get("content-length", 0))

        if resp.status_code in (403, 404) and (cl_hdr <= 64 or len(chunk) <= 12):
            logger.debug(
                "_probe_audio_url: rejected (status=%d, cl=%d) — token mismatch or no track",
                resp.status_code,
                cl_hdr,
            )
            return None
        if len(chunk) >= 8:
            logger.debug(
                "_probe_audio_url: confirmed audio track (status=%d, %d bytes) at %s…",
                resp.status_code,
                len(chunk),
                candidate[:80],
            )
            return candidate
        logger.debug(
            "_probe_audio_url: empty/tiny response (status=%d, %d bytes) — video-only",
            resp.status_code,
            len(chunk),
        )
    except Exception as exc:
        logger.debug("_probe_audio_url: request failed (%s) — assuming video-only", exc)
    return None


def _validate_mp4(path: Path) -> bool:
    """Return True if file is >= 100 KB and has valid MP4 container magic."""
    try:
        if path.stat().st_size < 100_000:
            return False
        hdr = path.read_bytes()[:12]
        return hdr[4:8] in (b"ftyp", b"mdat", b"moov", b"wide", b"free")
    except Exception:
        return False


def _clear_crashed_flag(profile_dir: Path) -> None:
    """Set exit_type=Normal in Brave Preferences so it won't show Restore dialog.

    Called BEFORE launch (prevent dialog on fresh start) AND
    AFTER terminate (undo the Crashed state our kill creates).

    Uses JSON parse → modify → atomic write-back instead of regex so that:
    - Malformed or future-format Preferences files are handled safely.
    - No risk of producing invalid JSON from unescaped values.
    - Atomic write (temp file + rename) prevents corrupt Preferences on crash.
    """
    import json
    import tempfile

    for slot in ["Default", "Profile 1", "Profile 2"]:
        prefs = profile_dir / slot / "Preferences"
        if not prefs.exists():
            continue
        try:
            raw = prefs.read_text(encoding="utf-8", errors="replace")
            data = json.loads(raw)

            # Patch exit_type under the "profile" or "browser" key (Chromium layout)
            # Also patch at the top level to handle flat Preferences files.
            modified = False
            nested = [data.get(k) for k in ("profile", "browser") if isinstance(data.get(k), dict)]
            for section in [data] + nested:
                if not isinstance(section, dict):
                    continue
                if section.get("exit_type") not in (None, "Normal"):
                    section["exit_type"] = "Normal"
                    modified = True
                if section.get("crashed") is True:
                    section["crashed"] = False
                    modified = True
                if section.get("session_crash_detected") is True:
                    section["session_crash_detected"] = False
                    modified = True

            if not modified:
                continue  # nothing to change — skip write

            # Atomic write: write to temp file in same dir, then rename
            tmp_fd, tmp_str = tempfile.mkstemp(dir=prefs.parent, suffix=".tmp", prefix="omnidl_prefs_")
            try:
                import os as _os

                _os.write(tmp_fd, json.dumps(data, separators=(",", ": ")).encode("utf-8"))
            finally:
                _os.close(tmp_fd)
            Path(tmp_str).replace(prefs)
            logger.debug("Cleared crashed flag in %s/%s/Preferences", profile_dir.name, slot)
        except (json.JSONDecodeError, OSError, KeyError):
            # Preferences corrupt or unreadable — skip silently (same as before)
            pass
        except Exception:
            pass


# ── Browser launch helpers ─────────────────────────────────────────────────────


def _find_browser_exe(browser: str) -> str:
    """Return path to Brave or Chrome executable, or raise RuntimeError.

    Supports Windows and macOS.  The .app bundle path on macOS must point to
    the actual Mach-O binary inside Contents/MacOS/ — Playwright needs a
    process it can launch directly, not the .app bundle itself.

    Note: App Store builds of Chrome/Brave do NOT support
    --remote-debugging-port.  Users must install from the vendor website.
    """

    browser = browser.lower()

    candidates: list[str] = []

    if sys.platform == "win32":
        if browser == "brave":
            candidates = [
                r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
                r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
            ]
        else:
            candidates = [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            ]

    elif sys.platform == "darwin":
        home = Path.home()
        if browser == "brave":
            candidates = [
                "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
                str(home / "Applications/Brave Browser.app/Contents/MacOS/Brave Browser"),
            ]
        else:
            candidates = [
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                str(home / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            ]

    else:
        raise RuntimeError("Facebook Story chỉ hỗ trợ Windows và macOS.\nLinux chưa được hỗ trợ.")

    exe = next((p for p in candidates if Path(p).exists()), None)
    if not exe:
        raise RuntimeError(
            f"Không tìm thấy {browser.title()}.  Hãy cài đặt trình duyệt trước.\n"
            "Lưu ý: bản tải từ App Store không hỗ trợ CDP — cần bản từ website chính thức."
        )
    return exe


def _free_port() -> int:
    """Return a free local TCP port."""
    with _socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ── CDP intercept via Playwright ───────────────────────────────────────────────


def _cdp_intercept(
    story_url: str,
    browser: str,
    timeout: float,
    on_progress: Optional[Callable],
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Launch browser, navigate to story_url; return (video_cdn_url, audio_cdn_url, progressive_cdn_url).

    Two bugs fixed in this version:

    BUG 1 — Wrong story captured (networkidle causes story to advance):
        wait_for_load_state("networkidle") caused the browser to wait until
        Facebook SPA fully rendered and started playing the story.  For a 17-second
        story, networkidle was reached AFTER the story finished and Facebook
        auto-advanced to the next story — so Layer A captured the next story's
        video URL, not the target.
        Fix: Remove networkidle. Use wait_until="domcontentloaded" (original
        behavior). ERR_ABORTED is non-fatal — Layer A fires via page.on("request")
        which intercepts requests at the network layer, independent of page load
        state. Video URL is captured within 3-5 s of navigation start.

    BUG 2 — Audio URL never captured (MSE appendBuffer bypasses JS interception):
        page.on("request") and fetch/XHR patches in _PRE_PAGE_JS only intercept
        requests that go through the JS Fetch API or XMLHttpRequest.  Facebook's
        DASH player uses MediaSource.appendBuffer() which triggers browser-native
        HTTP requests that BYPASS the JS layer entirely.
        Fix: Use Playwright CDP session (ctx.new_cdp_session()) with
        Network.enable() to intercept ALL browser-level network requests,
        including native MediaSource segment fetches. This is the same domain
        used by Chrome DevTools Network panel.
    """
    try:
        from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright  # noqa: I001
    except ImportError as err:
        raise RuntimeError("Thiếu thư viện Playwright.\nChạy: pip install playwright") from err

    import os

    def _prog(pct: int, msg: str) -> None:
        if on_progress:
            try:
                on_progress(pct, "", msg)
            except Exception:
                pass

    exe = _find_browser_exe(browser)
    port = _free_port()

    if sys.platform == "win32":
        local_app = Path(os.environ.get("LOCALAPPDATA", ""))
        profile_base = local_app / (
            "BraveSoftware/Brave-Browser/User Data"
            if browser.lower() == "brave"
            else "Google/Chrome/User Data"
        )
    elif sys.platform == "darwin":
        home = Path.home()
        profile_base = home / (
            "Library/Application Support/BraveSoftware/Brave-Browser"
            if browser.lower() == "brave"
            else "Library/Application Support/Google/Chrome"
        )
    else:
        profile_base = Path()

    if profile_base.exists():
        _clear_crashed_flag(profile_base)

    cmd = [
        exe,
        f"--remote-debugging-port={port}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate",
        "--restore-last-session=false",
        "--no-session-crashed-bubble",
        "--hide-crash-restore-bubble",
        # ── FIX BUG-STORY-1: wrong story captured / no audio ─────────────
        # Root cause: Brave opens the tab programmatically (not by user click),
        # so the browser enforces autoplay policy → v.play() with audio throws
        # NotAllowedError.  _PLAY_JS falls back to muted playback, but when
        # muted, Facebook's DASH player skips audio segment fetches → audio CDN
        # URL is never captured by any layer (A/B/C/D/E) in 40 s of polling.
        # Meanwhile the overlay ("Nhấp để xem tin") prevents story-1 from
        # actually playing, so Facebook preloads stories 2/3 in the background;
        # those preload CDN requests are captured as "the video URL" instead.
        #
        # This single Chromium flag overrides the autoplay policy at the browser
        # level, allowing unmuted v.play() without a real user gesture.  With
        # unmuted playback:
        #   • Story-1 starts playing immediately on page load → its video CDN
        #     URL is captured first (before any background preload requests).
        #   • Facebook DASH player fetches audio segments → audio CDN URL is
        #     captured within the first 5-10 s of polling.
        # Cross-platform: supported by all Chromium-based browsers (Brave,
        # Chrome) on Windows and macOS; no effect on non-Chromium browsers.
        "--autoplay-policy=no-user-gesture-required",
        # Prevent Chrome/Brave from throttling JS timers and deferring network
        # requests in tabs that were opened programmatically.  Without these
        # flags, Brave may classify the CDP-opened tab as "background/occluded"
        # and apply aggressive resource throttling — delaying the Facebook DASH
        # JS and extending the time before audio CDN requests are made.
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
    ]
    _prog(8, f"Đang khởi động {browser.title()}...")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    logger.info("CDP: launching %s on port %d (pid=%d)", browser, port, proc.pid)

    video_url: Optional[str] = None
    audio_url: Optional[str] = None
    progressive_url: Optional[str] = None
    video_found_at: float = 0.0
    # Deduped set of unmatched fbcdn.net /o1/ or /m1/ URL prefixes seen by
    # Layer D — diagnostic only, lets future format changes be spotted from
    # user debug logs without needing a live repro.
    _unmatched_fbcdn_urls: set = set()
    # How long to wait for the audio CDN URL AFTER the video URL is captured.
    #
    # Root cause analysis (4 GB RAM / Win 11 LTSC):
    #   - Facebook preloads the video DASH init segment immediately on page
    #     load, so the video CDN URL is captured within 3-5 s of navigation.
    #   - However, the "Nhấp để xem tin" overlay blocks actual playback.
    #   - _PLAY_JS dismisses the overlay every 3 s, but on a slow 4 GB system
    #     the overlay render + JS dismiss + initial video buffering collectively
    #     take 15-20 s before audio DASH segments begin flowing.
    #   - History: 12 s → always missed; 20 s → still missed on this machine.
    #   - 40 s gives a comfortable margin: audio starts at ~22 s, captured at
    #     ~22-25 s, well inside the window.
    #   - On fast systems the loop exits immediately when audio_url is set,
    #     so the larger value has zero cost in the happy path.
    _AUDIO_WAIT_S: float = 40.0

    try:
        with sync_playwright() as pw:
            _prog(10, "Đang kết nối CDP...")
            cdp_browser = None
            deadline = time.monotonic() + 30.0
            last_exc = None

            while time.monotonic() < deadline:
                try:
                    cdp_browser = pw.chromium.connect_over_cdp(
                        f"http://127.0.0.1:{port}",
                        timeout=3_000,
                    )
                    break
                except Exception as exc:
                    last_exc = exc
                    time.sleep(0.8)

            if cdp_browser is None:
                raise RuntimeError(
                    "Không kết nối được CDP.\n\n"
                    "Đóng HOÀN TOÀN trình duyệt (kể cả System Tray) rồi thử lại.\n"
                    f"(chi tiết: {last_exc})"
                )

            logger.info("CDP: Playwright connected on port %d", port)

            ctx = cdp_browser.contexts[0]
            page = ctx.new_page()

            # ── Layer A: Playwright high-level request intercept ──────────────
            def _on_request(request) -> None:
                nonlocal video_url, audio_url, video_found_at
                u = request.url
                if not video_url and _is_fb_video_url(u):
                    logger.info("CDP[A]: video URL caught (%d chars)", len(u))
                    video_url = u
                    video_found_at = time.monotonic()
                elif not audio_url and _is_fb_audio_url(u) and _audio_matches_video(u, video_url):
                    logger.info("CDP[A]: audio URL caught (%d chars)", len(u))
                    audio_url = u

            page.on("request", _on_request)

            # ── Layer B: MIME-type match on responses ─────────────────────────
            def _on_response(response) -> None:
                nonlocal video_url, audio_url, video_found_at
                if "fbcdn.net" not in response.url:
                    return
                ct = response.headers.get("content-type", "").lower()
                if not video_url and ct.startswith("video/") and "mjpeg" not in ct:
                    logger.info("CDP[B]: video MIME=%s", ct)
                    video_url = response.url
                    video_found_at = time.monotonic()
                elif (
                    not audio_url
                    and ct.startswith("audio/")
                    and _audio_matches_video(response.url, video_url)
                ):
                    logger.info("CDP[B]: audio MIME=%s", ct)
                    audio_url = response.url

            page.on("response", _on_response)

            # ── Layer D: CDP Network domain — catches ALL browser requests ────
            # BUG 2 FIX: MediaSource.appendBuffer() triggers native browser HTTP
            # requests that bypass page.on("request") and the JS fetch patch.
            # CDP Network.requestWillBeSent fires for EVERY network request
            # regardless of how it was initiated — including native MediaSource
            # segment fetches that carry the audio CDN URL with its own oh= token.
            cdp_session = None
            try:
                cdp_session = ctx.new_cdp_session(page)
                cdp_session.send("Network.enable")

                def _on_cdp_request(params: dict) -> None:
                    nonlocal video_url, audio_url, video_found_at
                    u = params.get("request", {}).get("url", "")
                    if not u or "fbcdn.net" not in u:
                        return
                    if not video_url and _is_fb_video_url(u):
                        logger.info("CDP[D]: video URL via Network domain (%d chars)", len(u))
                        video_url = u
                        video_found_at = time.monotonic()
                    elif not audio_url and _is_fb_audio_url(u) and _audio_matches_video(u, video_url):
                        logger.info("CDP[D]: audio URL via Network domain (%d chars)", len(u))
                        audio_url = u
                    elif ("/o1/" in u or "/m1/" in u) and u[:100] not in _unmatched_fbcdn_urls:
                        _unmatched_fbcdn_urls.add(u[:100])
                        logger.debug("CDP[D]: unmatched fbcdn.net /o1//m1/ URL: %s", u[:100])

                # BUG 2 supplemental: catch audio by MIME type on the CDP Network
                # domain too, mirroring Layer B but at the browser-request level —
                # covers cases where the URL path pattern no longer matches
                # _is_fb_audio_url but the response is still genuinely audio/*.
                def _on_cdp_response(params: dict) -> None:
                    nonlocal audio_url
                    resp = params.get("response", {}) or {}
                    mime = resp.get("mimeType", "") or ""
                    u = resp.get("url", "")
                    if (
                        not audio_url
                        and mime.startswith("audio/")
                        and u
                        and _audio_matches_video(u, video_url)
                    ):
                        logger.info("CDP[D]: audio MIME=%s via Network.responseReceived", mime)
                        audio_url = u

                cdp_session.on("Network.requestWillBeSent", _on_cdp_request)
                cdp_session.on("Network.responseReceived", _on_cdp_response)
                logger.debug("CDP[D]: Network domain enabled")
            except Exception as exc:
                logger.debug("CDP[D]: Network domain unavailable (%s) — using A/B/C only", exc)
                cdp_session = None

            # ── Layer E: Context-level route — Service Worker coverage ────────
            # ROOT CAUSE OF MISSING AUDIO:
            #   Facebook.com is a PWA with an active Service Worker.  When the
            #   DASH player fetches audio CDN segments the flow is:
            #     page → SW (intercept) → CDN (SW's own fetch) → SW → page
            #   Layers A/B/D only see requests on the PAGE target.  The SW→CDN
            #   fetch happens on a SEPARATE SERVICE WORKER TARGET; those requests
            #   are invisible to CDP Network.requestWillBeSent on the page.
            #
            #   Playwright's context.route() is the only interception layer that
            #   covers Service Worker outbound requests (documented in Playwright
            #   ≥ 1.16).  By routing at the context level we capture the actual
            #   CDN request that the SW makes, which contains the audio URL with
            #   its correct one-hop auth token.
            #
            #   We route only *.fbcdn.net URLs to minimise overhead and call
            #   route.continue_() immediately so playback is unaffected.
            _fbcdn_route_re = re.compile(r"https?://[^/]+\.fbcdn\.net/")
            _route_installed = False

            def _handle_fbcdn_route(route) -> None:
                nonlocal video_url, audio_url, video_found_at
                try:
                    u = route.request.url
                    if "fbcdn.net" in u:
                        if not video_url and _is_fb_video_url(u):
                            logger.info("CDP[E]: video URL via context route (%d chars)", len(u))
                            video_url = u
                            video_found_at = time.monotonic()
                        elif not audio_url and _is_fb_audio_url(u) and _audio_matches_video(u, video_url):
                            logger.info("CDP[E]: audio URL via context route (%d chars)", len(u))
                            audio_url = u
                except Exception:
                    pass
                finally:
                    try:
                        route.continue_()
                    except Exception:
                        pass

            try:
                ctx.route(_fbcdn_route_re, _handle_fbcdn_route)
                _route_installed = True
                logger.debug(
                    "CDP[E]: context-level fbcdn.net route installed (covers Service Worker → CDN requests)"
                )
            except Exception as exc:
                logger.debug("CDP[E]: route install failed (%s) — proceeding without", exc)
            page.add_init_script(_PRE_PAGE_JS)

            # ── Navigate ──────────────────────────────────────────────────────
            # BUG 1 FIX: Use domcontentloaded (original), NOT networkidle.
            # networkidle waited until the 17s story finished and FB advanced
            # to the next story — causing the wrong story URL to be captured.
            story_url_norm = _normalize_url(story_url)
            _prog(12, "Đang mở Story trong trình duyệt...")
            logger.info("CDP: navigating to %s", story_url_norm[:100])

            try:
                page.goto(story_url_norm, wait_until="domcontentloaded", timeout=min(timeout, 20) * 1_000)
            except PWTimeout:
                pass
            except Exception as exc:
                logger.debug("page.goto warning (non-fatal): %s", exc)

            # ── BUG-STORY-3: Dismiss overlay immediately after DOMContentLoaded ─────
            # On 4 GB RAM / Win 11 LTSC, page.evaluate(_POLL_AUDIO_JS) blocks
            # for 2-5 s while Facebook's JS evaluates large inline <script> data.
            # During that blocking window, Facebook's DASH preloader fires CDN
            # requests for stories 2/3 in the background; those requests arrive
            # via Layer A/B/D/E and win the "first video URL" race against story-1.
            #
            # Fix: call _PLAY_JS BEFORE _POLL_AUDIO_JS so the "Nhấp để xem tin"
            # overlay is dismissed as early as possible — right at DOMContentLoaded,
            # before any background preload requests have a chance to fire.
            # This gives story-1 a head start: the DASH player starts story-1's
            # CDN requests before story-2/3 preloads are initiated.
            # _PLAY_JS is idempotent; calling it again in the poll loop (3-5 s
            # cadence) is harmless and keeps the overlay dismissed if Facebook
            # re-renders it after the initial click.
            try:
                page.evaluate(_PLAY_JS)
                logger.debug(
                    "CDP: _PLAY_JS fired immediately after DOMContentLoaded"
                    " (BUG-STORY-3 fix — dismisses overlay before audio scan)"
                )
            except Exception as _pjs_exc:
                logger.debug("CDP: pre-scan _PLAY_JS failed (non-fatal): %s", _pjs_exc)

            # ── Immediate post-load audio scan ────────────────────────────────
            # Run the full audio poll (incl. inline <script> scan) once right
            # after DOMContentLoaded.  This catches audio CDN URLs embedded in
            # Facebook's initial page data before playback even begins — the
            # audio URL is in the serialised GraphQL/Relay store in <script> tags.
            if not audio_url:
                try:
                    aval_early = page.evaluate(_POLL_AUDIO_JS)
                    if (
                        aval_early
                        and "fbcdn.net" in aval_early
                        and _audio_matches_video(aval_early, video_url)
                    ):
                        logger.info(
                            "CDP[C]: audio URL found in initial page data (%d chars)",
                            len(aval_early),
                        )
                        audio_url = str(aval_early)
                except Exception:
                    pass

            # ── Immediate post-load progressive-URL scan ────────────────────
            # Same rationale as the audio scan above: the progressive (muxed
            # video+audio) MP4 URL is also embedded in Facebook's initial page
            # data, so it can be found before playback begins.
            if not progressive_url:
                try:
                    pval_early = page.evaluate(_POLL_PROGRESSIVE_JS)
                    if pval_early and "fbcdn.net" in pval_early:
                        logger.info(
                            "CDP: progressive URL found in initial page data (%d chars)",
                            len(pval_early),
                        )
                        progressive_url = str(pval_early)
                except Exception:
                    pass
            _prog(15, "Đang chờ video load...")
            loop_deadline = time.monotonic() + timeout
            # Initialize last_play to now so the polling loop waits the full
            # cadence (5 s / 3 s) before calling _PLAY_JS again — we already
            # called it immediately above (BUG-STORY-3 fix).  Without this,
            # the first loop iteration would call _PLAY_JS a second time within
            # 0.4 s, which is harmless but wastes a page.evaluate() round-trip.
            last_play = time.monotonic()
            last_poll = 0.0

            while time.monotonic() < loop_deadline:
                now = time.monotonic()

                if video_url and audio_url:
                    break

                if video_url and video_found_at > 0:
                    if now - last_play >= 3.0:
                        try:
                            page.evaluate(_PLAY_JS)
                        except Exception:
                            pass
                        # FIX BUG-STORY-2: page.evaluate() can block for many
                        # seconds on low-RAM machines (4 GB / Win 11 LTSC).
                        # Recompute `now` after the blocking call so the
                        # `_AUDIO_WAIT_S` guard below uses the real elapsed
                        # time, not the stale value from the loop top.
                        # Without this refresh, the loop overruns _AUDIO_WAIT_S
                        # by 30-60 s on slow hardware (observed: 97 s actual
                        # wait despite _AUDIO_WAIT_S=40.0).
                        now = time.monotonic()
                        last_play = now
                    # A progressive (muxed) URL already guarantees sound, so
                    # there is no need to wait the full 40 s for a separate
                    # audio-DASH URL that historically never arrives — cut the
                    # wait to 10 s.  Without a progressive URL, keep the full
                    # budget since it's the only way audio can still show up.
                    audio_wait = 10.0 if progressive_url else _AUDIO_WAIT_S
                    if now - video_found_at > audio_wait:
                        logger.debug(
                            "CDP: video found, audio not captured in %.0fs (progressive=%s) — proceeding",
                            audio_wait,
                            bool(progressive_url),
                        )
                        break

                if not video_url and now - last_play > 5.0:
                    try:
                        page.evaluate(_PLAY_JS)
                    except Exception:
                        pass
                    last_play = now

                if now - last_poll > 1.0:
                    try:
                        if not video_url:
                            val = page.evaluate(_POLL_JS)
                            if val and val not in ("VIDEO_FOUND_NO_SRC", "NO_VIDEO", ""):
                                logger.info("CDP[C]: video via poll (%d chars)", len(val))
                                video_url = str(val)
                                video_found_at = time.monotonic()
                                try:
                                    page.evaluate(_PLAY_JS)
                                    last_play = time.monotonic()
                                except Exception:
                                    pass
                        if video_url and not audio_url:
                            aval = page.evaluate(_POLL_AUDIO_JS)
                            now = time.monotonic()  # refresh after blocking JS evaluate
                            if aval and "fbcdn.net" in aval and _audio_matches_video(aval, video_url):
                                logger.info("CDP[C]: audio via poll (%d chars)", len(aval))
                                audio_url = str(aval)
                            elif aval and "fbcdn.net" in aval:
                                logger.debug(
                                    "CDP[C]: audio poll URL ignored — manifest mismatch (wrong story)"
                                )
                        if not progressive_url:
                            pval = page.evaluate(_POLL_PROGRESSIVE_JS)
                            if pval and "fbcdn.net" in pval:
                                logger.info("CDP: progressive URL via poll (%d chars)", len(pval))
                                progressive_url = str(pval)
                    except Exception as exc:
                        logger.debug("poll error (non-fatal): %s", exc)
                    last_poll = time.monotonic()
                    audio_wait = 10.0 if progressive_url else _AUDIO_WAIT_S
                    if video_url and not audio_url and now - video_found_at > audio_wait:
                        logger.debug(
                            "CDP: audio budget exhausted post-poll (%.0fs, progressive=%s) — proceeding",
                            audio_wait,
                            bool(progressive_url),
                        )
                        break

                _prog(
                    min(45, 15 + int((timeout - (loop_deadline - now)) / timeout * 30)),
                    "Đang chờ video load...",
                )
                time.sleep(0.4)

            # ── Layer E cleanup ───────────────────────────────────────────────
            if _route_installed:
                try:
                    ctx.unroute(_fbcdn_route_re, _handle_fbcdn_route)
                except Exception:
                    pass

        if not video_url:
            logger.warning("CDP: no video URL found within %.0fs", timeout)
        elif not audio_url:
            logger.info(
                "CDP: no audio URL captured — %s",
                "will use progressive URL" if progressive_url else "story may be video-only",
            )

        return video_url, audio_url, progressive_url

    finally:
        try:
            proc.terminate()
            proc.wait(timeout=4)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            if profile_base.exists():
                _clear_crashed_flag(profile_base)
                logger.debug("Cleared browser crash flag after CDP session")
        except Exception:
            pass


def _download_cdn_url(
    cdn_url: str,
    dest: Path,
    on_progress: Optional[Callable],
) -> Optional[Path]:
    """Download cdn_url to dest via streaming HTTP GET.

    Strips byte-range params first (avoids DASH init-segment-only response).
    Does a HEAD check — skips if Content-Length < 50 KB.
    """
    import requests

    def _prog(pct: int, speed: str, msg: str) -> None:
        if on_progress:
            try:
                on_progress(pct, speed, msg)
            except Exception:
                pass

    full_url = _full_video_url(cdn_url)
    headers = {"User-Agent": _UA, "Referer": "https://www.facebook.com/"}

    # HEAD check
    try:
        head = requests.head(full_url, headers=headers, timeout=10, allow_redirects=True)
        cl = int(head.headers.get("content-length", 0))
        if 0 < cl < 50_000:
            logger.warning("HEAD: size=%d < 50 KB (DASH init segment) — skip", cl)
            return None
    except Exception as exc:
        logger.debug("HEAD failed (%s) — proceeding with GET", exc)

    _prog(50, "", "Đang tải video...")
    try:
        resp = requests.get(full_url, headers=headers, stream=True, timeout=60)
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("GET failed: %s", exc)
        return None

    total = int(resp.headers.get("content-length", 0))
    done = 0
    start = time.monotonic()
    # Total deadline: 5 min for large files, but never hang forever
    stream_deadline = start + 300.0
    dest.parent.mkdir(parents=True, exist_ok=True)

    with open(dest, "wb") as f:
        for chunk in resp.iter_content(chunk_size=256 * 1024):
            if time.monotonic() > stream_deadline:
                logger.warning("_download_cdn_url: stream deadline exceeded (300s)")
                dest.unlink(missing_ok=True)
                return None
            if chunk:
                f.write(chunk)
                done += len(chunk)
                elapsed = time.monotonic() - start
                speed = done / elapsed if elapsed > 0.1 else 0
                pct = min(95, 50 + int(done / total * 44)) if total else 70
                s_str = (
                    f"{speed / 1048576:.1f} MB/s"
                    if speed > 1_048_576
                    else f"{speed / 1024:.0f} KB/s"
                    if speed > 0
                    else ""
                )
                _prog(pct, s_str, f"Đang tải... {done // 1024} KB")

    if _validate_mp4(dest):
        return dest

    logger.warning("GET result not valid MP4 (%d bytes)", dest.stat().st_size if dest.exists() else 0)
    dest.unlink(missing_ok=True)
    return None


def _ffmpeg_download(
    cdn_url: str,
    dest: Path,
    on_progress: Optional[Callable],
) -> Optional[Path]:
    """Use ffmpeg to reassemble DASH segments into a single MP4."""
    from utils.ffmpeg_locator import locate_ffmpeg

    loc = locate_ffmpeg()
    if not loc:
        logger.warning("ffmpeg not available — skipping")
        return None

    full_url = _full_video_url(cdn_url)
    dest.parent.mkdir(parents=True, exist_ok=True)

    if on_progress:
        try:
            on_progress(60, "", "ffmpeg đang xử lý DASH stream...")
        except Exception:
            pass

    cmd = [
        loc.ffmpeg_bin,
        "-y",
        "-user_agent",
        _UA,
        "-referer",
        "https://www.facebook.com/",
        "-i",
        full_url,
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(dest),
    ]
    logger.debug("ffmpeg: %s ... %s", loc.ffmpeg_bin, full_url[:60])

    try:
        result = subprocess.run(cmd, capture_output=True, timeout=120, creationflags=_WIN_NO_WINDOW)
    except subprocess.TimeoutExpired:
        logger.warning("ffmpeg timeout")
        dest.unlink(missing_ok=True)
        return None
    except Exception as exc:
        logger.warning("ffmpeg error: %s", exc)
        dest.unlink(missing_ok=True)
        return None

    if result.returncode == 0 and _validate_mp4(dest):
        logger.info("ffmpeg OK: %s (%d bytes)", dest.name, dest.stat().st_size)
        return dest

    tail = result.stderr[-300:].decode("utf-8", errors="replace") if result.stderr else ""
    logger.warning("ffmpeg rc=%d: %s", result.returncode, tail)
    dest.unlink(missing_ok=True)
    return None


# ── Public entry point ─────────────────────────────────────────────────────────


def _ffmpeg_mux(
    video_url: str,
    audio_url: str,
    dest: Path,
    on_progress: Optional[Callable],
) -> Optional[Path]:
    """Mux separate video-DASH and audio-DASH streams into one MP4.

    Uses two -i inputs (video + audio) with stream-copy so there is no
    re-encode overhead.  Both inputs receive the FB CDN User-Agent and
    Referer headers so the server accepts the requests.

    Returns dest on success, None on failure (file is unlinked on failure).
    """
    from utils.ffmpeg_locator import locate_ffmpeg

    loc = locate_ffmpeg()
    if not loc:
        logger.warning("_ffmpeg_mux: ffmpeg not available")
        return None

    v_url = _full_video_url(video_url)
    a_url = _full_video_url(audio_url)
    dest.parent.mkdir(parents=True, exist_ok=True)

    if on_progress:
        try:
            on_progress(55, "", "FFmpeg đang ghép video + audio...")
        except Exception:
            pass

    cmd = [
        loc.ffmpeg_bin,
        "-y",
        "-user_agent",
        _UA,
        "-referer",
        "https://www.facebook.com/",
        "-i",
        v_url,
        "-user_agent",
        _UA,
        "-referer",
        "https://www.facebook.com/",
        "-i",
        a_url,
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(dest),
    ]
    logger.debug("ffmpeg mux: video=%.60s… audio=%.60s…", v_url, a_url)

    try:
        result = subprocess.run(cmd, capture_output=True, timeout=120, creationflags=_WIN_NO_WINDOW)
    except subprocess.TimeoutExpired:
        logger.warning("_ffmpeg_mux: timeout")
        dest.unlink(missing_ok=True)
        return None
    except Exception as exc:
        logger.warning("_ffmpeg_mux error: %s", exc)
        dest.unlink(missing_ok=True)
        return None

    if result.returncode == 0 and _validate_mp4(dest):
        logger.info("ffmpeg mux OK: %s (%d bytes)", dest.name, dest.stat().st_size)
        return dest

    tail = result.stderr[-300:].decode("utf-8", errors="replace") if result.stderr else ""
    logger.warning("ffmpeg mux rc=%d: %s", result.returncode, tail)
    dest.unlink(missing_ok=True)
    return None


def _has_audio_stream(ffmpeg_bin: str, path: Path) -> bool:
    """Return True if the MP4 file contains at least one audio stream."""
    ffprobe = str(Path(ffmpeg_bin).parent / "ffprobe")
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            timeout=10,
            creationflags=_WIN_NO_WINDOW,
        )
        return b"audio" in result.stdout
    except Exception:
        return True  # assume audio present if probe unavailable


def _ffmpeg_download_with_audio(
    video_url: str,
    dest: Path,
    on_progress: Optional[Callable],
) -> Optional[Path]:
    """Download Facebook Story using ffmpeg's -map 0:a? to capture audio from DASH.

    Root cause of missing audio:
        The video CDN URL intercepted by Layer A is a pure video DASH track
        (/o1/v/...).  The corresponding audio track (/o1/a/...) has a different
        one-hop token (`oh`) that cannot be guessed or probed via HTTP from Python.
        All previous fix attempts failed because audio URL derivation with the
        video token returns 403 permanently.

    This approach lets ffmpeg handle the DASH manifest directly:
        ffmpeg opens the video CDN URL and reads the Content-Type response.
        If Facebook's CDN serves a DASH manifest (application/dash+xml) at this
        URL, ffmpeg discovers sibling audio tracks automatically via its lavf
        DASH demuxer.  We then select both streams with -map 0:v? -map 0:a?.

    Returns dest on success (with audio), None on failure.
    """
    from utils.ffmpeg_locator import locate_ffmpeg

    loc = locate_ffmpeg()
    if not loc:
        return None

    full_url = _full_video_url(video_url)
    dest.parent.mkdir(parents=True, exist_ok=True)

    if on_progress:
        try:
            on_progress(55, "", "FFmpeg đang tải video+audio từ DASH...")
        except Exception:
            pass

    headers_str = f"User-Agent: {_UA}\r\nReferer: https://www.facebook.com/\r\n"

    cmd = [
        loc.ffmpeg_bin,
        "-y",
        "-headers",
        headers_str,
        "-i",
        full_url,
        "-map",
        "0:v?",
        "-map",
        "0:a?",
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(dest),
    ]
    logger.debug("ffmpeg dash-all: %.80s…", full_url)

    try:
        result = subprocess.run(cmd, capture_output=True, timeout=120, creationflags=_WIN_NO_WINDOW)
    except subprocess.TimeoutExpired:
        logger.warning("_ffmpeg_download_with_audio: timeout")
        dest.unlink(missing_ok=True)
        return None
    except Exception as exc:
        logger.warning("_ffmpeg_download_with_audio error: %s", exc)
        dest.unlink(missing_ok=True)
        return None

    if result.returncode == 0 and _validate_mp4(dest):
        if _has_audio_stream(loc.ffmpeg_bin, dest):
            logger.info(
                "ffmpeg dash-all OK (with audio): %s (%d bytes)",
                dest.name,
                dest.stat().st_size,
            )
            return dest
        logger.debug("ffmpeg dash-all: no audio in result — DASH manifest has no audio track")
        dest.unlink(missing_ok=True)
        return None

    tail = result.stderr[-400:].decode("utf-8", errors="replace") if result.stderr else ""
    logger.warning("ffmpeg dash-all rc=%d stderr: %s", result.returncode, tail[-200:])
    dest.unlink(missing_ok=True)
    return None


def download_story(
    url: str,
    config: "ConfigManager",
    browser: str = "brave",
    on_progress: Optional[Callable[[int, str, str], None]] = None,
    timeout: float = 60.0,
) -> Path:
    """Download a Facebook Story video.

    Raises RuntimeError with a Vietnamese user-facing message on failure.
    Public API is identical to the previous CDP implementation.
    """
    if not is_facebook_story_url(url):
        raise RuntimeError("URL không phải Facebook Story.\nHãy dán URL dạng facebook.com/stories/...")

    if not config.download_dir:
        raise RuntimeError("Thư mục tải về chưa được thiết lập")
    output_dir = Path(config.download_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    m = re.search(r"/stories/(\d+)", url)
    slug = m.group(1)[:16] if m else str(int(time.time()))
    dest = output_dir / f"fb_story_{slug}.mp4"
    # Avoid silently overwriting a previous download of the same story
    if dest.exists():
        dest = output_dir / f"fb_story_{slug}_{int(time.time())}.mp4"

    # CDP via Playwright: capture video + audio DASH CDN URLs, and a
    # progressive (already-muxed) MP4 URL from Facebook's page data.
    # Facebook Stories usually stream video and audio as independent DASH
    # tracks; we mux them with FFmpeg to produce a file with sound. When the
    # audio-DASH URL is never intercepted, the progressive URL is a robust
    # fallback that already carries sound.
    cdn_url, audio_url, progressive_url = _cdp_intercept(url, browser, timeout, on_progress)

    if not cdn_url and progressive_url:
        logger.info("CDP: no DASH video URL — falling back to progressive URL as primary source")
        cdn_url = progressive_url

    if not cdn_url:
        raise RuntimeError(
            "Không bắt được URL video của Story.\n\n"
            "Có thể do:\n"
            "• Story đã hết hạn (Stories tồn tại 24 giờ)\n"
            "• Bạn chưa đăng nhập Facebook trong Brave/Chrome\n"
            "• Story này chỉ có ảnh (không có video)\n\n"
            "Mở Story trong trình duyệt kiểm tra trước."
        )

    logger.info("Video URL: %s…", cdn_url[:80])
    if audio_url:
        logger.info("Audio URL (intercepted): %s…", audio_url[:80])
    else:
        logger.info("Audio URL: not intercepted — will use progressive/ffmpeg DASH demuxer")
    if progressive_url:
        logger.info("Progressive URL: %s…", progressive_url[:80])

    # ── Audio URL derivation fallback ──────────────────────────────────────
    # Only attempt derivation if CDP already captured audio URL.
    # The _probe_audio_url approach (substituting /o1/v/→/o1/a/) is known to
    # return 403 because the video's `oh` one-hop token is not valid for audio.
    # We keep this block for cases where CDP DID capture audio URL directly —
    # which is path (a) below.
    if not audio_url:
        candidate = _derive_audio_url(cdn_url)
        if candidate:
            logger.debug("Derived audio candidate: %s…", candidate[:80])
            audio_url = _probe_audio_url(candidate)
            if audio_url:
                logger.info("Audio URL (derived+confirmed): %s…", audio_url[:80])
            else:
                logger.debug("Audio URL: derive probe 403 (expected) — using ffmpeg DASH path")

    if on_progress:
        try:
            on_progress(48, "", "Đã bắt được URL — đang tải...")
        except Exception:
            pass

    # ── Download strategy ──────────────────────────────────────────────────
    # Priority order (highest to lowest):
    #   1. ffmpeg mux: CDP captured both video+audio DASH URLs (best quality, guaranteed audio)
    #   2. progressive URL: Facebook page-data muxed MP4 (playable_url_quality_hd etc.) —
    #      already carries audio, no muxing needed
    #   3. ffmpeg DASH all-streams: let ffmpeg discover audio from DASH manifest
    #      (works when CDN URL is a manifest with sibling audio track)
    #   4. requests GET / ffmpeg single stream: video-only fallback (no audio, last resort)
    result: Optional[Path] = None
    if audio_url:
        # Path 1: CDP intercepted both streams — mux directly
        result = _ffmpeg_mux(cdn_url, audio_url, dest, on_progress)
        if not result:
            logger.warning("ffmpeg mux failed — trying progressive URL")

    if not result and progressive_url:
        # Path 2: progressive MP4 from page data — already has audio
        result = _download_cdn_url(progressive_url, dest, on_progress)
        if result:
            from utils.ffmpeg_locator import locate_ffmpeg

            loc = locate_ffmpeg()
            has_audio = _has_audio_stream(loc.ffmpeg_bin, result) if loc else True
            logger.info("Progressive URL download OK (audio=%s)", has_audio)
        else:
            logger.warning("Progressive URL download failed — trying DASH all-streams")

    if not result:
        # Path 3: Let ffmpeg read the DASH manifest and find audio automatically
        result = _ffmpeg_download_with_audio(cdn_url, dest, on_progress)
        if result:
            logger.info("Audio captured via ffmpeg DASH demuxer")
        else:
            logger.warning("ffmpeg DASH demuxer found no audio — falling back to video-only")

    if not result:
        # Path 4a: video-only via requests stream
        result = _download_cdn_url(cdn_url, dest, on_progress)
    if not result:
        # Path 4b: video-only via ffmpeg (reassemble DASH segments)
        result = _ffmpeg_download(cdn_url, dest, on_progress)

    if not result:
        raise RuntimeError(
            "Bắt được URL video nhưng không tải được file hoàn chỉnh.\n\n"
            "Nguyên nhân thường gặp:\n"
            "• CDN URL đã hết hạn (load quá lâu)\n"
            "• Kết nối mạng không ổn định\n\n"
            "Hãy thử lại ngay sau khi mở Story trong trình duyệt."
        )

    if on_progress:
        try:
            on_progress(100, "", f"✅ Hoàn thành! {result.name}")
        except Exception:
            pass

    logger.info("Facebook Story saved: %s (%d bytes)", result, result.stat().st_size)
    return result
