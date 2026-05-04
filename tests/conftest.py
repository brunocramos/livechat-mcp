from __future__ import annotations

import pytest


@pytest.fixture
def isolated_lock_dir(tmp_path, monkeypatch):
    """Point the lockfile module at a fresh tmp dir so tests don't fight over the real lock."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def _isolate_runtime_dir(tmp_path_factory, monkeypatch, request):
    """Every test gets its own runtime dir so the audio loop's release-marker
    check (which runs in many tests via _run_inner) cannot pick up stale state
    from the user's real ~/.cache. Tests that explicitly use `isolated_lock_dir`
    override this with their own tmp_path."""
    if "isolated_lock_dir" in request.fixturenames:
        # Let the explicit fixture win.
        return
    rt = tmp_path_factory.mktemp("runtime")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(rt))
