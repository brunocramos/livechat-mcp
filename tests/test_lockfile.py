from __future__ import annotations

import os

from livechat_mcp.lockfile import (
    SessionLock,
    clear_release_request,
    consume_release_request_for,
    signal_holder_to_shutdown,
)


# --- locking ---


def test_acquire_release_roundtrip(isolated_lock_dir):
    lock = SessionLock()
    assert lock.try_acquire() is None
    assert lock.held()
    lock.release()
    assert not lock.held()


def test_acquire_is_idempotent_for_same_instance(isolated_lock_dir):
    lock = SessionLock()
    assert lock.try_acquire() is None
    assert lock.try_acquire() is None  # second call no-ops


def test_second_lock_sees_holder_pid(isolated_lock_dir):
    a = SessionLock()
    b = SessionLock()
    assert a.try_acquire() is None
    holder = b.try_acquire()
    assert holder == os.getpid()
    assert not b.held()
    a.release()


def test_release_lets_other_acquire(isolated_lock_dir):
    a = SessionLock()
    b = SessionLock()
    assert a.try_acquire() is None
    assert b.try_acquire() == os.getpid()
    a.release()
    assert b.try_acquire() is None
    b.release()


def test_pid_file_contains_holder_pid(isolated_lock_dir):
    lock = SessionLock()
    assert lock.try_acquire() is None
    # PID lives in a SEPARATE file from the lock so other processes can read it
    # without contending with the byte-range lock (mandatory on Windows).
    pid_file = isolated_lock_dir / "livechat-mcp" / "session.pid"
    assert pid_file.read_text().strip() == str(os.getpid())
    lock.release()


def test_release_clears_pid_file(isolated_lock_dir):
    lock = SessionLock()
    assert lock.try_acquire() is None
    pid_file = isolated_lock_dir / "livechat-mcp" / "session.pid"
    assert pid_file.exists()
    lock.release()
    assert not pid_file.exists()


def test_release_is_safe_when_unheld(isolated_lock_dir):
    lock = SessionLock()
    lock.release()  # no-op, must not raise
    assert not lock.held()


# --- file-based takeover signaling (cross-platform) ---


def test_signal_writes_release_marker(isolated_lock_dir):
    assert signal_holder_to_shutdown(12345) is True
    marker = isolated_lock_dir / "livechat-mcp" / "release.req"
    assert marker.exists()
    assert marker.read_text().strip() == "12345"


def test_consume_release_request_matches_pid_and_clears(isolated_lock_dir):
    signal_holder_to_shutdown(os.getpid())
    assert consume_release_request_for(os.getpid()) is True
    # Marker should be gone after consume.
    assert consume_release_request_for(os.getpid()) is False


def test_consume_release_request_ignores_other_pid(isolated_lock_dir):
    signal_holder_to_shutdown(42)
    # We are not pid 42, so the marker is not for us.
    assert consume_release_request_for(os.getpid()) is False
    # And the marker is preserved for the actual target.
    assert consume_release_request_for(42) is True


def test_consume_release_request_no_marker_returns_false(isolated_lock_dir):
    assert consume_release_request_for(os.getpid()) is False


def test_clear_release_request_is_idempotent(isolated_lock_dir):
    clear_release_request()  # no marker yet
    signal_holder_to_shutdown(os.getpid())
    clear_release_request()
    assert consume_release_request_for(os.getpid()) is False
    clear_release_request()  # already gone
