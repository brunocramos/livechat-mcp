"""Drives AudioPipeline._run_inner with a fake sounddevice module and scripted
VAD decisions to verify the segmentation state machine end-to-end without
needing a real microphone or Whisper model."""

from __future__ import annotations

import sys
import threading
import time
from typing import Optional

import numpy as np
import pytest

from livechat_mcp import config
from livechat_mcp.audio import AudioPipeline
from livechat_mcp.queue_manager import SessionState


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _make_fake_sd(blocksize: int, n_frames: int):
    """Build a fake `sounddevice` module whose InputStream synchronously pushes
    `n_frames` of zero-audio through the supplied callback when the with block
    is entered."""

    class _FakeInputStream:
        def __init__(self, samplerate, channels, dtype, blocksize, callback):  # noqa: ANN001
            self._cb = callback
            self._blocksize = blocksize

        def __enter__(self):
            for _ in range(n_frames):
                f = np.zeros((self._blocksize, 1), dtype=np.float32)
                self._cb(f, self._blocksize, None, None)
            return self

        def __exit__(self, *a):
            return False

    fake = type("sounddevice", (), {"InputStream": _FakeInputStream})()
    return fake


def _watchdog_shutdown(state: SessionState, seconds: float = 3.0) -> None:
    """Forces shutdown after `seconds` if the test wedges. Daemon thread, harmless."""
    def fire():
        time.sleep(seconds)
        state.request_shutdown()
    threading.Thread(target=fire, daemon=True).start()


def _scripted_vad(state: SessionState, decisions: list[bool]):
    """Returns an _is_speech replacement that walks `decisions` and requests
    shutdown once the script is exhausted."""
    calls = {"n": 0}

    def is_speech(_frame: np.ndarray) -> bool:
        i = calls["n"]
        calls["n"] += 1
        if i >= len(decisions):
            state.request_shutdown()
            return False
        return decisions[i]

    return is_speech


def _silence_frames_to_end(silence_sec: Optional[float] = None) -> int:
    s = silence_sec if silence_sec is not None else config.SILENCE_TO_END_UTTERANCE_SEC
    return int(s * config.SAMPLE_RATE / config.VAD_FRAME_SAMPLES)


class _FakeTranscriber:
    def __init__(self, text: Optional[str] = "stub-text") -> None:
        self.text = text
        self.calls: list[int] = []

    def transcribe(self, audio: np.ndarray) -> Optional[str]:
        self.calls.append(len(audio))
        return self.text


# -----------------------------------------------------------------------------
# Tests
# -----------------------------------------------------------------------------


def test_loop_segments_speech_then_silence_into_one_utterance(monkeypatch):
    state = SessionState()
    transcriber = _FakeTranscriber("hello")
    pipeline = AudioPipeline(state, transcriber)

    silence_to_end = _silence_frames_to_end()
    decisions = [True] * 30 + [False] * (silence_to_end + 5)

    pipeline._load_vad = lambda: object()
    pipeline._is_speech = _scripted_vad(state, decisions)

    monkeypatch.setitem(
        sys.modules, "sounddevice",
        _make_fake_sd(config.VAD_FRAME_SAMPLES, len(decisions) + 10),
    )
    _watchdog_shutdown(state)

    pipeline._run_inner()

    assert state.drain_utterances() == ["hello"]
    assert len(transcriber.calls) == 1


def test_loop_does_not_finalize_when_speech_never_ends(monkeypatch):
    """Speech with no terminating silence and no max-cut should not produce
    an utterance — the loop just exits when shutdown fires."""
    state = SessionState()
    transcriber = _FakeTranscriber("should-not-be-called")
    pipeline = AudioPipeline(state, transcriber)

    decisions = [True] * 30  # all speech, no silence

    pipeline._load_vad = lambda: object()
    pipeline._is_speech = _scripted_vad(state, decisions)

    monkeypatch.setitem(
        sys.modules, "sounddevice",
        _make_fake_sd(config.VAD_FRAME_SAMPLES, len(decisions) + 5),
    )
    _watchdog_shutdown(state)

    pipeline._run_inner()

    assert state.drain_utterances() == []
    assert transcriber.calls == []


