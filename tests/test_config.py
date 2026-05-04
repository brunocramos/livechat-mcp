"""config.py reads env vars at import time, so tests reload the module after
setting the relevant vars."""

from __future__ import annotations

import importlib

import pytest


def _reload_config():
    import livechat_mcp.config as c
    return importlib.reload(c)


@pytest.fixture(autouse=True)
def restore_config():
    yield
    # Always leave the imported module in its default-env state for the rest of the suite.
    _reload_config()


def test_defaults_match_documented_values(monkeypatch):
    # Strip any LIVECHAT_* overrides that might be set in the user's environment
    # so we can assert against the documented defaults.
    for key in list(__import__("os").environ):
        if key.startswith("LIVECHAT_"):
            monkeypatch.delenv(key, raising=False)
    c = _reload_config()
    assert c.SAMPLE_RATE == 16_000
    assert c.CHANNELS == 1
    assert c.VAD_FRAME_SAMPLES == 512
    assert c.VAD_SPEECH_THRESHOLD == 0.5
    assert c.SILENCE_TO_END_UTTERANCE_SEC == 1.5
    assert c.MIN_UTTERANCE_DURATION_SEC == 0.4
    assert c.MAX_UTTERANCE_DURATION_SEC == 120
    assert c.WHISPER_MODEL == "base.en"
    assert c.LONG_POLL_TIMEOUT_SEC == 300
    assert c.UTTERANCE_JOIN_SEPARATOR == " / "
    assert c.WAKE_PHRASE_END == "terminate voice session now"
    assert c.SENTINEL_END_SESSION == "__END_SESSION__"
    assert c.SENTINEL_NO_INPUT == "__NO_INPUT__"
    assert c.SENTINEL_ALREADY_RUNNING == "__ALREADY_RUNNING__"
    assert c.DEBUG is False


def test_env_overrides_apply(monkeypatch):
    monkeypatch.setenv("LIVECHAT_SILENCE_SEC", "2.5")
    monkeypatch.setenv("LIVECHAT_WHISPER_MODEL", "small.en")
    monkeypatch.setenv("LIVECHAT_VAD_THRESHOLD", "0.3")
    monkeypatch.setenv("LIVECHAT_DEBUG", "1")
    monkeypatch.setenv("LIVECHAT_END_PHRASE", "  Bye Bye  ")
    monkeypatch.setenv("LIVECHAT_LONG_POLL_SEC", "60")

    c = _reload_config()

    assert c.SILENCE_TO_END_UTTERANCE_SEC == 2.5
    assert c.WHISPER_MODEL == "small.en"
    assert c.VAD_SPEECH_THRESHOLD == 0.3
    assert c.DEBUG is True
    assert c.WAKE_PHRASE_END == "bye bye"  # lowercased and stripped
    assert c.LONG_POLL_TIMEOUT_SEC == 60


@pytest.mark.parametrize("value,expected", [
    ("1", True),
    ("true", True),
    ("YES", True),
    ("True", True),
    ("0", False),
    ("false", False),
    ("", False),
    ("anything-else", False),
])
def test_debug_flag_truthiness(monkeypatch, value, expected):
    monkeypatch.setenv("LIVECHAT_DEBUG", value)
    c = _reload_config()
    assert c.DEBUG is expected
