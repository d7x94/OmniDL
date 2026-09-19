"""
tests/test_batch_convert_and_story.py

Covers the two features added in this change set:

Batch conversion
  BC-1  ConvertQueue.set_max_concurrent clamps to [1, MAX_CONCURRENT_LIMIT]
        and workers already submitted keep the semaphore they captured.
  BC-2  ConvertTab._selected_pending honours the per-file checkboxes and
        falls back to "everything pending" when nothing is ticked, both as
        pure logic and through the live Qt signals on a real ConvertTab.
  BC-3  POST /api/files/convert/batch queues every valid path, reports the
        invalid ones instead of failing the whole call, and 422s only when
        nothing could be queued.
  BC-4  GET/POST /api/convert/concurrency read and persist the limit, and the
        literal route is registered ahead of /api/convert/{job_id}.

Facebook Story
  FS-1  _cdp_intercept refuses to launch while the browser is already running
        (Chromium would forward the command line and drop the debug port).
  FS-2  cdp_only_reason() gates /api/analyse and /api/download identically.
  FS-3  _normalize_url / _full_video_url keep repeated query parameters.

Endpoint functions are invoked directly (the test_api_archive.py convention) —
this repo has no httpx dependency installed.
"""

from __future__ import annotations

import asyncio
import inspect
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import api.server as srv
from api.models import BatchFileConvertRequest, ConvertConcurrencyRequest
from app.services.ffmpeg_convert_service import ConvertQueue

# ── Helpers ─────────────────────────────────────────────────────────────────


def _endpoint(app, path: str, method: str):
    for route in app.routes:
        if getattr(route, "path", None) != path:
            continue
        if method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"route {method} {path} not found")


def _call(fn, *args, **kwargs):
    result = fn(*args, **kwargs)
    return asyncio.run(result) if inspect.iscoroutine(result) else result


class _FakeConvert:
    """RemoteConvertService stand-in: records calls, hands back job ids."""

    def __init__(self) -> None:
        self.started: list[Path] = []
        self.max_concurrent = 2
        self.persisted: list[int] = []
        self.raise_for: set[str] = set()

    def start_convert_from_path(self, *, file_path: Path, **_kw):
        if file_path.name in self.raise_for:
            raise ValueError("bad settings")
        self.started.append(file_path)
        return SimpleNamespace(job_id=f"job{len(self.started)}")

    def set_max_concurrent(self, value: int) -> int:
        applied = max(1, min(ConvertQueue.MAX_CONCURRENT_LIMIT, int(value)))
        self.max_concurrent = applied
        self.persisted.append(applied)
        return applied


def _make_app(download_dir: Path, convert: _FakeConvert | None = None):
    convert = convert if convert is not None else _FakeConvert()
    config = SimpleNamespace(
        api_token="",
        download_dir=download_dir,
        taildrop_target_nodes=[],
        taildrop_enabled=False,
    )
    service = SimpleNamespace(get_all_tasks=lambda: [], get_history=lambda: [])
    app = srv.create_app(service, config, remote_convert=convert)  # type: ignore[arg-type]
    return app, convert


def _batch_body(paths: list[str]) -> BatchFileConvertRequest:
    return BatchFileConvertRequest(file_paths=paths)


# ── BC-1 ────────────────────────────────────────────────────────────────────


class TestConvertQueueConcurrency:
    def test_clamped_to_range(self) -> None:
        q = ConvertQueue(max_concurrent=2)
        assert q.set_max_concurrent(0) == 1
        assert q.set_max_concurrent(99) == ConvertQueue.MAX_CONCURRENT_LIMIT
        assert q.set_max_concurrent(3) == 3
        assert q.max_concurrent == 3

    def test_unchanged_value_keeps_same_semaphore(self) -> None:
        q = ConvertQueue(max_concurrent=2)
        before = q._semaphore
        q.set_max_concurrent(2)
        assert q._semaphore is before

    def test_running_worker_releases_the_semaphore_it_acquired(self, tmp_path: Path) -> None:
        """A resize mid-flight must not leave the old semaphore permanently held."""
        q = ConvertQueue(max_concurrent=1)
        old_sem = q._semaphore
        entered = threading.Event()
        release = threading.Event()

        def _fake_run(*_a, **_kw):
            entered.set()
            release.wait(5)

        q._svc._run = _fake_run  # type: ignore[assignment]
        q.submit(source=tmp_path / "a.mp4")
        assert entered.wait(5)

        q.set_max_concurrent(4)  # swaps self._semaphore while the worker runs
        release.set()

        # The worker must give back the semaphore it took, not the new one.
        assert old_sem.acquire(timeout=5)
        assert q._semaphore is not old_sem


