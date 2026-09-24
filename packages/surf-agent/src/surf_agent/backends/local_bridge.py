from __future__ import annotations

import contextlib
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, ContextManager

from ..constants import CHROME_NEW_WINDOW_TIMEOUT_S, DEFAULT_WAIT_TIMEOUT_MS
from ..errors import BridgeToolError, BridgeUnavailable, ErrorCode, SurfAgentError
from ..snapshots import snapshot_capture_from_page
from .base import AgentPage, ScreenshotOptions, WaitConditions

BRIDGE_HEALTH_POLL_INTERVAL_S = 0.05


class LocalBridgeClient:
    def __init__(
        self,
        *,
        backend_label: str,
        module_name: str,
        timeout_s: float,
        port: int,
        profile_dir: Path,
        startup_error: str,
        timeout_hint: str = "",
        before_start: Callable[[], ContextManager[None]] | None = None,
    ) -> None:
        self.backend_label = backend_label
        self.module_name = module_name
        self.timeout_s = timeout_s
        self.port = port
        self.profile_dir = profile_dir
        self.startup_error = startup_error
        self.timeout_hint = timeout_hint
        self.before_start = before_start

    def call_tool(self, name: str, args: dict[str, Any] | None = None, *, extra_timeout_s: float = 0.0) -> str:
        self._ensure_running()
        output = self._call_tool(
            name, args, return_none_on_connection_failure=False, extra_timeout_s=extra_timeout_s
        )
        assert isinstance(output, str)
        return output

    def call_tool_if_running(
        self,
        name: str,
        args: dict[str, Any] | None = None,
        *,
        on_request_may_have_been_dispatched: Callable[[], None] | None = None,
    ) -> str | None:
        if not self._health_ok():
            return None
        # Once the health check succeeds, a transport failure cannot prove
        # whether the irreversible bridge request reached the browser runtime.
        if on_request_may_have_been_dispatched is not None:
            on_request_may_have_been_dispatched()
        return self._call_tool(name, args, return_none_on_connection_failure=True)

    def _call_tool(
        self,
        name: str,
        args: dict[str, Any] | None,
        *,
        return_none_on_connection_failure: bool,
        extra_timeout_s: float = 0.0,
    ) -> str | None:
        payload = json.dumps({"name": name, "args": args or {}}).encode()
        request = self._call_request(payload)
        timeout_s = self.timeout_s + extra_timeout_s
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                data = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            raise self._tool_error(name, exc) from exc
        except TimeoutError as exc:
            raise self._tool_timeout(name, timeout_s) from exc
        except urllib.error.URLError as exc:
            if _is_timeout_url_error(exc):
                raise self._tool_timeout(name, timeout_s) from exc
            if return_none_on_connection_failure:
                return None
            raise self._bridge_unavailable(exc) from exc
        except OSError as exc:
            if return_none_on_connection_failure:
                return None
            raise self._bridge_unavailable(exc) from exc
        result = data.get("result")
        return result if isinstance(result, str) else ""

    def _tool_error(self, name: str, exc: urllib.error.HTTPError) -> BridgeToolError:
        detail = exc.read().decode(errors="replace") or str(exc)
        code = None
        try:
            parsed = json.loads(detail)
        except json.JSONDecodeError:
            pass
        else:
            detail = parsed.get("error") or detail
            code = ErrorCode(parsed["code"]) if parsed.get("code") else None
        return BridgeToolError(backend_label=self.backend_label, tool_name=name, detail=str(detail), code=code)

    def _tool_timeout(self, name: str, timeout_s: float) -> BridgeUnavailable:
        # The request reached a healthy bridge, so the browser may still apply it.
        return BridgeUnavailable(
            f"{self.backend_label} bridge tool {name} timed out after {timeout_s:g}s; "
            f"its effect is unknown{self.timeout_hint}",
            code=ErrorCode.OUTCOME_UNKNOWN,
        )

    def _bridge_unavailable(self, exc: BaseException) -> BridgeUnavailable:
        return BridgeUnavailable(f"{self.backend_label} bridge call failed: {exc}")

    def stop(self) -> str:
        if not self._health_ok():
            return ""
        payload = json.dumps({"name": "stop", "args": {}}).encode()
        try:
            with urllib.request.urlopen(self._call_request(payload), timeout=self.timeout_s) as response:
                data = json.loads(response.read().decode())
        except (OSError, urllib.error.URLError, json.JSONDecodeError, TimeoutError):
            return ""
        result = data.get("result")
        return result if isinstance(result, str) else ""

    def _call_request(self, payload: bytes) -> urllib.request.Request:
        return urllib.request.Request(
            f"http://127.0.0.1:{self.port}/call",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

    def _ensure_running(self) -> None:
        if self._health_ok():
            return
        guard = self.before_start() if self.before_start is not None else contextlib.nullcontext()
        with guard:
            if self.before_start is not None and self._health_ok():
                return
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            command = [
                sys.executable,
                "-m",
                self.module_name,
                "--port",
                str(self.port),
                "--profile-dir",
                str(self.profile_dir),
            ]
            subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.monotonic() + CHROME_NEW_WINDOW_TIMEOUT_S
            while time.monotonic() < deadline:
                if self._health_ok():
                    return
                time.sleep(0.25)
        raise SurfAgentError(f"{self.backend_label} bridge did not become healthy; {self.startup_error}")

    def _health_ok(self) -> bool:
        data = self._health_payload()
        return data is not None and data.get("status") == "ok"

    def _health_payload(self) -> dict[str, object] | None:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=1.0) as response:
                data = json.loads(response.read().decode())
                if response.status != 200 or not isinstance(data, dict):
                    return None
                return data
        except (OSError, urllib.error.URLError, json.JSONDecodeError, TimeoutError):
            return None

    def _wait_until_stopped(self) -> None:
        deadline = time.monotonic() + CHROME_NEW_WINDOW_TIMEOUT_S
        while self._health_ok():
            if time.monotonic() >= deadline:
                raise BridgeUnavailable(f"{self.backend_label} bridge did not stop after requesting restart")
            time.sleep(BRIDGE_HEALTH_POLL_INTERVAL_S)


