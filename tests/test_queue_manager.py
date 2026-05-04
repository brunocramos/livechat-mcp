from __future__ import annotations

import threading
import time

import pytest

from livechat_mcp.queue_manager import SessionState


def test_push_and_drain_returns_in_order():
    s = SessionState()
    s.push_utterance("hello")
    s.push_utterance("world")
    assert s.drain_utterances() == ["hello", "world"]


def test_drain_clears_queue():
    s = SessionState()
    s.push_utterance("once")
    assert s.drain_utterances() == ["once"]
    assert s.drain_utterances() == []


def test_drain_empty_returns_empty_list():
    assert SessionState().drain_utterances() == []


def test_push_strips_surrounding_whitespace():
    s = SessionState()
    s.push_utterance("  hello  ")
    assert s.drain_utterances() == ["hello"]


@pytest.mark.parametrize("text", ["", "   ", "\t\n"])
def test_push_drops_blank(text):
    s = SessionState()
    s.push_utterance(text)
    assert s.drain_utterances() == []


def test_get_blocking_returns_immediately_when_queued():
    s = SessionState()
    s.push_utterance("hi")
    t0 = time.monotonic()
    got = s.get_utterance_blocking(timeout=1.0)
    elapsed = time.monotonic() - t0
    assert got == "hi"
    assert elapsed < 0.1


def test_get_blocking_timeout_returns_none():
    s = SessionState()
    t0 = time.monotonic()
    got = s.get_utterance_blocking(timeout=0.05)
    elapsed = time.monotonic() - t0
    assert got is None
    assert elapsed >= 0.04  # roughly the timeout


def test_get_blocking_wakes_on_push():
    s = SessionState()
    received: list[str] = []

    def consumer():
        v = s.get_utterance_blocking(timeout=2.0)
        if v is not None:
            received.append(v)

    t = threading.Thread(target=consumer)
    t.start()
    time.sleep(0.05)
    s.push_utterance("delayed")
    t.join(timeout=2.0)
    assert received == ["delayed"]


def test_shutdown_signaling_round_trip():
    s = SessionState()
    assert not s.shutdown_requested()
    s.request_shutdown()
    assert s.shutdown_requested()


def test_wait_for_shutdown_returns_true_when_set():
    s = SessionState()
    s.request_shutdown()
    assert s.wait_for_shutdown(timeout=0.0) is True


def test_wait_for_shutdown_times_out_when_unset():
    s = SessionState()
    assert s.wait_for_shutdown(timeout=0.01) is False


def test_reset_for_new_session_clears_queue_shutdown_and_started():
    s = SessionState()
    s.push_utterance("stale")
    s.request_shutdown()
    s.mark_started()

    s.reset_for_new_session()

    assert s.drain_utterances() == []
    assert not s.shutdown_requested()
    assert not s.wait_until_started(timeout=0.0)


def test_mark_started_and_wait_until_started():
    s = SessionState()
    assert not s.wait_until_started(timeout=0.0)
    s.mark_started()
    assert s.wait_until_started(timeout=0.1)
