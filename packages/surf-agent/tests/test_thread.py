from __future__ import annotations

from dataclasses import dataclass

import pytest

from surf_agent.cli import SnapshotCapture
from surf_agent.errors import SurfAgentError
from surf_agent.thread import Thread


@dataclass
class FakeBackend:
    snapshots: list[SnapshotCapture]
    opened: list[str]
    closed: int = 0

    def open(self, url: str) -> str:
        self.opened.append(url)
        return f"opened {url}"

    def capture_snapshot(self) -> SnapshotCapture:
        return self.snapshots.pop(0)

    def close(self) -> int:
        self.closed += 1
        return 0


class FakeAgent:
    def __init__(self, backend: FakeBackend) -> None:
        self.browser_backend = backend
        self.thread_names: list[str] = []


def capture(text: str, *, page_id: int = 1) -> SnapshotCapture:
    return SnapshotCapture(
        text=text,
        page_id=page_id,
        url="https://example.test/",
        title="Example",
        origin="https://example.test",
        url_without_fragment="https://example.test/",
    )


def test_acquire_creates_named_thread_and_open_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = FakeBackend([], [])
    created: list[tuple[str, dict[str, object]]] = []

    class AgentFactory:
        def __init__(self, *, thread: str, **kwargs: object) -> None:
            created.append((thread, kwargs))
            self.browser_backend = backend

    monkeypatch.setattr("surf_agent.thread.SurfAgent", AgentFactory)

    thread = Thread.acquire("research")

    assert created == [("research", {})]
    assert thread.open("https://example.test") == "opened https://example.test"
    assert backend.opened == ["https://example.test"]


def test_snapshot_returns_full_value_and_emit_returns_useful_diff() -> None:
    baseline = "".join(f"stable line {index}\n" for index in range(220))
    changed = baseline.replace("stable line 100", "changed line 100")
    full = changed + "new line\n"
    backend = FakeBackend([capture(baseline), capture(changed), capture(full)], [])
    thread = Thread("research", agent=FakeAgent(backend))

    assert thread.snapshot() == baseline
    emitted = thread.emit()
    assert "+++ current" in emitted
    assert "+changed line 100" in emitted
    assert thread.emit(full=True) == full


def test_emit_without_baseline_returns_full_value() -> None:
    backend = FakeBackend([capture("initial\n")], [])
    thread = Thread("research", agent=FakeAgent(backend))

    assert thread.emit() == "initial\n"


def test_close_delegates_to_browser_lifecycle() -> None:
    backend = FakeBackend([], [])
    thread = Thread("research", agent=FakeAgent(backend))

    assert thread.close() is None
    assert backend.closed == 1


def test_acquire_rejects_unsafe_thread_names() -> None:
    with pytest.raises(SurfAgentError):
        Thread.acquire("../shared")