def test_loop_marks_started(monkeypatch):
    state = SessionState()
    pipeline = AudioPipeline(state, _FakeTranscriber())

    decisions = [False] * 5

    pipeline._load_vad = lambda: object()
    pipeline._is_speech = _scripted_vad(state, decisions)

    monkeypatch.setitem(
        sys.modules, "sounddevice",
        _make_fake_sd(config.VAD_FRAME_SAMPLES, len(decisions) + 5),
    )
    _watchdog_shutdown(state)

    pipeline._run_inner()

    assert state.wait_until_started(timeout=0.0)


def test_loop_force_cuts_runaway_utterance_at_max_duration(monkeypatch):
    state = SessionState()
    transcriber = _FakeTranscriber("long")
    pipeline = AudioPipeline(state, transcriber)

    # Shrink max duration so the test runs fast.
    monkeypatch.setattr(config, "MAX_UTTERANCE_DURATION_SEC", 1.0)
    monkeypatch.setattr(config, "MIN_UTTERANCE_DURATION_SEC", 0.1)

    max_frames = int(
        config.MAX_UTTERANCE_DURATION_SEC * config.SAMPLE_RATE / config.VAD_FRAME_SAMPLES
    )
    decisions = [True] * (max_frames + 10)  # never stops speaking

    pipeline._load_vad = lambda: object()
    pipeline._is_speech = _scripted_vad(state, decisions)

    monkeypatch.setitem(
        sys.modules, "sounddevice",
        _make_fake_sd(config.VAD_FRAME_SAMPLES, len(decisions) + 5),
    )
    _watchdog_shutdown(state)

    pipeline._run_inner()

    assert state.drain_utterances() == ["long"]
    assert len(transcriber.calls) == 1


def test_loop_two_utterances_with_silence_gap(monkeypatch):
    state = SessionState()

    transcribed: list[str] = []

    class _T:
        def transcribe(self, audio):  # noqa: ANN001
            transcribed.append(f"utt-{len(transcribed) + 1}")
            return transcribed[-1]

    pipeline = AudioPipeline(state, _T())

    silence_to_end = _silence_frames_to_end()
    # Speech, full silence (closes utterance 1), Speech again, full silence (closes utterance 2)
    decisions = (
        [True] * 30 + [False] * (silence_to_end + 3) +
        [True] * 30 + [False] * (silence_to_end + 3)
    )

    pipeline._load_vad = lambda: object()
    pipeline._is_speech = _scripted_vad(state, decisions)

    monkeypatch.setitem(
        sys.modules, "sounddevice",
        _make_fake_sd(config.VAD_FRAME_SAMPLES, len(decisions) + 10),
    )
    _watchdog_shutdown(state, seconds=5.0)

    pipeline._run_inner()

    assert state.drain_utterances() == ["utt-1", "utt-2"]


# Note: the MIN_UTTERANCE_DURATION_SEC filter in _finalize is exercised in
# tests/test_audio_finalize.py, where audio chunks can be sized precisely. In
# the full segmentation loop, preroll (~0.3s) plus the trailing silence window
# (~1.5s) is always added to the speech, so a brief speech burst still produces
# a long enough chunk to pass the filter. That's expected behavior.


def test_loop_aborts_cleanly_if_sounddevice_missing(monkeypatch):
    state = SessionState()
    pipeline = AudioPipeline(state, _FakeTranscriber())

    # Force the import inside _run_inner to fail.
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__  # type: ignore[index]

    def fake_import(name, *args, **kwargs):
        if name == "sounddevice":
            raise ImportError("no portaudio")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)

    pipeline._run_inner()

    # Module should request shutdown rather than crash, and never queue anything.
    assert state.shutdown_requested()
    assert state.drain_utterances() == []
