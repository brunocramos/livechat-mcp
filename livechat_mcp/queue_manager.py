"""Thread-safe utterance queue and shutdown coordination.

The queue persists utterances across tool calls. Audio capture is decoupled
from MCP tool calls, so utterances spoken while Claude is generating a
response are still captured and queued.
"""

from __future__ import annotations

import queue
import threading
from typing import Optional


class SessionState:
    """Shared state between the audio pipeline and the MCP tool handlers."""

    def __init__(self) -> None:
        self._utterances: queue.Queue[str] = queue.Queue()
        self._shutdown = threading.Event()
        self._started = threading.Event()

    # --- Utterance queue ---

    def push_utterance(self, text: str) -> None:
        """Called by the transcription worker."""
        text = text.strip()
        if text:
            self._utterances.put(text)

    def drain_utterances(self) -> list[str]:
        """Non-blocking. Returns all currently-queued utterances and clears the queue."""
        items: list[str] = []
        while True:
            try:
                items.append(self._utterances.get_nowait())
            except queue.Empty:
                break
        return items

    def get_utterance_blocking(self, timeout: Optional[float]) -> Optional[str]:
        """Blocks until an utterance appears or timeout is hit. Returns None on timeout."""
        try:
            return self._utterances.get(timeout=timeout)
        except queue.Empty:
            return None

    # --- Shutdown signaling ---

    def request_shutdown(self) -> None:
        self._shutdown.set()

    def shutdown_requested(self) -> bool:
        return self._shutdown.is_set()

    def reset_for_new_session(self) -> None:
        """Clear shutdown/started flags and discard any stale queued utterances
        so a new session can begin on the same long-lived MCP server."""
        self._shutdown.clear()
        self._started.clear()
        while True:
            try:
                self._utterances.get_nowait()
            except queue.Empty:
                break

    def wait_for_shutdown(self, timeout: Optional[float] = None) -> bool:
        return self._shutdown.wait(timeout=timeout)

    # --- Startup signaling (so the MCP server can wait for the audio pipeline) ---

    def mark_started(self) -> None:
        self._started.set()

    def wait_until_started(self, timeout: Optional[float] = None) -> bool:
        return self._started.wait(timeout=timeout)