# ── BC-2 ────────────────────────────────────────────────────────────────────


class TestSelectedPending:
    """_selected_pending is pure logic over the job dict — no Qt needed."""

    @staticmethod
    def _tab(jobs):
        from ui.tabs.convert_tab import ConvertTab

        stub = SimpleNamespace(_jobs={j.id: j for j in jobs})
        return ConvertTab._selected_pending(stub)  # type: ignore[arg-type]

    @staticmethod
    def _job(jid: str, *, selected: bool, state=None):
        from ui.tabs.convert_tab import FileJob, FileState

        return FileJob(id=jid, source=Path(f"{jid}.mp4"), state=state or FileState.PENDING, selected=selected)

    def test_only_ticked_files_are_returned(self) -> None:
        a = self._job("a", selected=True)
        b = self._job("b", selected=False)
        assert [j.id for j in self._tab([a, b])] == ["a"]

    def test_nothing_ticked_falls_back_to_all_pending(self) -> None:
        a = self._job("a", selected=False)
        b = self._job("b", selected=False)
        assert {j.id for j in self._tab([a, b])} == {"a", "b"}

    def test_non_pending_files_are_never_included(self) -> None:
        from ui.tabs.convert_tab import FileState

        done = self._job("a", selected=True, state=FileState.DONE)
        pending = self._job("b", selected=True)
        assert [j.id for j in self._tab([done, pending])] == ["b"]

    def test_new_files_start_selected(self) -> None:
        from ui.tabs.convert_tab import FileJob

        assert FileJob().selected is True


class TestConvertTabWidgetWiring:
    """One real-widget test: the checkbox/signal wiring is exactly what the
    unbound-method pattern used elsewhere in this repo cannot catch (a
    setChecked-before-connect slip, or a select-all feedback loop, only shows
    up once the signals are live)."""

    @staticmethod
    def _tab(tmp_path: Path):
        pytest.importorskip("PySide6")
        from PySide6.QtWidgets import QApplication

        from app.event_bus import EventBus
        from infrastructure.config.config_manager import ConfigManager
        from ui.tabs.convert_tab import ConvertTab

        if QApplication.instance() is None:
            try:
                QApplication([])
            except Exception:  # no display and no offscreen plugin
                pytest.skip("Qt cannot create a QApplication in this environment")

        config = ConfigManager(tmp_path / "config.json")
        app = SimpleNamespace(config=config, taildrop=SimpleNamespace(bus=EventBus()))
        return ConvertTab(app), config  # type: ignore[arg-type]

    @staticmethod
    def _add(tab, *names: str):
        from ui.tabs.convert_tab import FileJob

        for name in names:
            job = FileJob(source=Path(name))
            tab._jobs[job.id] = job
        tab._refresh_ui()

    def test_unticking_a_card_narrows_the_convert_set(self, tmp_path: Path) -> None:
        tab, _ = self._tab(tmp_path)
        self._add(tab, "a.mp4", "b.mp4")

        first_card = next(iter(tab._cards.values()))
        first_card._sel_cb.setChecked(False)   # real signal path

        assert [j.source.name for j in tab._selected_pending()] == ["b.mp4"]
        assert tab._select_all_cb.isChecked() is False

    def test_select_all_reticks_every_pending_file(self, tmp_path: Path) -> None:
        tab, _ = self._tab(tmp_path)
        self._add(tab, "a.mp4", "b.mp4")
        next(iter(tab._cards.values()))._sel_cb.setChecked(False)

        tab._on_select_all_clicked(True)

        assert sorted(j.source.name for j in tab._selected_pending()) == ["a.mp4", "b.mp4"]

    def test_unticking_everything_still_converts_everything(self, tmp_path: Path) -> None:
        tab, _ = self._tab(tmp_path)
        self._add(tab, "a.mp4", "b.mp4")

        tab._on_select_all_clicked(False)

        assert sorted(j.source.name for j in tab._selected_pending()) == ["a.mp4", "b.mp4"]

    def test_a_queued_file_cannot_be_ticked(self, tmp_path: Path) -> None:
        from ui.tabs.convert_tab import FileState

        tab, _ = self._tab(tmp_path)
        self._add(tab, "a.mp4")
        job = next(iter(tab._jobs.values()))
        job.state = FileState.QUEUED
        tab._rebuild_card(job)

        assert tab._cards[job.id]._sel_cb.isEnabled() is False

    def test_parallel_spinner_updates_queue_and_config(self, tmp_path: Path) -> None:
        tab, config = self._tab(tmp_path)

        tab._par_spin.setValue(5)

        assert tab._queue.max_concurrent == 5
        assert config.convert_max_concurrent == 5

    def test_spinner_starts_from_the_saved_setting(self, tmp_path: Path) -> None:
        tab, config = self._tab(tmp_path)
        config.set("convert_max_concurrent", 4)
        tab2, _ = self._tab(tmp_path)
        assert tab2._par_spin.value() == 4
        assert tab2._queue.max_concurrent == 4


