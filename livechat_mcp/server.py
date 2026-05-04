"""MCP server entry point.

Exposes three tools:
- get_voice_input: returns next utterance(s), long-polls if queue is empty.
- end_voice_session: cleanly ends the session.
- take_over_voice_session: signals a sibling instance to release the lock.

stdio transport. All logging to stderr; stdout is reserved for MCP protocol.
"""

from __future__ import annotations

import asyncio
import re
import signal
import sys
import time
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import GetPromptResult, Prompt, PromptMessage, TextContent, Tool

from . import config
from .audio import AudioPipeline
from .lockfile import SessionLock, signal_holder_to_shutdown
from .queue_manager import SessionState
from .transcribe import Transcriber


def _log(msg: str) -> None:
    print(f"[livechat] {msg}", file=sys.stderr, flush=True)


_PROMPT_DESCRIPTIONS = {
    "livechat": "Start a live voice review session. Speak instead of typing.",
    "endlivechat": "End the active live voice session and summarize.",
}


def _commands_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "commands"


def _load_prompt_body(name: str) -> str:
    if name not in _PROMPT_DESCRIPTIONS:
        raise ValueError(f"Unknown prompt: {name}")

    path = _commands_dir() / f"{name}.md"
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\s*\n.*?\n---\s*\n(.*)$", text, re.DOTALL)
    if match:
        return match.group(1).lstrip("\n")
    return text


async def _run() -> None:
    state = SessionState()
    transcriber = Transcriber()
    lock = SessionLock()
    pipeline = AudioPipeline(state, transcriber, on_stop=lock.release)

    # Audio pipeline is NOT started at boot — only when the user actually begins
    # a /livechat session. This keeps the mic closed for Claude Code windows that
    # have the MCP server installed but rarely use voice mode, and lets the
    # cross-process lock gate microphone capture.

    # SIGINT / SIGTERM → request shutdown so get_voice_input returns __END_SESSION__.
    # Cross-process takeover (formerly SIGUSR1) is now a file-marker mechanism
    # handled in audio.py — works on all platforms including Windows.
    loop = asyncio.get_running_loop()

    def _on_signal(signame: str) -> None:
        _log(f"received {signame}, requesting shutdown")
        state.request_shutdown()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _on_signal, signal.Signals(sig).name)
        except NotImplementedError:
            # Windows: asyncio's add_signal_handler is unsupported on the proactor
            # loop. Plain signal.signal still wakes the event loop.
            signal.signal(sig, lambda *_: state.request_shutdown())

    server: Server = Server("livechat-mcp")

    @server.list_prompts()
    async def list_prompts() -> list[Prompt]:
        return [
            Prompt(
                name=name,
                title=name,
                description=description,
                arguments=[],
            )
            for name, description in _PROMPT_DESCRIPTIONS.items()
        ]

    def _reset_if_idle() -> bool:
        """Reset state IF there's no healthy session running.

        - Pipeline alive AND no shutdown pending → healthy session, no-op
          (we must not drop in-flight utterances or kick the audio thread).
        - Otherwise (pipeline dead, or shutdown was requested by /endlivechat)
          → clear shutdown flag and queue so a fresh session can start.

        Returns True if a reset was performed, False if it was a no-op.
        """
        if state.shutdown_requested() or not pipeline.is_alive():
            state.reset_for_new_session()
            return True
        return False

    @server.get_prompt()
    async def get_prompt(name: str, arguments: dict | None) -> GetPromptResult:
        if name == "livechat" and _reset_if_idle():
            _log("livechat prompt requested; cleared stale state")
        del arguments
        return GetPromptResult(
            description=_PROMPT_DESCRIPTIONS.get(name),
            messages=[
                PromptMessage(
                    role="user",
                    content=TextContent(type="text", text=_load_prompt_body(name)),
                )
            ],
        )

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="get_voice_input",
                description=(
                    "Returns the next voice utterance from the user as text. "
                    "Used in a loop during voice review sessions. "
                    "If multiple utterances are queued, they are joined with ' / '. "
                    "Returns the literal string '__END_SESSION__' when the user has "
                    "ended the session (Ctrl+C, /endlivechat, or wake phrase) — "
                    "stop calling this tool when you see that. "
                    "Returns '__NO_INPUT__' if the long-poll timed out with no speech; "
                    "in that case, call this tool again. "
                    "Returns '__ALREADY_RUNNING__:<pid>' if another livechat MCP "
                    "process (e.g. another Claude Code window) currently holds the "
                    "session lock — ask the user to confirm a takeover, then call "
                    "take_over_voice_session if they agree."
                ),
                inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
            ),
            Tool(
                name="end_voice_session",
                description=(
                    "Cleanly end the current voice session. After calling this, "
                    "any further get_voice_input calls will return '__END_SESSION__'. "
                    "Use this when the user invokes /endlivechat or otherwise asks "
                    "to stop voice mode."
                ),
                inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
            ),
            Tool(
                name="take_over_voice_session",
                description=(
                    "Forcibly take the cross-process session lock from another "
                    "livechat MCP instance. Signals the holder to release, waits "
                    "briefly, and starts a new session here. Only call this after "
                    "the user explicitly confirms taking over from the other window. "
                    "Returns 'OK' on success or an error string on failure."
                ),
                inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
            ),
            Tool(
                name="reset_voice_session",
                description=(
                    "Clear stale shutdown state from a previous /endlivechat in "
                    "this same MCP server process so a new voice session can "
                    "start cleanly. Call this once at the very beginning of a "
                    "/livechat session, after the announcement and before the "
                    "first get_voice_input. Safe to call mid-session: if a "
                    "session is already running healthily this is a no-op and "
                    "no in-flight utterances are dropped."
                ),
                inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
            ),
        ]

    call_seq = {"n": 0}

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        call_seq["n"] += 1
        n = call_seq["n"]
        t0 = time.monotonic()
        _log(f"tool call #{n} → {name}")
        if name == "get_voice_input":
            text = await _get_voice_input(state, pipeline, lock)
            elapsed_ms = (time.monotonic() - t0) * 1000
            preview = text if len(text) <= 60 else text[:57] + "..."
            _log(f"tool call #{n} ← {name} {elapsed_ms:.0f}ms {preview!r}")
            return [TextContent(type="text", text=text)]
        if name == "end_voice_session":
            state.request_shutdown()
            _log(f"tool call #{n} ← {name} (shutdown requested)")
            return [TextContent(type="text", text="Voice session ended.")]
        if name == "reset_voice_session":
            did_reset = _reset_if_idle()
            msg = (
                "Voice session reset; ready for a new session."
                if did_reset
                else "Voice session is active; nothing to reset."
            )
            _log(f"tool call #{n} ← {name} ({'reset' if did_reset else 'no-op'})")
            return [TextContent(type="text", text=msg)]
        if name == "take_over_voice_session":
            result = await _take_over(state, pipeline, lock)
            _log(f"tool call #{n} ← {name} {result!r}")
            return [TextContent(type="text", text=result)]
        _log(f"tool call #{n} ← unknown tool: {name}")
        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    async with stdio_server() as (read, write):
        _log("MCP server ready (stdio)")
        await server.run(read, write, server.create_initialization_options())

    # Cleanup
    state.request_shutdown()
    pipeline.join(timeout=2.0)
    _log("server exited")


