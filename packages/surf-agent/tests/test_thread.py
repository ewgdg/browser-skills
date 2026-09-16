from __future__ import annotations

from dataclasses import dataclass
import io
import json
from pathlib import Path

import pytest

from surf_agent.backends.local_bridge import LocalBridgeBackend
from surf_agent.cli import SnapshotCapture
from surf_agent.errors import SurfAgentError
from surf_agent.thread import Thread


@dataclass
class FakeBackend:
    snapshots: list[SnapshotCapture]
    opened: list[str]
    close_status: int = 0
    closed: int = 0
    capture_calls: int = 0

    def open(self, url: str) -> str:
        self.opened.append(url)
        return f"opened {url}"

    def capture_snapshot(self) -> SnapshotCapture:
        self.capture_calls += 1
        return self.snapshots.pop(0)

    def close(self) -> int:
        self.closed += 1
        return self.close_status

    def close_silently(self) -> int:
        return self.close()


@dataclass
class FakeAgent:
    browser_backend: FakeBackend


def capture(text: str, *, page_id: int = 1) -> SnapshotCapture:
    return SnapshotCapture(
        text=text,
        page_id=page_id,
        url="https://example.test/",
        title="Example",
        origin="https://example.test",
        url_without_fragment="https://example.test/",
    )


def use_backend(monkeypatch: pytest.MonkeyPatch, backend: FakeBackend) -> None:
    monkeypatch.setattr("surf_agent.thread._create_agent", lambda _name: FakeAgent(backend))


def test_thread_constructs_named_context_and_open_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = FakeBackend([], [])
    created: list[str] = []

    def create_agent(name: str) -> FakeAgent:
        created.append(name)
        return FakeAgent(backend)

    monkeypatch.setattr("surf_agent.thread._create_agent", create_agent)

    thread = Thread("research")

    assert created == ["research"]
    assert thread.open("https://example.test") == "opened https://example.test"
    assert backend.opened == ["https://example.test"]


def test_snapshot_is_complete_typed_value_and_does_not_advance_baseline(monkeypatch: pytest.MonkeyPatch) -> None:
    first = capture("first\n")
    second = capture("second\n")
    backend = FakeBackend([first, second], [])
    use_backend(monkeypatch, backend)
    thread = Thread("research")

    thread.snapshot()  # silent observation must not become an emission baseline
    observed = thread.snapshot()
    output = io.StringIO()
    thread.emit(observed, sink=output)

    assert observed.text == second.text
    assert output.getvalue() == second.text
    assert backend.capture_calls == 2


def test_last_emitted_snapshot_is_baseline_not_last_silent_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    emitted = capture("".join(f"stable line {index}\n" for index in range(220)))
    silent = capture(emitted.text.replace("stable line 100", "silent change"))
    next_emitted = capture(emitted.text.replace("stable line 101", "emitted change"))
    backend = FakeBackend([silent, next_emitted], [])
    use_backend(monkeypatch, backend)
    thread = Thread("research")
    first_output = io.StringIO()

    thread.emit(emitted, sink=first_output)
    thread.snapshot()
    second_output = io.StringIO()
    thread.emit(next_emitted, sink=second_output)

    assert "+emitted change" in second_output.getvalue()
    assert "silent change" not in second_output.getvalue()


def test_emit_does_not_capture_and_explicit_full_establishes_baseline(monkeypatch: pytest.MonkeyPatch) -> None:
    first = capture("".join(f"stable line {index}\n" for index in range(220)))
    second = capture(first.text.replace("stable line 100", "changed line 100"))
    backend = FakeBackend([], [])
    use_backend(monkeypatch, backend)
    thread = Thread("research")

    output = io.StringIO()
    thread.emit(first, full=True, sink=output)
    thread.emit(second, sink=output)

    assert backend.capture_calls == 0
    assert output.getvalue().startswith(first.text)
    assert "+changed line 100" in output.getvalue()


def test_output_failure_does_not_advance_baseline(monkeypatch: pytest.MonkeyPatch) -> None:
    first = capture("".join(f"stable line {index}\n" for index in range(220)))
    second = capture(first.text.replace("stable line 100", "changed line 100"))
    third = capture(first.text.replace("stable line 101", "third change"))
    backend = FakeBackend([], [])
    use_backend(monkeypatch, backend)
    thread = Thread("research")
    thread.emit(first, sink=io.StringIO())

    class FailingSink:
        def write(self, _value: str) -> int:
            raise OSError("closed output")

    with pytest.raises(OSError):
        thread.emit(second, sink=FailingSink())
    output = io.StringIO()
    thread.emit(third, sink=output)

    assert "+third change" in output.getvalue()
    assert "changed line 100" not in output.getvalue()


