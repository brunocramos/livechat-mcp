"""Tunable configuration. Override via environment variables where supported."""

from __future__ import annotations

import os

# --- Audio capture ---
SAMPLE_RATE: int = 16_000  # Whisper expects 16kHz
CHANNELS: int = 1
# Frame size for VAD. Silero works on 512-sample frames at 16kHz (32ms).
VAD_FRAME_SAMPLES: int = 512

# --- VAD / segmentation ---
# Probability threshold above which a frame is considered speech.
VAD_SPEECH_THRESHOLD: float = float(os.getenv("LIVECHAT_VAD_THRESHOLD", "0.5"))
# Seconds of continuous silence after speech to treat as end-of-utterance.
SILENCE_TO_END_UTTERANCE_SEC: float = float(os.getenv("LIVECHAT_SILENCE_SEC", "1.5"))
# Minimum utterance duration to bother transcribing (filters coughs, "uh", etc.).
MIN_UTTERANCE_DURATION_SEC: float = float(os.getenv("LIVECHAT_MIN_UTTERANCE_SEC", "0.4"))
# Maximum utterance duration before forced cut (in case VAD thinks you never stopped).
MAX_UTTERANCE_DURATION_SEC: float = float(os.getenv("LIVECHAT_MAX_UTTERANCE_SEC", "120"))

# --- Whisper ---
# Model size: tiny.en, base.en, small.en, medium.en. base.en is the sweet spot.
WHISPER_MODEL: str = os.getenv("LIVECHAT_WHISPER_MODEL", "base.en")
# "auto" will pick CUDA if available, otherwise CPU. On Mac, this is CPU.
WHISPER_DEVICE: str = os.getenv("LIVECHAT_WHISPER_DEVICE", "auto")
# int8 is fast and accurate enough for English. Use "float16" on GPU.
WHISPER_COMPUTE_TYPE: str = os.getenv("LIVECHAT_WHISPER_COMPUTE", "int8")
WHISPER_LANGUAGE: str = os.getenv("LIVECHAT_WHISPER_LANGUAGE", "en")

# --- MCP tool behavior ---
# How long get_voice_input blocks waiting for an utterance before returning __NO_INPUT__.
LONG_POLL_TIMEOUT_SEC: float = float(os.getenv("LIVECHAT_LONG_POLL_SEC", "300"))
# How often the long-poll wakes up to check the queue.
QUEUE_POLL_INTERVAL_SEC: float = 0.1
# Separator used when joining multiple queued utterances into one return value.
UTTERANCE_JOIN_SEPARATOR: str = " / "

# --- Session lifecycle ---
# Highly distinctive phrase to end the session via voice. Multi-word, awkward, unlikely
# to appear naturally in code review. User should also have /endlivechat available.
WAKE_PHRASE_END: str = os.getenv(
    "LIVECHAT_END_PHRASE", "terminate voice session now"
).lower().strip()

# Sentinel return values from get_voice_input. The slash command tells Claude what these mean.
SENTINEL_END_SESSION: str = "__END_SESSION__"
SENTINEL_NO_INPUT: str = "__NO_INPUT__"
# Returned (with a `:<pid>` suffix) when another livechat MCP process holds the session lock.
SENTINEL_ALREADY_RUNNING: str = "__ALREADY_RUNNING__"

# --- Debug ---
DEBUG: bool = os.getenv("LIVECHAT_DEBUG", "").lower() in {"1", "true", "yes"}