async def _get_voice_input(
    state: SessionState, pipeline: AudioPipeline, lock: SessionLock
) -> str:
    """Drain queued utterances or long-poll for a new one."""
    if state.shutdown_requested():
        return config.SENTINEL_END_SESSION

    # If no audio thread is running, this is either the first call ever or the
    # first call after a previous /endlivechat.
    if not pipeline.is_alive():
        holder = lock.try_acquire()
        if holder is not None:
            _log(f"session lock held by PID {holder}; refusing to start audio")
            return f"{config.SENTINEL_ALREADY_RUNNING}:{holder}"
        _log("starting new session: audio pipeline up")
        pipeline.start()

    # Drain any queued utterances first (non-blocking).
    drained = state.drain_utterances()
    if drained:
        return config.UTTERANCE_JOIN_SEPARATOR.join(drained)

    # Otherwise, long-poll. We poll on a tight interval so we can respond quickly
    # to shutdown requests without blocking the asyncio loop.
    deadline_left = config.LONG_POLL_TIMEOUT_SEC
    interval = config.QUEUE_POLL_INTERVAL_SEC

    while deadline_left > 0:
        if state.shutdown_requested():
            return config.SENTINEL_END_SESSION
        # Check the queue without blocking the event loop.
        drained = state.drain_utterances()
        if drained:
            return config.UTTERANCE_JOIN_SEPARATOR.join(drained)
        await asyncio.sleep(interval)
        deadline_left -= interval

    return config.SENTINEL_NO_INPUT


async def _take_over(
    state: SessionState, pipeline: AudioPipeline, lock: SessionLock
) -> str:
    """Forcibly take the session lock from another livechat process."""
    if pipeline.is_alive():
        return "OK (already holding the session)"

    if state.shutdown_requested():
        state.reset_for_new_session()

    # See who currently holds it.
    holder = lock.try_acquire()
    if holder is None:
        pipeline.start()
        return "OK"

    _log(f"takeover requested: signalling PID {holder} to release")
    if not signal_holder_to_shutdown(holder):
        return f"failed to signal holder PID {holder} (process gone or no permission)"

    # Wait for the holder to release. Their pipeline thread needs a moment to
    # finish closing the InputStream and call on_stop → release.
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        await asyncio.sleep(0.1)
        holder = lock.try_acquire()
        if holder is None:
            pipeline.start()
            return "OK"

    return f"holder PID {holder} did not release within 3s"


def main() -> None:
    """Console-script entry point."""
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        _log("interrupted")


if __name__ == "__main__":
    main()
