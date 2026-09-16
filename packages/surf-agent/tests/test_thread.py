from __future__ import annotations

from dataclasses import dataclass
import io
import json
from pathlib import Path

import pytest

from surf_agent.backends.local_bridge import LocalBridgeBackend
from surf_agent.backends.axi import parse_axi_eval_value
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
                return json.dumps({"ready": True}) + "\n"
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


def test_wait_string_that_looks_numeric_is_text_not_duration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class StubClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []

        def call_tool(self, name: str, args: dict[str, object]) -> str:
            self.calls.append((name, args))
            return "waited\n"

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

    Thread("research").wait("123")

    assert client.calls == [("wait", {"thread": "research", "target": "123"})]


def test_evaluate_preserves_scalar_string_types_and_rejects_malformed_local_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    values = iter([json.dumps("123"), json.dumps("true"), json.dumps("null"), json.dumps({"x": 1}), "not-json"])

    class StubClient:
        def call_tool(self, name: str, args: dict[str, object]) -> str:
            return next(values) + "\n"

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

    assert thread.evaluate("1") == "123"
    assert thread.evaluate("2") == "true"
    assert thread.evaluate("3") == "null"
    assert thread.evaluate("4") == {"x": 1}
    with pytest.raises(SurfAgentError, match="invalid"):
        thread.evaluate("5")


def test_malformed_local_state_is_not_treated_as_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class StubClient:
        def call_tool_if_running(self, name: str, args: dict[str, object]) -> str:
            return "not-json"

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

    with pytest.raises(SurfAgentError, match="invalid state"):
        Thread("research").is_open()


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ('result: "123"\n', "123"),
        ('result: "true"\n', "true"),
        ('result: "null"\n', "null"),
        ('result: {"x": 1}\n', {"x": 1}),
        ("result: true\n", True),
        ("result: null\n", None),
    ],
)
def test_axi_eval_value_decodes_transport_once(output: str, expected: object) -> None:
    assert parse_axi_eval_value(output) == expected


def test_axi_eval_value_rejects_malformed_transport() -> None:
    with pytest.raises(SurfAgentError, match="invalid AXI evaluation"):
        parse_axi_eval_value("not an AXI result")


def test_axi_thread_selects_owned_page_for_numeric_text_wait_and_typed_evaluation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from types import SimpleNamespace

    from surf_agent.backends.axi import AxiBackend

    calls: list[tuple[str, dict[str, object]]] = []

    class Client:
        def call_tool(self, name: str, args: dict[str, object]) -> str:
            calls.append((name, args))
            if name == "evaluate_script":
                return 'Script ran on page and returned:\n```json\n{\n  "count": 2\n}\n```'
            return "selected\n" if name == "select_page" else "ok\n"

    state_file = tmp_path / "research.json"
    state_file.write_text('{"backend": "axi", "page_id": 7}')
    agent = SimpleNamespace(state_file=state_file, bridge_client=Client())
    agent.browser_backend = AxiBackend(agent)
    monkeypatch.setattr("surf_agent.thread._create_agent", lambda _name: agent)
    thread = Thread("research")

    thread.wait("123")
    assert [name for name, _args in calls] == ["select_page", "wait_for"]
    assert calls[0][1]["pageId"] == 7
    assert thread.evaluate("({count: 2})") == {"count": 2}


def test_axi_eval_value_handles_multiline_json() -> None:
    assert parse_axi_eval_value('result: {\n  "count": 2\n}\n') == {"count": 2}


@pytest.mark.parametrize(
    ("response", "expected", "remembered"),
    [
        ('{"pages":[{"id":7,"url":"https://example.test/"}]}', True, True),
        ('{"pages":[{"id":8,"url":"https://other.test/"}]}', False, False),
        ("No pages open\n", False, False),
        (None, False, True),
    ],
)
def test_axi_is_open_checks_running_inventory_without_selecting_or_starting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    response: str | None, expected: bool, remembered: bool,
) -> None:
    from types import SimpleNamespace

    from surf_agent.backends.axi import AxiBackend, AxiBridgeUnavailable

    calls: list[str] = []

    class Client:
        def call_tool(self, name: str, args: dict[str, object]) -> str:
            calls.append(name)
            assert (name, args) == ("list_pages", {})
            if response is None:
                raise AxiBridgeUnavailable("bridge not running")
            return response

    state_file = tmp_path / "research.json"
    state_file.write_text('{"backend": "axi", "page_id": 7}')
    agent = SimpleNamespace(state_file=state_file, bridge_client=Client())
    agent.browser_backend = AxiBackend(agent)
    monkeypatch.setattr("surf_agent.thread._create_agent", lambda _name: agent)

    assert Thread("research").is_open() is expected
    assert calls == ["list_pages"]
    assert state_file.exists() is remembered


def test_axi_is_open_preserves_state_on_invalid_inventory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from types import SimpleNamespace

    from surf_agent.backends.axi import AxiBackend

    class Client:
        def call_tool(self, _name: str, _args: dict[str, object]) -> str:
            return "unexpected response"

    state_file = tmp_path / "research.json"
    state_file.write_text('{"backend": "axi", "page_id": 7}')
    agent = SimpleNamespace(state_file=state_file, bridge_client=Client())
    agent.browser_backend = AxiBackend(agent)
    monkeypatch.setattr("surf_agent.thread._create_agent", lambda _name: agent)

    with pytest.raises(SurfAgentError, match="inventory"):
        Thread("research").is_open()
    assert state_file.exists()


def test_emit_separates_unterminated_snapshots_without_changing_the_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_backend(monkeypatch, FakeBackend([], []))
    thread = Thread("research")
    first = capture("first")
    output = io.StringIO()

    thread.emit(first, sink=output)
    thread.emit(capture("second"), full=True, sink=output)

    assert output.getvalue() == "first\nsecond\n"
    assert first.text == "first"


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
