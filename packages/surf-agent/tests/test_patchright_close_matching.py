from __future__ import annotations

import json
import urllib.error
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from surf_agent.backends.bridge_common import PageSlot
from surf_agent.backends.local_bridge import LocalBridgeClient
from surf_agent.backends.patchright.backend import PatchrightBackend
from surf_agent.backends.patchright.bridge import PatchrightRuntime
from surf_agent.cli import SurfAgent, main
from surf_agent.errors import BridgeUnavailable, SurfAgentError


class Page:
    def __init__(self, events: list[str], name: str, *, close_error: Exception | None = None) -> None:
        self.events = events
        self.name = name
        self.close_error = close_error
        self.closed = False

    def close(self) -> None:
        self.events.append(self.name)
        if self.close_error is not None:
            raise self.close_error
        self.closed = True

    def is_closed(self) -> bool:
        return self.closed


class Context:
    def __init__(self, pages: list[Page]) -> None:
        self.pages = pages


class BridgeClient:
    def __init__(self, result: dict[str, object] | None) -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, object]]] = []

    def call_tool_if_running(self, name: str, args: dict[str, object] | None = None) -> str | None:
        self.calls.append((name, args or {}))
        if self.result is None:
            return None
        return json.dumps(self.result, sort_keys=True) + "\n"


def local_client(tmp_path: Path) -> LocalBridgeClient:
    return LocalBridgeClient(
        backend_label="Test",
        module_name="test.bridge",
        timeout_s=1.0,
        port=9555,
        profile_dir=tmp_path / "profile",
        startup_error="bridge failed",
    )


def bridge_response(result: str) -> MagicMock:
    response = MagicMock()
    response.read.return_value = json.dumps({"result": result}).encode()
    response.__enter__.return_value = response
    return response


def runtime_with_pages(tmp_path: Path, pages: dict[str, tuple[Page, int]]) -> PatchrightRuntime:
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    runtime.pages = {
        thread: PageSlot(page=page, page_token=page_id)
        for thread, (page, page_id) in pages.items()
    }
    return runtime


def test_call_tool_if_running_returns_none_when_initially_unhealthy(tmp_path: Path) -> None:
    client = local_client(tmp_path)

    with (
        patch.object(client, "_health_ok", return_value=False),
        patch.object(client, "_ensure_running") as ensure_running,
        patch("surf_agent.backends.local_bridge.urllib.request.urlopen") as urlopen,
    ):
        assert client.call_tool_if_running("close-matching", {"pattern": "*"}) is None

    ensure_running.assert_not_called()
    urlopen.assert_not_called()


def test_call_tool_if_running_returns_none_when_bridge_disappears_without_starting(tmp_path: Path) -> None:
    client = local_client(tmp_path)

    with (
        patch.object(client, "_health_ok", return_value=True),
        patch.object(client, "_ensure_running") as ensure_running,
        patch("surf_agent.backends.local_bridge.subprocess.Popen") as popen,
        patch(
            "surf_agent.backends.local_bridge.urllib.request.urlopen",
            side_effect=urllib.error.URLError(ConnectionRefusedError("connection refused")),
        ),
    ):
        assert client.call_tool_if_running("close-matching", {"pattern": "*"}) is None

    ensure_running.assert_not_called()
    popen.assert_not_called()


def test_call_tool_if_running_returns_healthy_bridge_result(tmp_path: Path) -> None:
    client = local_client(tmp_path)

    with (
        patch.object(client, "_health_ok", return_value=True),
        patch("surf_agent.backends.local_bridge.urllib.request.urlopen", return_value=bridge_response("closed\n")),
    ):
        assert client.call_tool_if_running("close-matching", {"pattern": "*"}) == "closed\n"


def test_call_tool_if_running_preserves_bridge_errors(tmp_path: Path) -> None:
    client = local_client(tmp_path)
    http_error = urllib.error.HTTPError(
        "http://127.0.0.1:9555/call",
        500,
        "Internal Server Error",
        hdrs=None,
        fp=BytesIO(b'{"error":"bridge failed"}'),
    )

    with (
        patch.object(client, "_health_ok", return_value=True),
        patch("surf_agent.backends.local_bridge.urllib.request.urlopen", side_effect=http_error),
        pytest.raises(SurfAgentError, match="Test bridge tool close-matching failed: bridge failed"),
    ):
        client.call_tool_if_running("close-matching", {"pattern": "*"})

    with (
        patch.object(client, "_health_ok", return_value=True),
        patch("surf_agent.backends.local_bridge.urllib.request.urlopen", side_effect=TimeoutError("timed out")),
        pytest.raises(BridgeUnavailable, match="Test bridge tool close-matching timed out after 1s"),
    ):
        client.call_tool_if_running("close-matching", {"pattern": "*"})


def test_close_matching_closes_matching_managed_threads_in_sorted_order(tmp_path: Path) -> None:
    events: list[str] = []
    runtime = runtime_with_pages(
        tmp_path,
        {
            "agent-z": (Page(events, "agent-z"), 30),
            "other": (Page(events, "other"), 40),
            "agent-a": (Page(events, "agent-a"), 10),
        },
    )

    unowned = Page(events, "unowned")
    runtime.browser_or_context = Context([slot.page for slot in runtime.pages.values()] + [unowned])

    result = json.loads(runtime.call("close-matching", {"pattern": " agent-* "}))

    assert result == {
        "closed": [
            {"page_id": 10, "thread": "agent-a"},
            {"page_id": 30, "thread": "agent-z"},
        ],
        "failed": [],
        "pattern": "agent-*",
    }
    assert events == ["agent-a", "agent-z"]
    assert unowned.closed is False
    assert set(runtime.pages) == {"other"}


