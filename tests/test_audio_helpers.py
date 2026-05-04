from __future__ import annotations

import numpy as np

from livechat_mcp.audio import _normalize, _split_frames


# --- _split_frames ---


def test_split_frames_exact_multiple():
    buf = np.arange(20, dtype=np.float32)
    chunks = _split_frames(buf, 5)
    assert len(chunks) == 4
    assert all(len(c) == 5 for c in chunks)
    assert chunks[0][0] == 0 and chunks[-1][-1] == 19


def test_split_frames_drops_trailing_remainder():
    buf = np.arange(13, dtype=np.float32)
    chunks = _split_frames(buf, 5)
    assert len(chunks) == 2
    assert all(len(c) == 5 for c in chunks)
    # samples 10, 11, 12 dropped


def test_split_frames_returns_empty_when_buf_too_short():
    buf = np.arange(3, dtype=np.float32)
    assert _split_frames(buf, 5) == []


def test_split_frames_empty_input():
    assert _split_frames(np.array([], dtype=np.float32), 5) == []


# --- _normalize (used for wake-phrase matching) ---


def test_normalize_lowercases_and_strips_punctuation():
    assert _normalize("Hello, World!") == "hello world"


def test_normalize_collapses_whitespace():
    assert _normalize("foo   bar\tbaz\n") == "foo bar baz"


def test_normalize_keeps_alphanumeric():
    assert _normalize("CALL 911-NOW") == "call 911 now"


def test_normalize_handles_empty():
    assert _normalize("") == ""


def test_normalize_pure_punctuation_becomes_empty():
    assert _normalize("!!!...???") == ""


def test_wake_phrase_substring_match():
    # Verifies the "in" check the audio module uses works after normalization.
    spoken = "Okay,  please TERMINATE voice session NOW. thanks!"
    assert "terminate voice session now" in _normalize(spoken)
