"""
tests/test_event_bus.py
Unit tests for app/event_bus.py

Covers uncovered lines (63% → target 90%+):
- unsubscribe() removes handler
- handler exception does not kill other handlers
- publish() with no subscribers does nothing
- subscribe / unsubscribe is thread-safe under concurrent access
"""
import threading

from app.event_bus import EventBus

# ---------------------------------------------------------------------------
# Basic pub/sub
# ---------------------------------------------------------------------------

class TestBasicPubSub:
    def test_subscribed_handler_is_called(self):
        bus = EventBus()
        calls = []
        bus.subscribe("test.event", lambda **kw: calls.append(kw))
        bus.publish("test.event", value=42)
        assert calls == [{"value": 42}]

    def test_unsubscribed_handler_is_not_called(self):
        bus = EventBus()
        calls = []
        def handler(**kw):
            calls.append(kw)
        bus.subscribe("test.event", handler)
        bus.unsubscribe("test.event", handler)
        bus.publish("test.event", value=99)
        assert calls == []

    def test_publish_with_no_subscribers_does_not_raise(self):
        bus = EventBus()
        bus.publish("event.nobody.cares")  # should be silent

    def test_multiple_handlers_all_called(self):
        bus = EventBus()
        calls = []
        bus.subscribe("ev", lambda **kw: calls.append(1))
        bus.subscribe("ev", lambda **kw: calls.append(2))
        bus.publish("ev")
        assert sorted(calls) == [1, 2]

    def test_unsubscribe_nonexistent_handler_does_not_raise(self):
        bus = EventBus()
        bus.unsubscribe("ev", lambda **kw: None)  # should not raise


# ---------------------------------------------------------------------------
# Exception isolation
# ---------------------------------------------------------------------------

class TestExceptionIsolation:
    def test_handler_exception_does_not_prevent_other_handlers(self):
        bus = EventBus()
        results = []

        def bad_handler(**kw):
            raise ValueError("I crashed!")

        def good_handler(**kw):
            results.append("ok")

        bus.subscribe("ev", bad_handler)
        bus.subscribe("ev", good_handler)
        bus.publish("ev")  # bad_handler raises, good_handler should still run
        assert "ok" in results


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------

class TestThreadSafety:
    def test_concurrent_publish_does_not_crash(self):
        bus = EventBus()
        results = []
        lock = threading.Lock()

        def handler(**kw):
            with lock:
                results.append(kw.get("n"))

        bus.subscribe("ev", handler)

        threads = [
            threading.Thread(target=bus.publish, args=("ev",), kwargs={"n": i})
            for i in range(50)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 50

    def test_unsubscribe_during_publish_does_not_crash(self):
        """Handlers list is copied before iteration.

        Modification mid-publish is safe.
        """
        bus = EventBus()
        calls = []

        def self_removing(**kw):
            bus.unsubscribe("ev", self_removing)
            calls.append("fired")

        bus.subscribe("ev", self_removing)
        bus.publish("ev")
        bus.publish("ev")  # second publish should not call it again
        assert calls == ["fired"]


# ---------------------------------------------------------------------------
# publish_* convenience helpers (lines 73-130 coverage)
# ---------------------------------------------------------------------------

class TestPublishHelpers:
    """All publish_* shortcuts must fire the matching event with correct kwargs."""

    def _bus_with_capture(self, event_name):
        bus = EventBus()
        received = []
        bus.subscribe(event_name, lambda **kw: received.append(kw))
        return bus, received

    def test_publish_download_started(self):
        bus, recv = self._bus_with_capture(EventBus.DOWNLOAD_STARTED)
        task = object()
        bus.publish_download_started(task)
        assert recv and recv[0]["task"] is task

    def test_publish_download_progress(self):
        bus, recv = self._bus_with_capture(EventBus.DOWNLOAD_PROGRESS)
        task = object()
        bus.publish_download_progress(task)
        assert recv and recv[0]["task"] is task

    def test_publish_download_completed(self):
        bus, recv = self._bus_with_capture(EventBus.DOWNLOAD_COMPLETED)
        task = object()
        bus.publish_download_completed(task)
        assert recv and recv[0]["task"] is task

    def test_publish_download_failed(self):
        bus, recv = self._bus_with_capture(EventBus.DOWNLOAD_FAILED)
        task = object()
        bus.publish_download_failed(task)
        assert recv and recv[0]["task"] is task

    def test_publish_download_cancelled(self):
        bus, recv = self._bus_with_capture(EventBus.DOWNLOAD_CANCELLED)
        task = object()
        bus.publish_download_cancelled(task)
        assert recv and recv[0]["task"] is task

    def test_publish_analysis_done(self):
        bus, recv = self._bus_with_capture(EventBus.ANALYSIS_DONE)
        info = object()
        bus.publish_analysis_done(info)
        assert recv and recv[0]["info"] is info

    def test_publish_analysis_failed(self):
        bus, recv = self._bus_with_capture(EventBus.ANALYSIS_FAILED)
        bus.publish_analysis_failed("oops")
        assert recv and recv[0]["error"] == "oops"

    def test_publish_taildrop_completed(self):
        bus, recv = self._bus_with_capture(EventBus.TAILDROP_COMPLETED)
        task = object()
        bus.publish_taildrop_completed(task, dest_node="phone")
        assert recv and recv[0]["dest_node"] == "phone"

    def test_publish_taildrop_failed(self):
        bus, recv = self._bus_with_capture(EventBus.TAILDROP_FAILED)
        task = object()
        bus.publish_taildrop_failed(task, dest_node="phone", error="timeout")
        assert recv and recv[0]["error"] == "timeout"

    def test_publish_convert_taildrop_completed(self):
        from pathlib import Path
        bus, recv = self._bus_with_capture(EventBus.CONVERT_TAILDROP_COMPLETED)
        p = Path("/tmp/out.mp4")  # nosec B108
        bus.publish_convert_taildrop_completed(p, dest_node="phone")
        assert recv and recv[0]["out_path"] == p

    def test_publish_convert_taildrop_failed(self):
        from pathlib import Path
        bus, recv = self._bus_with_capture(EventBus.CONVERT_TAILDROP_FAILED)
        p = Path("/tmp/out.mp4")  # nosec B108
        bus.publish_convert_taildrop_failed(p, dest_node="phone", error="err")
        assert recv and recv[0]["error"] == "err"

    def test_publish_convert_started(self):
        bus, recv = self._bus_with_capture(EventBus.CONVERT_STARTED)
        job = object()
        bus.publish_convert_started(job)
        assert recv and recv[0]["job"] is job

    def test_publish_convert_progress(self):
        bus, recv = self._bus_with_capture(EventBus.CONVERT_PROGRESS)
        job = object()
        bus.publish_convert_progress(job)
        assert recv and recv[0]["job"] is job

    def test_publish_convert_completed(self):
        bus, recv = self._bus_with_capture(EventBus.CONVERT_COMPLETED)
        job = object()
        bus.publish_convert_completed(job)
        assert recv and recv[0]["job"] is job

    def test_publish_convert_failed(self):
        bus, recv = self._bus_with_capture(EventBus.CONVERT_FAILED)
        job = object()
        bus.publish_convert_failed(job)
        assert recv and recv[0]["job"] is job

    def test_publish_convert_cancelled(self):
        bus, recv = self._bus_with_capture(EventBus.CONVERT_CANCELLED)
        job = object()
        bus.publish_convert_cancelled(job)
        assert recv and recv[0]["job"] is job
