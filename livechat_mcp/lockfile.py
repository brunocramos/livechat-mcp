"""Cross-process lock for the active livechat session.

Two MCP server processes (e.g. two Claude Code windows) both opening the mic
produces garbage on both sides. We acquire an `fcntl.flock` on a well-known
path while a session is active, and write our PID inside so the other process
can name the holder when reporting the conflict.

The kernel auto-releases the lock when the holding process exits, so a
crashed MCP server does not permanently block future sessions.
"""

from __future__ import annotations

import errno
import fcntl
import os
import signal
from pathlib import Path
from typing import Optional


def _lock_path() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or os.path.expanduser("~/.cache")
    d = Path(base) / "livechat-mcp"
    d.mkdir(parents=True, exist_ok=True)
    return d / "session.lock"


class SessionLock:
    """Best-effort exclusion: only one livechat session at a time across processes."""

    def __init__(self) -> None:
        self._fd: Optional[int] = None
        self._path = _lock_path()

    def try_acquire(self) -> Optional[int]:
        """Attempt to acquire the lock.

        Returns None on success (lock now held by us, or already was).
        Returns the holder's PID on conflict.
        """
        if self._fd is not None:
            return None
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            if e.errno not in (errno.EWOULDBLOCK, errno.EAGAIN):
                os.close(fd)
                raise
            holder = _read_pid(self._path)
            os.close(fd)
            return holder if holder > 0 else -1
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
        self._fd = fd
        return None

    def release(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(fd)
        except OSError:
            pass

    def held(self) -> bool:
        return self._fd is not None


def _read_pid(path: Path) -> int:
    try:
        with open(path) as f:
            return int(f.read().strip() or 0)
    except (OSError, ValueError):
        return 0


def signal_holder_to_shutdown(pid: int) -> bool:
    """Best-effort: send SIGUSR1 to the lock holder so it gracefully releases."""
    try:
        os.kill(pid, signal.SIGUSR1)
        return True
    except (ProcessLookupError, PermissionError):
        return False
