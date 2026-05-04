"""Cross-process lock for the active livechat session, plus the cross-process
takeover signal.

Two MCP server processes (e.g. two Claude Code windows) both opening the mic
produces garbage on both sides. We acquire an OS-level lock on a well-known
path while a session is active, and write our PID to a separate file so the
other process can name the holder when reporting the conflict.

Locking dispatches by platform:
- POSIX (macOS, Linux): `fcntl.flock` — auto-released when the holding fd
  closes, including on process crash.
- Windows: `msvcrt.locking` — same semantics; the OS releases on handle close.

The PID is intentionally stored in a SEPARATE file (`session.pid`), not in
the lock file. Windows' `msvcrt.locking` is mandatory byte-range locking and
also blocks reads on the locked region; if the holder's PID lived inside the
lock file, no other process could read it. Splitting the two files keeps the
PID freely readable on every platform.

Takeover signaling is also file-based on every platform (no SIGUSR1) so it
works on Windows too. The taker writes the holder's PID into a "release
request" marker file; the holder polls the marker on every audio frame and
shuts down when it sees its own PID.
"""

from __future__ import annotations

import errno
import os
import sys
from pathlib import Path
from typing import Optional

_IS_WINDOWS = sys.platform == "win32"

if _IS_WINDOWS:
    import msvcrt
else:
    import fcntl


# --- Platform-dispatched primitive lock ops --------------------------------

def _try_lock_fd(fd: int) -> bool:
    """Non-blocking exclusive lock on `fd`. Returns True on acquire, False on conflict."""
    if _IS_WINDOWS:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            # Either the lock is held by another fd or the OS refused for some
            # other reason. Treat as conflict; nothing else we can do.
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
            os.lseek(fd, 0, os.SEEK_SET)
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
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        return Path(xdg)
    if _IS_WINDOWS:
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


def _pid_path() -> Path:
    return _lock_dir() / "session.pid"


def _release_marker_path() -> Path:
    return _lock_dir() / "release.req"


# --- SessionLock -----------------------------------------------------------

class SessionLock:
    """Best-effort exclusion: only one livechat session at a time across processes."""

    def __init__(self) -> None:
        self._fd: Optional[int] = None
        self._path = _lock_path()
        self._pid_file = _pid_path()

    def try_acquire(self) -> Optional[int]:
        """Attempt to acquire the lock.

        Returns None on success (lock held now, or already was).
        Returns the holder's PID on conflict.
        """
        if self._fd is not None:
            return None
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            # Windows requires the file to have at least 1 byte before the
            # byte-range lock will succeed. POSIX doesn't care.
            if _IS_WINDOWS:
                try:
                    if os.fstat(fd).st_size == 0:
                        os.write(fd, b"\0")
                        os.lseek(fd, 0, os.SEEK_SET)
                except OSError:
                    pass
            acquired = _try_lock_fd(fd)
        except OSError:
            os.close(fd)
            raise

        if not acquired:
            holder = _read_pid(self._pid_file)
            os.close(fd)
            return holder if holder > 0 else -1

        # Acquired. Publish our PID to the (separate, unlocked) PID file so
        # other processes can name us on conflict.
        _write_pid(self._pid_file, os.getpid())
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
        # Best-effort: clear the PID file so a future read can't see a stale
        # holder. The lock itself is already released; this is just hygiene.
        try:
            self._pid_file.unlink()
        except OSError:
            pass

    def held(self) -> bool:
        return self._fd is not None


def _write_pid(path: Path, pid: int) -> None:
    try:
        path.write_text(f"{pid}\n", encoding="utf-8")
    except OSError:
        pass


def _read_pid(path: Path) -> int:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return 0
    line = text.splitlines()[0] if text else ""
    try:
        return int(line.strip() or 0)
    except ValueError:
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