def test_navigation_clears_emission_baseline_and_handles_are_independent(monkeypatch: pytest.MonkeyPatch) -> None:
    first = capture("".join(f"stable line {index}\n" for index in range(220)))
    second = capture(first.text.replace("stable line 1", "new page line 1"), page_id=2)
    first_backend = FakeBackend([], [])
    second_backend = FakeBackend([], [])
    backends = {"first": first_backend, "second": second_backend}
    monkeypatch.setattr("surf_agent.thread._create_agent", lambda name: FakeAgent(backends[name]))
    first_thread = Thread("first")
    second_thread = Thread("second")
    first_thread.emit(first, sink=io.StringIO())

    first_thread.open("https://example.test/new")
    output = io.StringIO()
    first_thread.emit(second, sink=output)
    independent = io.StringIO()
    second_thread.emit(second, sink=independent)

    assert output.getvalue() == second.text
    assert independent.getvalue() == second.text


def test_close_raises_on_backend_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = FakeBackend([], [], close_status=1)
    use_backend(monkeypatch, backend)
    thread = Thread("research")

    with pytest.raises(SurfAgentError, match="close"):
        thread.close()
    assert backend.closed == 1


def test_close_is_silent_through_real_local_backend(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    class StubClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []

        def call_tool(self, name: str, args: dict[str, object]) -> str:
            self.calls.append((name, args))
            return "closed by bridge\n"

    class Agent:
        state_file = Path("research.json")

    client = StubClient()
    agent = Agent()
    agent.stub_client = client
    backend = LocalBridgeBackend(agent, client=client, welcome_url=lambda: "about:blank")
    backend.client_attr = "stub_client"
    backend.display_name = "Stub"
    agent.browser_backend = backend
    monkeypatch.setattr("surf_agent.thread._create_agent", lambda _name: agent)

    Thread("research").close()

    assert client.calls == [("close", {"thread": "research"})]
    assert capsys.readouterr().out == ""

    backend.close()
    assert capsys.readouterr().out == "closed by bridge\n"


def test_thread_rejects_unsafe_names(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SurfAgentError):
        Thread("../shared")


def test_actions_use_real_local_backend_without_stdout(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    class StubClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []

        def call_tool(self, name: str, args: dict[str, object]) -> str:
            self.calls.append((name, args))
            if name == "eval":
                return '{"ready": true}\n'
            return f"{name} ok\n"

        def call_tool_if_running(self, name: str, args: dict[str, object]) -> str | None:
            self.calls.append((name, args))
            if name == "state":
                return json.dumps({"open": True})
            return None

    class Agent:
        state_file = tmp_path / "research.json"

    client = StubClient()
    agent = Agent()
    agent.stub_client = client
    backend = LocalBridgeBackend(agent, client=client, welcome_url=lambda: "about:blank")
    backend.client_attr = "stub_client"
    backend.display_name = "Stub"
    agent.browser_backend = backend
    monkeypatch.setattr("surf_agent.thread._create_agent", lambda _name: agent)
    thread = Thread("research")

    assert thread.is_open() is True
    assert thread.click("@button") == "click ok\n"
    assert thread.fill("@name", "Ada Lovelace") == "fill ok\n"
    assert thread.type_text("hello") == "type ok\n"
    assert thread.press("Enter") == "press ok\n"
    assert thread.scroll("down") == "scroll ok\n"
    assert thread.wait(250) == "wait ok\n"
    assert thread.wait("Loaded") == "wait ok\n"
    assert thread.back() == "back ok\n"
    assert thread.text() == "text ok\n"
    assert thread.screenshot(str(tmp_path / "shot.png"), full_page=True) == "screenshot ok\n"
    assert thread.evaluate("({ready: true})") == {"ready": True}

    assert [name for name, _args in client.calls] == [
        "state", "click", "fill", "type", "press", "scroll", "wait", "wait", "back", "text", "screenshot", "eval"
    ]
    assert client.calls[6][1]["target"] == 250
    assert client.calls[7][1]["target"] == "Loaded"
    assert capsys.readouterr().out == ""


def test_is_open_does_not_start_missing_local_bridge(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class StubClient:
        def __init__(self) -> None:
            self.running_checks = 0
            self.started_calls = 0

        def call_tool_if_running(self, name: str, args: dict[str, object]) -> str | None:
            self.running_checks += 1
            assert name == "state"
            return None

        def call_tool(self, name: str, args: dict[str, object]) -> str:
            self.started_calls += 1
            raise AssertionError("state query must not start the bridge")

    class Agent:
        state_file = tmp_path / "research.json"

    client = StubClient()
    agent = Agent()
    agent.stub_client = client
    backend = LocalBridgeBackend(agent, client=client, welcome_url=lambda: "about:blank")
    backend.client_attr = "stub_client"
    backend.display_name = "Stub"
    agent.browser_backend = backend
    monkeypatch.setattr("surf_agent.thread._create_agent", lambda _name: agent)

    assert Thread("research").is_open() is False
    assert client.running_checks == 1
    assert client.started_calls == 0
