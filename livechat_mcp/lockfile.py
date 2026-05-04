"""Cross-process lock for the active livechat session, plus the cross-process
takeover signal.

Two MCP server processes (e.g. two Claude Code windows) both opening the mic
produces garbage on both sides. We acquire an OS-level lock on a well-known
path while a session is active, and write our PID inside so the other process
can name the holder when reporting the conflict.

Locking dispatches by platform:
- POSIX (macOS, Linux): `fcntl.flock` — auto-released when the holding fd
  closes, including on process crash.
- Windows: `msvcrt.locking` — same semantics; the OS releases on handle close.

Takeover signaling is file-based on every platform (no SIGUSR1) so it works on
Windows too. The taker writes the holder's PID into a "release request" marker
file; the holder polls the marker on every audio frame and shuts down when it
sees its own PID.
"""

from __future__ import annotations

import errno
import os
import sys
from pathlib import Path
from typing import Optional

_IS_WINDOWS = sys.platform == "win32"

# On Windows, msvcrt.locking is a *mandatory* byte-range lock — it blocks
# reads on the locked byte as well as writes. We lock at a sentinel offset
# well past where we ever write the PID so other processes can still read
# the PID at offset 0.
_WIN_LOCK_OFFSET = 1024

if _IS_WINDOWS:
    import msvcrt
else:
    import fcntl


# --- Platform-dispatched primitive lock ops --------------------------------

def _try_lock_fd(fd: int) -> bool:
    """Non-blocking exclusive lock on `fd`. Returns True on acquire, False on conflict."""
    if _IS_WINDOWS:
        try:
            os.lseek(fd, _WIN_LOCK_OFFSET, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            # Either the lock is held by another fd or the OS refused for
            # another reason. Treat as conflict; caller decides how to react.
            return False
    else:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError as e:
            if e.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
                return False
            raise


def _unlock_fd(fd: int) -> None:
    if _IS_WINDOWS:
        try:
            os.lseek(fd, _WIN_LOCK_OFFSET, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass


# --- Paths -----------------------------------------------------------------

def _runtime_base() -> Path:
    """Pick a writable per-user runtime directory across platforms."""
    # Honor XDG_RUNTIME_DIR if set (Linux convention; respected by tests too).
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        return Path(xdg)
    if _IS_WINDOWS:
        # %LOCALAPPDATA% is the canonical per-user, machine-local cache root.
        local = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if local:
            return Path(local)
        return Path(os.path.expanduser("~")) / "AppData" / "Local"
    return Path(os.path.expanduser("~/.cache"))


def _lock_dir() -> Path:
    d = _runtime_base() / "livechat-mcp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _lock_path() -> Path:
    return _lock_dir() / "session.lock"


def _release_marker_path() -> Path:
    return _lock_dir() / "release.req"


# --- SessionLock -----------------------------------------------------------

class SessionLock:
    """Best-effort exclusion: only one livechat session at a time across processes."""

    def __init__(self) -> None:
        self._fd: Optional[int] = None
        self._path = _lock_path()

    def try_acquire(self) -> Optional[int]:
        """Attempt to acquire the lock.

        Returns None on success (lock held now, or already was).
        Returns the holder's PID on conflict.
        """
        if self._fd is not None:
            return None
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            acquired = _try_lock_fd(fd)
        except OSError:
            os.close(fd)
            raise

        if not acquired:
            holder = _read_pid(self._path)
            os.close(fd)
            return holder if holder > 0 else -1

        # Write our PID at offset 0 so other processes can identify us on conflict.
        # Truncating may fail on Windows while the lock is held in some configs;
        # ignore that and overwrite — _read_pid only reads the first line.
        try:
            os.ftruncate(fd, 0)
        except OSError:
            pass
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, f"{os.getpid()}\n".encode())
        self._fd = fd
        return None

    def release(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        _unlock_fd(fd)
        try:
            os.close(fd)
        except OSError:
            pass

    def held(self) -> bool:
        return self._fd is not None


def _read_pid(path: Path) -> int:
    try:
        with open(path) as f:
            # Only the first line — when ftruncate is rejected (Windows can
            # refuse it under an active lock), the file may still contain
            # bytes from a previous holder past our newly written PID.
            first = f.readline().strip()
        return int(first or 0)
    except (OSError, ValueError):
        return 0


# --- Cross-platform takeover signaling -------------------------------------

def signal_holder_to_shutdown(pid: int) -> bool:
    """Ask the current lock holder to release.

    Implemented as a marker file `release.req` containing the holder PID. The
    holder polls this on every audio frame (see audio.py) and requests shutdown
    when it sees its own PID. This is portable across macOS, Linux, and
    Windows — no SIGUSR1 dependency.

    Returns True if the marker was written, False on filesystem error.
    """
    marker = _release_marker_path()
    try:
        marker.write_text(f"{pid}\n", encoding="utf-8")
        return True
    except OSError:
        return False


def consume_release_request_for(pid: int) -> bool:
    """Called by the lock holder. Returns True iff a release request targeting
    `pid` is present, and atomically clears it."""
    marker = _release_marker_path()
    if not marker.exists():
        return False
    try:
        target = int(marker.read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        target = 0
    if target != pid:
        return False
    try:
        marker.unlink()
    except OSError:
        pass
    return True


def clear_release_request() -> None:
    """Best-effort cleanup of any stale release marker."""
    marker = _release_marker_path()
    try:
        marker.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass
