"""Tests for the async tool handlers in server.py.

We don't spin up a full MCP transport — the goal is to verify the logic of
_get_voice_input and _take_over against fakes for the pipeline and the lock.
"""

from __future__ import annotations

import asyncio
from typing import Optional

import pytest

from livechat_mcp import config, server
from livechat_mcp.queue_manager import SessionState


# -----------------------------------------------------------------------------
# Fakes
# -----------------------------------------------------------------------------


class FakePipeline:
    def __init__(self, alive: bool = False) -> None:
        self._alive = alive
        self.start_count = 0

    def is_alive(self) -> bool:
        return self._alive

    def start(self) -> None:
        self.start_count += 1
        self._alive = True


class FakeLock:
    def __init__(self, holder: Optional[int] = None) -> None:
        # Sequence of holders to return on successive try_acquire() calls.
        # If a list, pop one each call. If a single int (or None), reuse forever.
        self._holders = holder if isinstance(holder, list) else [holder]
        self._idx = 0
        self.released = 0

    def try_acquire(self) -> Optional[int]:
        if self._idx < len(self._holders):
            v = self._holders[self._idx]
            self._idx += 1
            return v
        return self._holders[-1]

    def release(self) -> None:
        self.released += 1


# -----------------------------------------------------------------------------
# prompts
# -----------------------------------------------------------------------------


def test_load_prompt_body_strips_frontmatter():
    body = server._load_prompt_body("livechat")

    assert body.startswith("You are entering **live voice review mode**")
    assert "description:" not in body.splitlines()[0]
    assert "Begin: print `Listening" in body


def test_load_prompt_body_rejects_unknown_prompt():
    with pytest.raises(ValueError, match="Unknown prompt"):
        server._load_prompt_body("missing")


# -----------------------------------------------------------------------------
# _get_voice_input
# -----------------------------------------------------------------------------


async def test_get_voice_input_returns_drained_utterances_joined():
    state = SessionState()
    pipeline = FakePipeline(alive=True)
    lock = FakeLock(holder=None)
    state.push_utterance("hello")
    state.push_utterance("world")

    out = await server._get_voice_input(state, pipeline, lock)

    assert out == "hello / world"
    assert pipeline.start_count == 0  # already alive


async def test_get_voice_input_returns_end_session_when_shutdown_already_set():
    state = SessionState()
    pipeline = FakePipeline(alive=True)
    lock = FakeLock(holder=None)
    state.request_shutdown()

    out = await server._get_voice_input(state, pipeline, lock)

    assert out == config.SENTINEL_END_SESSION


async def test_get_voice_input_long_poll_timeout_returns_no_input(monkeypatch):
    state = SessionState()
    pipeline = FakePipeline(alive=True)
    lock = FakeLock(holder=None)

    # Make the long-poll timeout almost instantaneous so the test is fast.
    monkeypatch.setattr(config, "LONG_POLL_TIMEOUT_SEC", 0.05)
    monkeypatch.setattr(config, "QUEUE_POLL_INTERVAL_SEC", 0.01)

    out = await server._get_voice_input(state, pipeline, lock)

    assert out == config.SENTINEL_NO_INPUT


async def test_get_voice_input_long_poll_picks_up_late_utterance(monkeypatch):
    state = SessionState()
    pipeline = FakePipeline(alive=True)
    lock = FakeLock(holder=None)

    monkeypatch.setattr(config, "LONG_POLL_TIMEOUT_SEC", 1.0)
    monkeypatch.setattr(config, "QUEUE_POLL_INTERVAL_SEC", 0.01)

    async def push_later():
        await asyncio.sleep(0.05)
        state.push_utterance("late")

    pusher = asyncio.create_task(push_later())
    out = await server._get_voice_input(state, pipeline, lock)
    await pusher

    assert out == "late"


async def test_get_voice_input_long_poll_returns_end_session_on_shutdown(monkeypatch):
    state = SessionState()
    pipeline = FakePipeline(alive=True)
    lock = FakeLock(holder=None)

    monkeypatch.setattr(config, "LONG_POLL_TIMEOUT_SEC", 1.0)
    monkeypatch.setattr(config, "QUEUE_POLL_INTERVAL_SEC", 0.01)

    async def shutdown_later():
        await asyncio.sleep(0.05)
        state.request_shutdown()

    asyncio.create_task(shutdown_later())
    out = await server._get_voice_input(state, pipeline, lock)

    assert out == config.SENTINEL_END_SESSION


