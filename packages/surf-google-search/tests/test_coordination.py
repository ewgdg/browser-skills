from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from surf_google_search.coordination import FileSearchCoordinator


def _wait_for(path: Path, timeout: float = 2) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.01)
    return path.exists()


def test_file_coordinator_serializes_processes_until_the_lease_is_released(tmp_path) -> None:
    lock_path = tmp_path / "search.lock"
    marker_path = tmp_path / "challenge.json"
    started_path = tmp_path / "started"
    acquired_path = tmp_path / "acquired"
    first = FileSearchCoordinator(lock_path=lock_path, marker_path=marker_path).acquire()
    script = """
import sys
from pathlib import Path
from surf_google_search.coordination import FileSearchCoordinator
lock, marker, started, acquired = map(Path, sys.argv[1:])
started.touch()
lease = FileSearchCoordinator(lock_path=lock, marker_path=marker).acquire()
acquired.touch()
lease.release()
"""
    waiter = subprocess.Popen(
        [
            sys.executable,
            "-c",
            script,
            str(lock_path),
            str(marker_path),
            str(started_path),
            str(acquired_path),
        ]
    )
    try:
        assert _wait_for(started_path)
        assert not acquired_path.exists()
        first.release()
        assert _wait_for(acquired_path)
        assert waiter.wait(timeout=2) == 0
    finally:
        first.release()
        if waiter.poll() is None:
            waiter.terminate()
            waiter.wait(timeout=2)


def test_challenge_marker_stores_only_the_preserved_thread(tmp_path) -> None:
    coordinator = FileSearchCoordinator(
        lock_path=tmp_path / "search.lock",
        marker_path=tmp_path / "challenge.json",
    )

    assert coordinator.blocked_thread() is None
    coordinator.block("surf-google-search-challenge123")
    assert coordinator.blocked_thread() == "surf-google-search-challenge123"
    assert (tmp_path / "challenge.json").read_text() == '{"thread":"surf-google-search-challenge123"}\n'

    coordinator.clear_block()
    assert coordinator.blocked_thread() is None