# ── BC-3 ────────────────────────────────────────────────────────────────────


class TestBatchConvertEndpoint:
    def test_queues_every_valid_path(self, tmp_path: Path) -> None:
        for name in ("a.mp4", "b.mp4"):
            (tmp_path / name).write_bytes(b"x")
        app, convert = _make_app(tmp_path)

        resp = _call(
            _endpoint(app, "/api/files/convert/batch", "POST"),
            _batch_body([str(tmp_path / "a.mp4"), str(tmp_path / "b.mp4")]),
            None,
        )

        assert resp.queued == 2
        assert resp.failed == 0
        assert resp.job_ids == ["job1", "job2"]
        assert [p.name for p in convert.started] == ["a.mp4", "b.mp4"]

    def test_partial_failure_still_queues_the_rest(self, tmp_path: Path) -> None:
        (tmp_path / "ok.mp4").write_bytes(b"x")
        app, convert = _make_app(tmp_path)

        resp = _call(
            _endpoint(app, "/api/files/convert/batch", "POST"),
            _batch_body([str(tmp_path / "ok.mp4"), str(tmp_path / "gone.mp4")]),
            None,
        )

        assert resp.queued == 1
        assert resp.failed == 1
        assert resp.errors[0].file_path.endswith("gone.mp4")
        assert [p.name for p in convert.started] == ["ok.mp4"]

    def test_path_outside_download_dir_is_rejected(self, tmp_path: Path) -> None:
        root = tmp_path / "dl"
        root.mkdir()
        outside = tmp_path / "secret.mp4"
        outside.write_bytes(b"x")
        app, convert = _make_app(root)

        with pytest.raises(Exception) as exc:
            _call(
                _endpoint(app, "/api/files/convert/batch", "POST"),
                _batch_body([str(outside)]),
                None,
            )

        assert exc.value.status_code == 422  # type: ignore[attr-defined]
        assert convert.started == []

    def test_all_invalid_raises_422(self, tmp_path: Path) -> None:
        app, _ = _make_app(tmp_path)
        with pytest.raises(Exception) as exc:
            _call(
                _endpoint(app, "/api/files/convert/batch", "POST"),
                _batch_body([str(tmp_path / "nope.mp4")]),
                None,
            )
        assert exc.value.status_code == 422  # type: ignore[attr-defined]

    def test_service_unavailable_is_503(self, tmp_path: Path) -> None:
        config = SimpleNamespace(
            api_token="", download_dir=tmp_path, taildrop_target_nodes=[], taildrop_enabled=False
        )
        service = SimpleNamespace(get_all_tasks=lambda: [], get_history=lambda: [])
        app = srv.create_app(service, config)  # type: ignore[arg-type]
        with pytest.raises(Exception) as exc:
            _call(_endpoint(app, "/api/files/convert/batch", "POST"), _batch_body(["x"]), None)
        assert exc.value.status_code == 503  # type: ignore[attr-defined]

    def test_empty_list_is_rejected_by_the_model(self) -> None:
        with pytest.raises(Exception):
            BatchFileConvertRequest(file_paths=[])

    def test_over_100_paths_is_rejected_by_the_model(self) -> None:
        with pytest.raises(Exception):
            BatchFileConvertRequest(file_paths=[f"f{i}.mp4" for i in range(101)])


# ── BC-4 ────────────────────────────────────────────────────────────────────