def _is_timeout_url_error(error: urllib.error.URLError) -> bool:
    return isinstance(error.reason, TimeoutError)


class LocalBridgeBackend:
    name: str
    display_name: str
    client_attr: str

    def __init__(self, agent: Any, *, client: LocalBridgeClient, welcome_url: Callable[[], str]) -> None:
        self.agent = agent
        self._client = client
        self.welcome_url = welcome_url

    @property
    def client(self) -> LocalBridgeClient:
        # Tests and callers may replace backend-specific clients after construction.
        return getattr(self.agent, self.client_attr, self._client)

    def list_threads(self) -> list[dict[str, Any]]:
        output = self.client.call_tool_if_running("list", {})
        if output is None:
            return []
        try:
            result = json.loads(output)
        except json.JSONDecodeError as exc:
            raise SurfAgentError("bridge returned invalid thread list JSON") from exc
        if not isinstance(result, dict) or not isinstance(result.get("pages"), list):
            raise SurfAgentError("bridge returned invalid thread list JSON")
        if any(not isinstance(item, dict) or not isinstance(item.get("thread"), str) for item in result["pages"]):
            raise SurfAgentError("bridge returned invalid thread list JSON")
        return result["pages"]

    def is_open(self) -> bool:
        output = self.client.call_tool_if_running("state", {"thread": self.agent.state_file.stem})
        if output is None:
            return False
        try:
            data = json.loads(output)
        except json.JSONDecodeError as exc:
            raise SurfAgentError(f"{self.display_name} bridge returned invalid state JSON") from exc
        if not isinstance(data, dict) or not isinstance(data.get("open"), bool):
            raise SurfAgentError(f"{self.display_name} bridge returned invalid state JSON")
        return isinstance(data, dict) and data.get("open") is True

    def close(self) -> int:
        self.close_page()
        return 0

    def close_page(self) -> str:
        return self._call("close")

    def focus(self) -> int:
        self._call("focus")
        return 0

    def close_matching(self, pattern: str) -> int:
        raise SurfAgentError(f"close-matching is not supported by {self.display_name} backend yet", exit_code=2)

    def capture_snapshot(self) -> Any:
        text = self.snapshot()
        current = self.capture_page_metadata()
        return snapshot_capture_from_page(text=text, page=current)

    def capture_page_metadata(self) -> Any:
        fallback = AgentPage(stable_local_page_id(self.agent.state_file.stem), backend=self.name)
        try:
            output = self._call("state")
        except SurfAgentError:
            return fallback
        try:
            data = json.loads(output)
        except json.JSONDecodeError:
            return fallback
        if not isinstance(data, dict):
            return fallback
        page_id = coerce_int(data.get("page_id")) or fallback.page_id
        return AgentPage(
            page_id,
            url=string_or_none(data.get("url")) or fallback.url,
            title=string_or_none(data.get("title")) or fallback.title,
            backend=self.name,
        )

    def open(self, url: str) -> str:
        return self._call("open", {"url": url})

    def new(self) -> str:
        return self._call("new", {"url": self.welcome_url()})

    def snapshot(self) -> str:
        return self._call("snapshot")

    def text(self, target: str | None = None) -> str:
        return self._call("text", None if target is None else {"target": target})

    def click(self, target: str) -> str:
        return self._call("click", {"uid": target})

    def fill(self, target: str, text: str) -> str:
        return self._call("fill", {"uid": target, "text": text})

    def type_text(self, text: str) -> str:
        return self._call("type", {"text": text})

    def press(self, key: str) -> str:
        return self._call("press", {"key": key})

    def scroll(self, direction: str) -> str:
        return self._call("scroll", {"direction": direction})

    def wait_ms(self, milliseconds: int) -> str:
        return self._call("wait", {"target": milliseconds})

    def wait_for(self, conditions: WaitConditions) -> str:
        payload = {
            "text": conditions.text,
            "gone": conditions.gone,
            "url": conditions.url,
            "timeoutMs": conditions.timeout_ms,
        }
        wait_s = (conditions.timeout_ms or DEFAULT_WAIT_TIMEOUT_MS) / 1000
        # The bridge holds the response for the whole wait; transport must outlast it.
        return self.client.call_tool("wait-for", self._thread_args(payload), extra_timeout_s=wait_s)

    def back(self) -> str:
        return self._call("back")

    def screenshot(self, options: ScreenshotOptions) -> str:
        return self._call("screenshot", {"path": options.path, "fullPage": options.full_page})

    def evaluate(self, code: str) -> str:
        return self._call("eval", {"code": code})

    def evaluate_value(self, code: str) -> Any:
        output = self.evaluate(code)
        try:
            return json.loads(output)
        except json.JSONDecodeError as exc:
            raise SurfAgentError(f"{self.display_name} bridge returned invalid evaluation JSON") from exc

    def _call(self, name: str, payload: dict[str, Any] | None = None) -> str:
        return self.client.call_tool(name, self._thread_args(payload))

    def _thread_args(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        return {"thread": self.agent.state_file.stem, **(payload or {})}



def stable_local_page_id(thread: str) -> int:
    value = 0
    for char in thread:
        value = ((value * 33) + ord(char)) % 2_147_483_647
    return value or 1


def coerce_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None