async def test_get_voice_input_starts_pipeline_when_lock_free():
    state = SessionState()
    pipeline = FakePipeline(alive=False)
    lock = FakeLock(holder=None)
    state.push_utterance("first")

    out = await server._get_voice_input(state, pipeline, lock)

    assert out == "first"
    assert pipeline.start_count == 1


async def test_get_voice_input_returns_already_running_when_lock_held():
    state = SessionState()
    pipeline = FakePipeline(alive=False)
    lock = FakeLock(holder=42_000)

    out = await server._get_voice_input(state, pipeline, lock)

    assert out == f"{config.SENTINEL_ALREADY_RUNNING}:42000"
    assert pipeline.start_count == 0


async def test_get_voice_input_resets_state_after_previous_session_ended(monkeypatch):
    """If the previous /endlivechat left shutdown_requested set, calling the tool
    again on a non-alive pipeline should reset and re-start, not loop on END."""
    state = SessionState()
    state.push_utterance("stale")  # left over from previous session
    state.request_shutdown()

    pipeline = FakePipeline(alive=False)
    lock = FakeLock(holder=None)

    monkeypatch.setattr(config, "LONG_POLL_TIMEOUT_SEC", 0.05)
    monkeypatch.setattr(config, "QUEUE_POLL_INTERVAL_SEC", 0.01)

    out = await server._get_voice_input(state, pipeline, lock)

    # Stale utterance was cleared, fresh pipeline started, long-poll timed out.
    assert out == config.SENTINEL_NO_INPUT
    assert pipeline.start_count == 1
    assert not state.shutdown_requested()


# -----------------------------------------------------------------------------
# _take_over
# -----------------------------------------------------------------------------


async def test_take_over_when_already_holding_returns_ok_already():
    state = SessionState()
    pipeline = FakePipeline(alive=True)
    lock = FakeLock(holder=None)

    out = await server._take_over(state, pipeline, lock)

    assert out == "OK (already holding the session)"


async def test_take_over_when_lock_free_starts_pipeline():
    state = SessionState()
    pipeline = FakePipeline(alive=False)
    lock = FakeLock(holder=None)

    out = await server._take_over(state, pipeline, lock)

    assert out == "OK"
    assert pipeline.start_count == 1


async def test_take_over_signals_holder_and_succeeds_when_holder_releases(monkeypatch):
    state = SessionState()
    pipeline = FakePipeline(alive=False)
    # First try_acquire reports holder; subsequent calls report free.
    lock = FakeLock(holder=[12345, None])

    monkeypatch.setattr(server, "signal_holder_to_shutdown", lambda pid: True)

    out = await server._take_over(state, pipeline, lock)

    assert out == "OK"
    assert pipeline.start_count == 1


async def test_take_over_signal_failure_returns_error(monkeypatch):
    state = SessionState()
    pipeline = FakePipeline(alive=False)
    lock = FakeLock(holder=99_999)

    monkeypatch.setattr(server, "signal_holder_to_shutdown", lambda pid: False)

    out = await server._take_over(state, pipeline, lock)

    assert "failed to signal" in out
    assert "99999" in out
    assert pipeline.start_count == 0


async def test_take_over_times_out_when_holder_does_not_release(monkeypatch):
    state = SessionState()
    pipeline = FakePipeline(alive=False)
    # Holder never releases — every try_acquire returns the same PID.
    lock = FakeLock(holder=55_555)

    monkeypatch.setattr(server, "signal_holder_to_shutdown", lambda pid: True)
    # Speed up the loop so the test takes ~0.3s instead of 3s.
    real_sleep = asyncio.sleep
    monkeypatch.setattr(server.time, "monotonic", _fast_clock())

    out = await server._take_over(state, pipeline, lock)

    assert "did not release" in out
    assert "55555" in out
    assert pipeline.start_count == 0


def _fast_clock():
    """Returns a monotonic() replacement that advances 0.5s per call so the 3s
    deadline in _take_over is hit after ~6 polls (≈0.6s of real sleep)."""
    counter = {"t": 0.0}

    def now():
        counter["t"] += 0.5
        return counter["t"]

    return now


async def test_take_over_resets_state_after_previous_session_ended():
    state = SessionState()
    state.push_utterance("stale")
    state.request_shutdown()

    pipeline = FakePipeline(alive=False)
    lock = FakeLock(holder=None)

    out = await server._take_over(state, pipeline, lock)

    assert out == "OK"
    assert pipeline.start_count == 1
    assert not state.shutdown_requested()
    assert state.drain_utterances() == []
