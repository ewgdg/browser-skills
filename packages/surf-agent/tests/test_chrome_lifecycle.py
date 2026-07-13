from __future__ import annotations

from pathlib import Path
import fcntl
import multiprocessing
import os
import threading

import pytest

from surf_agent.chrome_lifecycle import ChromeLifecycleCoordinator, destination_browser_family, find_active_chrome_roots
from surf_agent.errors import SurfAgentError


class Importer:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[bool] = []
        self.fail = fail

    def run(self, force: bool) -> object:
        self.calls.append(force)
        if self.fail:
            raise SurfAgentError("import failed")
        return object()


def test_startup_imports_before_launch_and_failure_prevents_launch(tmp_path: Path) -> None:
    importer = Importer()
    launched: list[bool] = []
    coordinator = ChromeLifecycleCoordinator(destination_root=tmp_path / "profile", state_root=tmp_path / "state", importer=importer)
    with coordinator.startup_guard(health_check=lambda: False):
        launched.append(True)
    assert importer.calls == [False]
    assert launched == [True]

    failed = ChromeLifecycleCoordinator(destination_root=tmp_path / "profile2", state_root=tmp_path / "state", importer=Importer(fail=True))
    with pytest.raises(SurfAgentError):
        with failed.startup_guard(health_check=lambda: False):
            launched.append(True)
    assert launched == [True]


def test_healthy_bridge_rechecks_without_double_import(tmp_path: Path) -> None:
    importer = Importer()
    coordinator = ChromeLifecycleCoordinator(destination_root=tmp_path / "profile", state_root=tmp_path / "state", importer=importer)
    with coordinator.startup_guard(health_check=lambda: True):
        pass
    assert importer.calls == []


def test_generic_process_discovery_matches_resolved_user_data_dir() -> None:
    profile = Path("/tmp/surf-profile").resolve()
    processes = [
        (10, ["chrome", f"--user-data-dir={profile}"]),
        (11, ["chrome", "--type=renderer", f"--user-data-dir={profile}"]),
        (12, ["chrome", "--user-data-dir", "/tmp/other"]),
    ]
    assert find_active_chrome_roots(profile, process_args=lambda: processes) == [10]


def test_idle_shutdown_rechecks_once_after_grace_and_warns_on_failure(tmp_path: Path) -> None:
    checks = iter([[], []])
    sleeps: list[float] = []
    stopped: list[bool] = []
    coordinator = ChromeLifecycleCoordinator(destination_root=tmp_path / "profile", state_root=tmp_path / "state", sleeper=sleeps.append)
    assert coordinator.stop_if_idle(page_inventory=lambda: next(checks), stop=lambda: stopped.append(True)) is True
    assert sleeps == [2.0]
    assert stopped == [True]

    visible = ChromeLifecycleCoordinator(destination_root=tmp_path / "profile", state_root=tmp_path / "state", sleeper=sleeps.append)
    assert visible.stop_if_idle(page_inventory=lambda: [object()], stop=lambda: stopped.append(True)) is False


def test_explicit_import_is_serialized_and_rechecks_destination_activity(tmp_path: Path) -> None:
    importer = Importer()
    inspections: list[Path] = []
    coordinator = ChromeLifecycleCoordinator(
        destination_root=tmp_path / "profile",
        state_root=tmp_path / "state",
        importer=importer,
        process_inspector=lambda path: inspections.append(path) or False,
    )

    coordinator.import_now()

    assert importer.calls == [True]
    assert inspections == [tmp_path / "profile"]


def test_destination_family_is_derived_or_unprovable() -> None:
    assert destination_browser_family(backend="patchright", executable=None) == "chrome"
    assert destination_browser_family(backend="axi", executable="/usr/bin/google-chrome") == "chrome"
    assert destination_browser_family(backend="axi", executable="/usr/bin/chromium") == "chromium"
    assert destination_browser_family(backend="axi", executable="/opt/custom-browser") is None


def test_unknown_idle_inventory_never_stops_or_sleeps(tmp_path: Path) -> None:
    sleeps: list[float] = []
    stopped: list[bool] = []
    coordinator = ChromeLifecycleCoordinator(destination_root=tmp_path / "profile", state_root=tmp_path / "state", sleeper=sleeps.append)

    assert coordinator.stop_if_idle(page_inventory=lambda: None, stop=lambda: stopped.append(True)) is False
    assert sleeps == []
    assert stopped == []


def _hold_lifecycle_lock(lock_file: str, ready: multiprocessing.Queue, release: multiprocessing.Event) -> None:
    descriptor = os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        ready.put(True)
        release.wait(5)
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def test_interprocess_lock_serializes_import_and_second_health_recheck_avoids_duplicate_launch(tmp_path: Path) -> None:
    importer = Importer()
    coordinator = ChromeLifecycleCoordinator(destination_root=tmp_path / "profile", state_root=tmp_path / "state", importer=importer)
    coordinator.state_root.mkdir()
    ready: multiprocessing.Queue = multiprocessing.Queue()
    release = multiprocessing.Event()
    holder = multiprocessing.Process(target=_hold_lifecycle_lock, args=(str(coordinator.lock_file), ready, release))
    holder.start()
    assert ready.get(timeout=2) is True
    entered = threading.Event()

    def launch() -> None:
        with coordinator.launch_guard(health_check=lambda: False):
            entered.set()

    contender = threading.Thread(target=launch)
    contender.start()
    assert entered.wait(0.05) is False
    release.set()
    contender.join(timeout=2)
    holder.join(timeout=2)
    assert entered.is_set()
    assert importer.calls == [False]

    health = iter([False, True, True])
    launched: list[bool] = []
    with coordinator.launch_guard(health_check=lambda: next(health)):
        # LocalBridgeClient performs this final health recheck while the guard holds.
        if not next(health):
            launched.append(True)
    assert importer.calls == [False, False]
    assert launched == []
