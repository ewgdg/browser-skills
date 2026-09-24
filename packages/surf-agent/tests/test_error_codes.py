from __future__ import annotations

import time
from http.server import HTTPServer
from pathlib import Path
from threading import Thread as WorkerThread
from types import SimpleNamespace

import pytest

from surf_agent.backends.axi import AxiBackend
from surf_agent.backends.base import WaitConditions
from surf_agent.backends.bridge_common import BridgeCodedError, BridgeRequestHandler
from surf_agent.backends.local_bridge import LocalBridgeBackend, LocalBridgeClient
from surf_agent.errors import ErrorCode, SurfAgentError
from surf_agent.thread import Thread


class StubRuntime:
    def __init__(self, behavior) -> None:
        self.behavior = behavior

    def health_payload(self) -> dict[str, object]:
        return {"status": "ok"}

    def call(self, name: str, args: dict[str, object]) -> str:
        return self.behavior(name, args)


def serve(behavior) -> tuple[HTTPServer, WorkerThread]:
    handler = type("Handler", (BridgeRequestHandler,), {"runtime": StubRuntime(behavior)})
    server = HTTPServer(("127.0.0.1", 0), handler)
    worker = WorkerThread(target=server.serve_forever, daemon=True)
    worker.start()
    return server, worker


@pytest.fixture
def bridge_client(tmp_path: Path):
    servers: list[tuple[HTTPServer, WorkerThread]] = []

    def start(behavior, *, timeout_s: float = 5.0) -> LocalBridgeClient:
        server, worker = serve(behavior)
        servers.append((server, worker))
        return LocalBridgeClient(
            backend_label="Stub",
            module_name="unused",
            timeout_s=timeout_s,
            port=server.server_port,
            profile_dir=tmp_path,
            startup_error="unused",
        )

    yield start
    for server, worker in servers:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


def test_bridge_error_code_reaches_the_caller(bridge_client) -> None:
    def fail(_name, _args):
        raise BridgeCodedError(ErrorCode.NOT_VISIBLE, "target '#x' is not visible")

    client = bridge_client(fail)

    with pytest.raises(SurfAgentError) as raised:
        client.call_tool("click", {"uid": "#x"})

    assert raised.value.code == ErrorCode.NOT_VISIBLE
    assert "not visible" in str(raised.value)


def test_uncoded_bridge_failure_has_no_code(bridge_client) -> None:
    def fail(_name, _args):
        raise RuntimeError("boom")

    with pytest.raises(SurfAgentError) as raised:
        bridge_client(fail).call_tool("click", {})

    assert raised.value.code is None


def test_transport_timeout_reports_unknown_outcome(bridge_client) -> None:
    def slow(_name, _args):
        time.sleep(0.5)
        return "clicked\n"

    with pytest.raises(SurfAgentError) as raised:
        bridge_client(slow, timeout_s=0.1).call_tool("click", {})

    assert raised.value.code == ErrorCode.OUTCOME_UNKNOWN


def test_wait_transport_timeout_covers_the_wait_duration(bridge_client) -> None:
    def slow_wait(_name, _args):
        time.sleep(0.3)
        return "waited\n"

    agent = SimpleNamespace(state_file=Path("research.json"))
    backend = LocalBridgeBackend(agent, client=bridge_client(slow_wait, timeout_s=0.2), welcome_url=lambda: "about:blank")
    backend.client_attr = "unused"

    assert backend.wait_for(WaitConditions(text="Saved", timeout_ms=1_000)) == "waited\n"


class RecordingClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def call_tool(self, name: str, args: dict[str, object], **_kwargs: object) -> str:
        self.calls.append((name, args))
        return f"{name} ok\n"


@pytest.fixture
def local_thread(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    client = RecordingClient()
    agent = SimpleNamespace(state_file=tmp_path / "research.json", stub_client=client)
    backend = LocalBridgeBackend(agent, client=client, welcome_url=lambda: "about:blank")
    backend.client_attr = "stub_client"
    backend.display_name = "Stub"
    agent.browser_backend = backend
    monkeypatch.setattr("surf_agent.thread._create_agent", lambda _name: agent)
    return Thread("research"), client


def test_wait_conditions_reach_the_bridge(local_thread) -> None:
    thread, client = local_thread

    thread.wait("Saved", gone="Loading…", url="*/done*", timeout_ms=2_000)
    thread.wait("Saved")

    assert client.calls == [
        ("wait-for", {"thread": "research", "text": "Saved", "gone": "Loading…", "url": "*/done*", "timeoutMs": 2_000}),
        ("wait-for", {"thread": "research", "text": "Saved", "gone": None, "url": None, "timeoutMs": None}),
    ]


def test_wait_milliseconds_still_sleeps(local_thread) -> None:
    thread, client = local_thread

    thread.wait(250)

    assert client.calls == [("wait", {"thread": "research", "target": 250})]


@pytest.mark.parametrize(
    ("args", "kwargs", "error"),
    [
        ((), {}, ValueError),
        ((250,), {"gone": "x"}, TypeError),
        ((250,), {"timeout_ms": 100}, TypeError),
        (("",), {}, ValueError),
        ((), {"gone": ""}, ValueError),
        (("x",), {"timeout_ms": 0}, ValueError),
        (("x",), {"timeout_ms": True}, TypeError),
    ],
)
def test_wait_rejects_ambiguous_or_empty_conditions(local_thread, args, kwargs, error) -> None:
    thread, client = local_thread

    with pytest.raises(error):
        thread.wait(*args, **kwargs)

    assert client.calls == []


def test_text_target_reaches_the_bridge(local_thread) -> None:
    thread, client = local_thread

    thread.text()
    thread.text("@e5")

    assert client.calls == [
        ("text", {"thread": "research"}),
        ("text", {"thread": "research", "target": "@e5"}),
    ]


@pytest.fixture
def axi_thread(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    state_file = tmp_path / "research.json"
    state_file.write_text('{"backend": "axi", "page_id": 7}')
    agent = SimpleNamespace(state_file=state_file, bridge_client=None)
    agent.browser_backend = AxiBackend(agent)
    monkeypatch.setattr("surf_agent.thread._create_agent", lambda _name: agent)
    return Thread("research")


@pytest.mark.parametrize(
    "call",
    [
        lambda thread: thread.text("main"),
        lambda thread: thread.wait(gone="Loading"),
        lambda thread: thread.wait(url="*/done"),
        lambda thread: thread.wait("Saved", timeout_ms=1_000),
    ],
)
def test_axi_refuses_patchright_only_capabilities(axi_thread, call) -> None:
    with pytest.raises(SurfAgentError) as raised:
        call(axi_thread)

    assert raised.value.code == ErrorCode.UNSUPPORTED
