"""BUG-KS-COOKIE-HOST (v20.3.14): a Remote API download hands KuaishouEngine the
CDN URL from the request body. The Kuaishou session cookie must only go to
Kuaishou's own hosts, never to whatever host that URL names."""

import infrastructure.downloader.kuaishou_engine as ks
from domain.models.download_task import DownloadTask, MediaInfo


class _FakeSession:
    def __init__(self, seen):
        self._seen = seen

    def get(self, url, headers=None, **kw):
        self._seen.append((url, dict(headers or {})))
        raise ConnectionError("offline test")

    def close(self):
        pass


def _run(tmp_path, monkeypatch, cdn_url):
    seen: list = []
    monkeypatch.setattr(ks, "_load_cookie_str", lambda cfg: "sid=FAKE")
    monkeypatch.setattr(ks, "_make_session", lambda *a, **k: _FakeSession(seen))
    monkeypatch.setattr(
        ks, "extract_info_kuaishou", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no"))
    )
    monkeypatch.setattr(ks.time, "sleep", lambda s: None)

    class _Cfg:
        download_dir = str(tmp_path)
        proxy = ""

    engine = ks.KuaishouEngine(_Cfg())
    task = DownloadTask(url=cdn_url, format_id="best", output_ext="mp4")
    task.media_info = MediaInfo(url=cdn_url, title="t", source_engine="kuaishou", video_id="abc123")
    task.output_dir = str(tmp_path)
    try:
        engine.download(task)
    except Exception:  # offline session: the download is expected to fail
        pass
    return seen


def test_cookie_not_sent_to_foreign_host(tmp_path, monkeypatch):
    seen = _run(tmp_path, monkeypatch, "https://attacker.example/video.mp4")
    assert seen, "no request was attempted"
    leaked = [u for u, h in seen if "Cookie" in h]
    assert leaked == [], leaked


def test_cookie_not_sent_to_lookalike_host(tmp_path, monkeypatch):
    seen = _run(tmp_path, monkeypatch, "https://kwaicdn.com.attacker.example/video.mp4")
    assert [u for u, h in seen if "Cookie" in h] == []


def test_cookie_still_sent_to_kuaishou_cdn(tmp_path, monkeypatch):
    seen = _run(tmp_path, monkeypatch, "https://v2.kwaicdn.com/upic/abc.mp4?x=1")
    assert seen and all(h.get("Cookie") == "sid=FAKE" for _u, h in seen), seen