class TestConcurrencyEndpoint:
    def test_get_reports_current_and_limit(self, tmp_path: Path) -> None:
        app, convert = _make_app(tmp_path)
        convert.max_concurrent = 3
        resp = _call(_endpoint(app, "/api/convert/concurrency", "GET"), None)
        assert resp.max_concurrent == 3
        assert resp.limit == ConvertQueue.MAX_CONCURRENT_LIMIT

    def test_post_applies_and_persists(self, tmp_path: Path) -> None:
        app, convert = _make_app(tmp_path)
        resp = _call(
            _endpoint(app, "/api/convert/concurrency", "POST"),
            ConvertConcurrencyRequest(max_concurrent=4),
            None,
        )
        assert resp.max_concurrent == 4
        assert convert.persisted == [4]

    def test_out_of_range_is_rejected_by_the_model(self) -> None:
        for bad in (0, 9):
            with pytest.raises(Exception):
                ConvertConcurrencyRequest(max_concurrent=bad)

    def test_literal_route_is_registered_before_the_job_id_route(self, tmp_path: Path) -> None:
        """Otherwise GET /api/convert/concurrency is swallowed by {job_id}."""
        app, _ = _make_app(tmp_path)
        paths = [getattr(r, "path", "") for r in app.routes]
        assert paths.index("/api/convert/concurrency") < paths.index("/api/convert/{job_id}")


# ── FS-1 ────────────────────────────────────────────────────────────────────


class TestStoryBrowserRunningGuard:
    def test_refuses_to_launch_while_browser_is_open(self, monkeypatch) -> None:
        import infrastructure.downloader.cookie_extractor as ck
        import infrastructure.downloader.facebook_story_engine as fse

        monkeypatch.setattr(fse, "_find_browser_exe", lambda _b: "brave.exe")
        monkeypatch.setattr(ck, "_is_browser_running", lambda _b: True)

        def _boom(*_a, **_kw):
            raise AssertionError("browser must not be launched")

        monkeypatch.setattr(fse.subprocess, "Popen", _boom)

        with pytest.raises(RuntimeError) as exc:
            fse._cdp_intercept("https://facebook.com/stories/1", "brave", 5.0, None)
        assert "brave" in str(exc.value).lower()

    def test_mac_process_lookup_uses_the_bundle_name(self, monkeypatch) -> None:
        import infrastructure.downloader.cookie_extractor as ck

        seen: dict = {}

        class _Res:
            returncode = 0

        def _fake_run(cmd, **_kw):
            seen["cmd"] = cmd
            return _Res()

        monkeypatch.setattr("sys.platform", "darwin")
        monkeypatch.setattr("subprocess.run", _fake_run)

        assert ck._is_browser_running("brave") is True
        assert seen["cmd"] == ["pgrep", "-x", "Brave Browser"]


# ── FS-2 ────────────────────────────────────────────────────────────────────


class TestCdpOnlyReason:
    def test_story_permalink_blocked_off_windows_and_mac(self, monkeypatch) -> None:
        monkeypatch.setattr("sys.platform", "linux")
        reason = srv.cdp_only_reason("https://www.facebook.com/stories/12345/")
        assert "Facebook Story" in reason

    def test_fb_watch_link_is_not_blocked(self, monkeypatch) -> None:
        """fb.watch fronts ordinary videos yt-dlp handles fine on a server."""
        monkeypatch.setattr("sys.platform", "linux")
        assert srv.cdp_only_reason("https://fb.watch/abcd/") == ""

    def test_nothing_blocked_on_windows(self, monkeypatch) -> None:
        monkeypatch.setattr("sys.platform", "win32")
        assert srv.cdp_only_reason("https://www.facebook.com/stories/12345/") == ""

    def test_ordinary_url_is_never_blocked(self, monkeypatch) -> None:
        monkeypatch.setattr("sys.platform", "linux")
        assert srv.cdp_only_reason("https://youtube.com/watch?v=x") == ""


# ── FS-3 ────────────────────────────────────────────────────────────────────


class TestStoryUrlHelpers:
    def test_normalize_keeps_repeated_params(self) -> None:
        from infrastructure.downloader.facebook_story_engine import _normalize_url

        out = _normalize_url("https://facebook.com/stories/1?a=1&a=2")
        assert out.count("a=1") == 1
        assert out.count("a=2") == 1
        assert "view_single=1" in out

    def test_full_video_url_strips_only_the_range_params(self) -> None:
        from infrastructure.downloader.facebook_story_engine import _full_video_url

        out = _full_video_url("https://x.fbcdn.net/v/t42?bytestart=0&byteend=9&oh=abc&tag=1&tag=2")
        assert "bytestart" not in out and "byteend" not in out
        assert "oh=abc" in out
        assert out.count("tag=1") == 1 and out.count("tag=2") == 1