def test_close_matching_keeps_failed_slot_and_continues_with_later_threads(tmp_path: Path) -> None:
    events: list[str] = []
    runtime = runtime_with_pages(
        tmp_path,
        {
            "agent-a": (Page(events, "agent-a", close_error=RuntimeError("cannot close")), 10),
            "agent-b": (Page(events, "agent-b"), 20),
            "other": (Page(events, "other"), 30),
        },
    )

    result = json.loads(runtime.call("close-matching", {"pattern": "agent-*"}))

    assert result == {
        "closed": [{"page_id": 20, "thread": "agent-b"}],
        "failed": [{"page_id": 10, "thread": "agent-a"}],
        "pattern": "agent-*",
    }
    assert events == ["agent-a", "agent-b"]
    assert set(runtime.pages) == {"agent-a", "other"}


def test_close_all_arms_idle_after_response_and_stops_after_grace(tmp_path: Path) -> None:
    now = [10.0]
    events: list[str] = []
    page = Page(events, "agent-a")
    context = Context([page])
    runtime = runtime_with_pages(tmp_path, {"agent-a": (page, 10)})
    runtime.browser_or_context = context
    runtime.clock = lambda: now[0]

    assert json.loads(runtime.call("close-matching", {"pattern": "*"})) == {
        "closed": [{"page_id": 10, "thread": "agent-a"}],
        "failed": [],
        "pattern": "*",
    }
    assert runtime.idle_shutdown_deadline is None

    runtime.after_response("close-matching")
    assert runtime.idle_shutdown_deadline == 12.0

    now[0] = 12.1
    assert runtime.service_actions() is True
    assert runtime.browser_or_context is None


def test_close_all_idle_shutdown_is_cancelled_by_new_visible_page(tmp_path: Path) -> None:
    now = [10.0]
    events: list[str] = []
    page = Page(events, "agent-a")
    context = Context([page])
    runtime = runtime_with_pages(tmp_path, {"agent-a": (page, 10)})
    runtime.browser_or_context = context
    runtime.clock = lambda: now[0]

    runtime.call("close-matching", {"pattern": "*"})
    runtime.after_response("close-matching")
    context.pages.append(Page(events, "new-visible"))

    now[0] = 12.1
    assert runtime.service_actions() is False
    assert runtime.idle_shutdown_deadline is None
    assert runtime.browser_or_context is context


@pytest.mark.parametrize(
    ("result", "expected_exit"),
    [
        ({"pattern": "agent-*", "closed": [{"thread": "agent-a", "page_id": 1}], "failed": []}, 0),
        ({"pattern": "agent-*", "closed": [], "failed": [{"thread": "agent-a", "page_id": 1}]}, 1),
    ],
)
def test_patchright_backend_routes_close_matching_and_maps_failures(
    capsys: pytest.CaptureFixture[str], result: dict[str, object], expected_exit: int
) -> None:
    client = BridgeClient(result)
    backend = PatchrightBackend(SimpleNamespace(patchright_client=client), client=client, welcome_url=lambda: "about:blank")

    assert backend.close_matching(" agent-* ") == expected_exit
    assert client.calls == [("close-matching", {"pattern": "agent-*"})]
    assert json.loads(capsys.readouterr().out) == result


def test_patchright_backend_returns_empty_result_when_nonstarting_call_is_unavailable(capsys: pytest.CaptureFixture[str]) -> None:
    client = BridgeClient(None)
    backend = PatchrightBackend(SimpleNamespace(patchright_client=client), client=client, welcome_url=lambda: "about:blank")

    assert backend.close_matching(" agent-* ") == 0
    assert client.calls == [("close-matching", {"pattern": "agent-*"})]
    assert json.loads(capsys.readouterr().out) == {
        "closed": [],
        "failed": [],
        "pattern": "agent-*",
    }


def test_patchright_backend_rejects_empty_close_matching_pattern() -> None:
    client = BridgeClient(None)
    backend = PatchrightBackend(SimpleNamespace(patchright_client=client), client=client, welcome_url=lambda: "about:blank")

    with pytest.raises(SurfAgentError, match="close-matching requires a thread glob pattern") as error:
        backend.close_matching("  ")

    assert error.value.exit_code == 2
    assert client.calls == []


def test_cli_close_all_reaches_patchright_close_matching(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    client = BridgeClient({"pattern": "*", "closed": [], "failed": []})
    with patch.dict("os.environ", {"SURF_AGENT_BACKEND": "patchright", "SURF_AGENT_HOME": str(tmp_path)}, clear=True):
        agent = SurfAgent(state_file=tmp_path / "state" / "thread.json")
        agent.patchright_client = client
        with patch("surf_agent.cli.SurfAgent", return_value=agent):
            assert main(["close-all"]) == 0

    assert client.calls == [("close-matching", {"pattern": "*"})]
    assert json.loads(capsys.readouterr().out) == {"closed": [], "failed": [], "pattern": "*"}
