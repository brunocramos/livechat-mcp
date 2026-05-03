"""Microphone capture, VAD, and utterance segmentation.

A background thread runs continuously while the server is up, independent of
whether Claude is calling the MCP tool. Detected utterances are pushed to the
shared SessionState queue and consumed by the MCP tool handler.
"""

from __future__ import annotations

import sys
import threading
from collections import deque
from typing import Callable, Optional

import numpy as np

from . import config
from .queue_manager import SessionState
from .transcribe import Transcriber


def _debug(msg: str) -> None:
    if config.DEBUG:
        print(f"[livechat:audio] {msg}", file=sys.stderr, flush=True)


class AudioPipeline:
    """Captures mic input, segments into utterances via VAD, transcribes, and queues text."""

    def __init__(
        self,
        state: SessionState,
        transcriber: Transcriber,
        on_stop: Optional[Callable[[], None]] = None,
    ) -> None:
        self.state = state
        self.transcriber = transcriber
        self._on_stop = on_stop
        self._thread: Optional[threading.Thread] = None
        self._vad_model = None
        self._vad_lock = threading.Lock()

    # --- Lifecycle ---

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="livechat-audio", daemon=True)
        self._thread.start()

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    # --- VAD ---

    def _load_vad(self):
        if self._vad_model is not None:
            return self._vad_model
        # silero-vad ships its own loader. Imported lazily to keep startup fast.
        from silero_vad import load_silero_vad

        print("[livechat] loading silero-vad", file=sys.stderr, flush=True)
        self._vad_model = load_silero_vad()
        print("[livechat] silero-vad ready", file=sys.stderr, flush=True)
        return self._vad_model

    def _is_speech(self, frame: np.ndarray) -> bool:
        """frame: float32 numpy array of length VAD_FRAME_SAMPLES."""
        import torch

        model = self._load_vad()
        with self._vad_lock:
            tensor = torch.from_numpy(frame)
            prob = float(model(tensor, config.SAMPLE_RATE).item())
        return prob >= config.VAD_SPEECH_THRESHOLD

    # --- Main loop ---

    def _run(self) -> None:
        try:
            self._run_inner()
        finally:
            if self._on_stop is not None:
                try:
                    self._on_stop()
                except Exception as e:  # noqa: BLE001
                    print(f"[livechat] on_stop callback failed: {e}", file=sys.stderr, flush=True)

    def _run_inner(self) -> None:
        try:
            import sounddevice as sd
        except Exception as e:
            print(
                f"[livechat] FATAL: could not import sounddevice ({e}). "
                "On macOS make sure portaudio is installed (brew install portaudio).",
                file=sys.stderr,
                flush=True,
            )
            self.state.request_shutdown()
            return

        # Pre-load VAD now so the first utterance is not delayed by load time.
        try:
            self._load_vad()
        except Exception as e:
            print(f"[livechat] FATAL: VAD load failed: {e}", file=sys.stderr, flush=True)
            self.state.request_shutdown()
            return

        # State for utterance segmentation
        in_speech = False
        current_audio: list[np.ndarray] = []
        silence_frames = 0
        silence_frames_to_end = int(
            (config.SILENCE_TO_END_UTTERANCE_SEC * config.SAMPLE_RATE)
            / config.VAD_FRAME_SAMPLES
        )
        max_utterance_frames = int(
            (config.MAX_UTTERANCE_DURATION_SEC * config.SAMPLE_RATE)
            / config.VAD_FRAME_SAMPLES
        )
        # Pre-roll: keep the last ~0.3s of audio so we don't clip the start of an utterance.
        preroll_frames = max(1, int(0.3 * config.SAMPLE_RATE / config.VAD_FRAME_SAMPLES))
        preroll: deque[np.ndarray] = deque(maxlen=preroll_frames)

        # Frame-level callback queue: sounddevice's callback runs in a separate thread,
        # so we use a queue to hand frames over to this loop.
        import queue as _queue

        frame_q: _queue.Queue[np.ndarray] = _queue.Queue()

        def _callback(indata, frames, time_info, status):  # noqa: ANN001
            if status:
                _debug(f"sounddevice status: {status}")
            # indata is shape (frames, channels). We want mono float32.
            mono = indata[:, 0].astype(np.float32, copy=True)
            frame_q.put(mono)

        try:
            with sd.InputStream(
                samplerate=config.SAMPLE_RATE,
                channels=config.CHANNELS,
                dtype="float32",
                blocksize=config.VAD_FRAME_SAMPLES,
                callback=_callback,
            ):
                self.state.mark_started()
                print(
                    f"[livechat] mic open: {config.SAMPLE_RATE}Hz mono, "
                    f"frame={config.VAD_FRAME_SAMPLES} samples",
                    file=sys.stderr,
                    flush=True,
                )

                while not self.state.shutdown_requested():
                    try:
                        frame = frame_q.get(timeout=0.2)
                    except _queue.Empty:
                        continue

                    # Sounddevice may give us larger blocks if the OS coalesces; chop to VAD size.
                    for sub in _split_frames(frame, config.VAD_FRAME_SAMPLES):
                        if self.state.shutdown_requested():
                            break
                        try:
                            speech = self._is_speech(sub)
                        except Exception as e:
                            _debug(f"VAD error: {e}")
                            speech = False

                        if speech:
                            if not in_speech:
                                # Starting a new utterance — flush pre-roll into it.
                                in_speech = True
                                current_audio = list(preroll)
                                _debug("speech started")
                            current_audio.append(sub)
                            silence_frames = 0
                        else:
                            preroll.append(sub)
                            if in_speech:
                                current_audio.append(sub)
                                silence_frames += 1
                                if silence_frames >= silence_frames_to_end:
                                    self._finalize(current_audio)
                                    in_speech = False
                                    current_audio = []
                                    silence_frames = 0

                        # Force-cut runaway utterances
                        if in_speech and len(current_audio) >= max_utterance_frames:
                            _debug("max utterance length hit, force-finalizing")
                            self._finalize(current_audio)
                            in_speech = False
                            current_audio = []
                            silence_frames = 0
        except Exception as e:
            print(f"[livechat] audio loop crashed: {e}", file=sys.stderr, flush=True)
            self.state.request_shutdown()

    def _finalize(self, frames: list[np.ndarray]) -> None:
        """Concatenate, transcribe, and push to the queue if non-empty."""
        if not frames:
            return
        audio = np.concatenate(frames)
        duration = len(audio) / config.SAMPLE_RATE
        if duration < config.MIN_UTTERANCE_DURATION_SEC:
            _debug(f"dropping short utterance ({duration:.2f}s)")
            return

        _debug(f"finalizing utterance: {duration:.2f}s")
        text = self.transcriber.transcribe(audio)
        if not text:
            _debug("empty transcription, skipping")
            return

        # Wake-phrase check: if the user said the end phrase, request shutdown.
        normalized = _normalize(text)
        if config.WAKE_PHRASE_END and config.WAKE_PHRASE_END in normalized:
            print(
                f"[livechat] wake phrase detected, requesting shutdown",
                file=sys.stderr,
                flush=True,
            )
            self.state.request_shutdown()
            return

        print(f"[livechat] utterance: {text}", file=sys.stderr, flush=True)
        self.state.push_utterance(text)


def _split_frames(buf: np.ndarray, size: int) -> list[np.ndarray]:
    """Split a numpy 1D array into chunks of exactly `size` samples. Drops trailing remainder."""
    n = (len(buf) // size) * size
    if n == 0:
        return []
    trimmed = buf[:n]
    return [trimmed[i : i + size] for i in range(0, n, size)]


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace — for wake-phrase matching."""
    import re

    cleaned = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    return re.sub(r"\s+", " ", cleaned).strip()
