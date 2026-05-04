"""Unit tests for AudioPipeline._finalize — the part that runs after VAD has cut
an utterance: minimum-duration filter, transcription, wake-phrase detection, and
push to the queue."""

from __future__ import annotations

from typing import Optional

import numpy as np

from livechat_mcp import config
from livechat_mcp.audio import AudioPipeline
from livechat_mcp.queue_manager import SessionState


class _FakeTranscriber:
    def __init__(self, text: Optional[str]) -> None:
        self.text = text
        self.calls = 0

    def transcribe(self, audio: np.ndarray) -> Optional[str]:
        self.calls += 1
        return self.text


def _audio_seconds(seconds: float) -> list[np.ndarray]:
    """Return a single-frame list of zeros sized to `seconds` of audio."""
    n = int(seconds * config.SAMPLE_RATE)
    return [np.zeros(n, dtype=np.float32)]


def test_finalize_drops_short_utterance_without_transcribing():
    state = SessionState()
    t = _FakeTranscriber("ignored")
    pipeline = AudioPipeline(state, t)

    pipeline._finalize(_audio_seconds(0.1))  # below MIN_UTTERANCE_DURATION_SEC

    assert state.drain_utterances() == []
    assert t.calls == 0


def test_finalize_pushes_transcribed_text():
    state = SessionState()
    t = _FakeTranscriber("hello world")
    pipeline = AudioPipeline(state, t)

    pipeline._finalize(_audio_seconds(1.0))

    assert state.drain_utterances() == ["hello world"]
    assert t.calls == 1


def test_finalize_skips_when_transcription_is_none():
    state = SessionState()
    t = _FakeTranscriber(None)
    pipeline = AudioPipeline(state, t)

    pipeline._finalize(_audio_seconds(1.0))

    assert state.drain_utterances() == []
    assert t.calls == 1


def test_finalize_wake_phrase_requests_shutdown_and_does_not_push():
    state = SessionState()
    t = _FakeTranscriber("Okay, TERMINATE voice session now.")
    pipeline = AudioPipeline(state, t)

    pipeline._finalize(_audio_seconds(2.0))

    assert state.shutdown_requested()
    assert state.drain_utterances() == []


def test_finalize_no_op_on_empty_frame_list():
    state = SessionState()
    t = _FakeTranscriber("never called")
    pipeline = AudioPipeline(state, t)

    pipeline._finalize([])

    assert state.drain_utterances() == []
    assert t.calls == 0


# --- language handling in Transcriber -----------------------------------------


def test_transcriber_passes_language_string_through(monkeypatch):
    from livechat_mcp import config as cfg
    from livechat_mcp.transcribe import Transcriber

    monkeypatch.setattr(cfg, "WHISPER_LANGUAGE", "pt")

    captured = {}

    class FakeModel:
        def transcribe(self, audio, **kw):
            captured.update(kw)
            return iter([]), None

    t = Transcriber()
    t._model = FakeModel()  # bypass _load
    t.transcribe(np.zeros(16_000, dtype=np.float32))

    assert captured["language"] == "pt"


def test_transcriber_treats_auto_as_none(monkeypatch):
    from livechat_mcp import config as cfg
    from livechat_mcp.transcribe import Transcriber

    monkeypatch.setattr(cfg, "WHISPER_LANGUAGE", "auto")

    captured = {}

    class FakeModel:
        def transcribe(self, audio, **kw):
            captured.update(kw)
            return iter([]), None

    t = Transcriber()
    t._model = FakeModel()
    t.transcribe(np.zeros(16_000, dtype=np.float32))

    assert captured["language"] is None


def test_transcriber_treats_empty_as_none(monkeypatch):
    from livechat_mcp import config as cfg
    from livechat_mcp.transcribe import Transcriber

    monkeypatch.setattr(cfg, "WHISPER_LANGUAGE", "")

    captured = {}

    class FakeModel:
        def transcribe(self, audio, **kw):
            captured.update(kw)
            return iter([]), None

    t = Transcriber()
    t._model = FakeModel()
    t.transcribe(np.zeros(16_000, dtype=np.float32))

    assert captured["language"] is None


def test_transcriber_auto_case_insensitive(monkeypatch):
    from livechat_mcp import config as cfg
    from livechat_mcp.transcribe import Transcriber

    monkeypatch.setattr(cfg, "WHISPER_LANGUAGE", "AUTO")

    captured = {}

    class FakeModel:
        def transcribe(self, audio, **kw):
            captured.update(kw)
            return iter([]), None

    t = Transcriber()
    t._model = FakeModel()
    t.transcribe(np.zeros(16_000, dtype=np.float32))

    assert captured["language"] is None
