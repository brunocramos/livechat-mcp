"""Whisper wrapper. Loads the model once, transcribes audio chunks on demand."""

from __future__ import annotations

import sys
from typing import Optional

import numpy as np

from . import config


class Transcriber:
    """Lazy-loaded faster-whisper wrapper."""

    def __init__(self) -> None:
        self._model = None  # loaded lazily on first transcribe()

    def _load(self) -> None:
        if self._model is not None:
            return
        # Imported here so a missing/slow torch import does not delay server startup
        # for users who never trigger transcription (e.g. testing tool registration).
        from faster_whisper import WhisperModel

        device = config.WHISPER_DEVICE
        if device == "auto":
            try:
                import torch

                device = "cuda" if torch.cuda.is_available() else "cpu"
            except Exception:
                device = "cpu"

        compute_type = config.WHISPER_COMPUTE_TYPE
        # On CPU, int8 is fastest. On CUDA, float16 is best. Auto-correct if user left default.
        if device == "cpu" and compute_type == "float16":
            compute_type = "int8"

        print(
            f"[livechat] loading whisper model={config.WHISPER_MODEL} "
            f"device={device} compute={compute_type}",
            file=sys.stderr,
            flush=True,
        )
        self._model = WhisperModel(
            config.WHISPER_MODEL,
            device=device,
            compute_type=compute_type,
        )
        print("[livechat] whisper model ready", file=sys.stderr, flush=True)

    def transcribe(self, audio: np.ndarray) -> Optional[str]:
        """Transcribe a 16kHz mono float32 numpy array. Returns text or None."""
        self._load()
        assert self._model is not None

        # faster-whisper accepts numpy arrays of float32 in [-1, 1].
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)

        # "auto" or unset → pass None so Whisper auto-detects per utterance.
        # Only works with multilingual models (e.g. "base" without .en suffix).
        lang = config.WHISPER_LANGUAGE
        language = None if not lang or lang.lower() == "auto" else lang

        try:
            segments, _info = self._model.transcribe(
                audio,
                language=language,
                vad_filter=False,  # we already segment via Silero VAD upstream
                beam_size=1,  # speed > marginal accuracy for review speech
                condition_on_previous_text=False,  # avoid hallucinated continuations
            )
            text = "".join(seg.text for seg in segments).strip()
            return text or None
        except Exception as e:
            print(f"[livechat] transcription error: {e}", file=sys.stderr, flush=True)
            return None
