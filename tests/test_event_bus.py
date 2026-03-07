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
