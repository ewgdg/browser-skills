from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from ..constants import CHROME_NEW_WINDOW_TIMEOUT_S
from ..errors import BridgeUnavailable, SurfAgentError
from .base import AgentPage, ScreenshotOptions


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
    ) -> None:
        self.backend_label = backend_label
        self.module_name = module_name
        self.timeout_s = timeout_s
        self.port = port
        self.profile_dir = profile_dir
        self.startup_error = startup_error
        self.timeout_hint = timeout_hint

    def call_tool(self, name: str, args: dict[str, Any] | None = None) -> str:
        self._ensure_running()
        payload = json.dumps({"name": name, "args": args or {}}).encode()
        request = self._call_request(payload)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                data = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace") or str(exc)
            try:
                parsed = json.loads(detail)
                detail = parsed.get("error") or detail
            except json.JSONDecodeError:
                pass
            raise SurfAgentError(f"{self.backend_label} bridge tool {name} failed: {detail}") from exc
        except TimeoutError as exc:
            raise BridgeUnavailable(
                f"{self.backend_label} bridge tool {name} timed out after {self.timeout_s:g}s{self.timeout_hint}"
            ) from exc
        except urllib.error.URLError as exc:
            raise BridgeUnavailable(f"{self.backend_label} bridge call failed: {exc}") from exc
        except OSError as exc:
            raise BridgeUnavailable(f"{self.backend_label} bridge call failed: {exc}") from exc
        result = data.get("result")
        return result if isinstance(result, str) else ""

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
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=1.0) as response:
                data = json.loads(response.read().decode())
                return response.status == 200 and data.get("status") == "ok"
        except (OSError, urllib.error.URLError, json.JSONDecodeError, TimeoutError):
            return False


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

    def print_page_id(self, *, force_new: bool = False) -> None:
        if force_new:
            self.new()
        print(0)

    def print_state(self, *, thread: str) -> None:
        print(self.client.call_tool("state", {"thread": thread}), end="")

    def print_list(self) -> None:
        print(self.client.call_tool("list", {}), end="")

    def close(self) -> int:
        output = self._call("close")
        self._print_output(output)
        return 0

    def focus(self) -> int:
        output = self._call("focus")
        self._print_output(output)
        return 0

    def close_matching(self, pattern: str) -> int:
        raise SurfAgentError(f"close-matching is not supported by {self.display_name} backend yet", exit_code=2)

    def capture_snapshot(self) -> Any:
        cli = _cli()
        text = self.snapshot()
        current = self.capture_page_metadata()
        return cli.snapshot_capture_from_page(text=text, page=current)

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

    def text(self) -> str:
        return self._call("text")

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

    def wait(self, target: str) -> str:
        value: str | int = int(target) if target.isdigit() else target
        return self._call("wait", {"target": value})

    def back(self) -> str:
        return self._call("back")

    def screenshot(self, options: ScreenshotOptions) -> str:
        return self._call("screenshot", {"path": options.path, "fullPage": options.full_page})

    def evaluate(self, code: str) -> str:
        return self._call("eval", {"code": code})

    def _call(self, name: str, payload: dict[str, Any] | None = None) -> str:
        return self.client.call_tool(name, {"thread": self.agent.state_file.stem, **(payload or {})})

    def _print_output(self, output: str) -> None:
        if output:
            print(output, end="" if output.endswith("\n") else "\n")


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


def _cli() -> Any:
    import surf_agent.cli as cli

    return cli
