from __future__ import annotations

import contextlib
import csv
import difflib
import fnmatch
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import quote, urlparse, urlunparse

FORBIDDEN_COMMANDS = {
    "ai",
    "chatgpt",
    "claude",
    "gemini",
    "perplexity",
    "grok",
    "aistudio",
    "aistudio.build",
    "tab.new",
    "new_tab",
    "tabs_create",
    "tab.switch",
    "switch_tab",
    "tab.close",
    "close_tab",
    "tab.group",
    "tab.ungroup",
    "window.new",
}

MANAGEMENT_COMMANDS = {
    "help",
    "state",
    "list",
    "page-id",
    "new",
    "reset",
    "close",
    "focus",
    "close-all",
    "close-matching",
    "bridge-stop",
}
DEFAULT_THREAD = "default"
DEFAULT_AXI_BIN = "npx -y chrome-devtools-axi"
DEFAULT_AXI_TIMEOUT_S = 15.0
DEFAULT_AXI_PORT = "9335"
DEFAULT_CHROME_DEBUG_PORT = "9336"
DEFAULT_CHROME_CLASS = "surf-agent"
AXI_STATE_DIR = Path.home() / ".chrome-devtools-axi"
AXI_BRIDGE_PID_FILE = AXI_STATE_DIR / "bridge.pid"
SURF_AGENT_WINDOW_TITLE = "Surf Agent"
CHROME_NEW_WINDOW_TIMEOUT_S = 10.0
SNAPSHOT_DIFF_MAX_RATIO = 0.50
SNAPSHOT_DIFF_MIN_SAVED_CHARS = 250
SNAPSHOT_DIFF_MAX_HUNKS = 8


@dataclass(frozen=True)
class AgentConfig:
    thread: str = DEFAULT_THREAD


@dataclass(frozen=True)
class AgentPage:
    page_id: int
    url: str | None = None
    title: str | None = None
    backend: str = "axi"


@dataclass(frozen=True)
class DoOptions:
    jsonl: bool = False
    quiet: bool = False


@dataclass
class DoContext:
    snapshot_baseline: "SnapshotCapture | None" = None


@dataclass(frozen=True)
class SnapshotCapture:
    text: str
    page_id: int | None
    url: str | None
    title: str | None
    origin: str | None
    url_without_fragment: str | None


@dataclass(frozen=True)
class SnapshotDiffDecision:
    output: str
    used_diff: bool
    reason: str


@dataclass(frozen=True)
class DoStep:
    args: list[str]
    emit: bool = False
    quiet: bool = False

    @property
    def command(self) -> str:
        return self.args[0] if self.args else ""

    @property
    def display(self) -> str:
        return shlex.join(self.args)


AgentState = AgentPage


class SurfAgentError(RuntimeError):
    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class AxiBridgeUnavailable(SurfAgentError):
    pass


class AxiBridgeConfigMismatch(SurfAgentError):
    pass


class AxiBridgeClient:
    def __init__(
        self,
        *,
        timeout_s: float,
        pid_file: Path = AXI_BRIDGE_PID_FILE,
        expected_profile_dir: Path | None = None,
        expected_chrome_class: str | None = None,
        expected_browser_url: str | None = None,
    ) -> None:
        self.timeout_s = timeout_s
        self.pid_file = pid_file
        self.expected_profile_dir = expected_profile_dir
        self.expected_chrome_class = expected_chrome_class
        self.expected_browser_url = expected_browser_url

    def call_tool(self, name: str, args: dict[str, Any] | None = None) -> str:
        port = self._ready_port()
        payload = json.dumps({"name": name, "args": args or {}}).encode()
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/call",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                data = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace") or str(exc)
            raise SurfAgentError(f"browser bridge tool {name} failed: {detail}") from exc
        except urllib.error.URLError as exc:
            raise AxiBridgeUnavailable(f"browser bridge call failed: {exc}") from exc
        except (json.JSONDecodeError, TimeoutError) as exc:
            raise SurfAgentError(f"browser bridge returned invalid response for {name}") from exc
        result = data.get("result")
        return result if isinstance(result, str) else ""

    def _ready_port(self) -> int:
        # surf-agent owns the AXI port default so callers do not need env boilerplate.
        # The PID file is still read for diagnostics, but a stale bridge on AXI's
        # package default port must not override surf-agent's configured port.
        port = int(os.environ.get("CHROME_DEVTOOLS_AXI_PORT", DEFAULT_AXI_PORT))
        pid_port = self._read_pid_port()
        if pid_port is not None and pid_port != port:
            # Ignore an old browser bridge on a different port; startup fallback below will use our env.
            pass
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=min(self.timeout_s, 2.0)) as response:
                data = json.loads(response.read().decode())
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise AxiBridgeUnavailable("browser bridge is not running") from exc
        if data.get("status") != "ok":
            raise AxiBridgeUnavailable("browser bridge is not healthy")
        self._verify_bridge_profile(port)
        return port

    def _verify_bridge_profile(self, port: int) -> None:
        if self.expected_profile_dir is None:
            return
        pid_data = self._read_pid_data()
        pid = coerce_int(pid_data.get("pid")) if pid_data else None
        pid_port = coerce_int(pid_data.get("port")) if pid_data else None
        env = self._read_process_env(pid) if pid is not None and pid_port == port else None
        mismatch = self._bridge_env_mismatch(env)
        if mismatch:
            raise AxiBridgeConfigMismatch(mismatch)

    def _bridge_env_mismatch(self, env: dict[str, str] | None) -> str | None:
        if self.expected_profile_dir is None:
            return None
        expected_profile = str(self.expected_profile_dir)
        if env is None:
            return f"browser bridge is already running on port {os.environ.get('CHROME_DEVTOOLS_AXI_PORT', DEFAULT_AXI_PORT)}, but surf-agent cannot verify it uses the dedicated profile; run `surf-agent bridge-stop`, then retry"
        browser_url = env.get("CHROME_DEVTOOLS_AXI_BROWSER_URL")
        if env.get("CHROME_DEVTOOLS_AXI_AUTO_CONNECT") == "1":
            return "browser bridge is running against an explicit/user Chrome connection; run `surf-agent bridge-stop`, then retry so surf-agent can use its dedicated profile"
        if self.expected_browser_url is not None:
            if browser_url != self.expected_browser_url:
                return f"browser bridge is running against browser URL {browser_url!r}, expected {self.expected_browser_url!r}; run `surf-agent bridge-stop`, then retry"
            return None
        if browser_url:
            return "browser bridge is running against an explicit/user Chrome connection; run `surf-agent bridge-stop`, then retry so surf-agent can use its dedicated profile"
        if env.get("CHROME_DEVTOOLS_AXI_USER_DATA_DIR") != expected_profile:
            return f"browser bridge is running with profile {env.get('CHROME_DEVTOOLS_AXI_USER_DATA_DIR')!r}, expected {expected_profile!r}; run `surf-agent bridge-stop`, then retry"
        if self.expected_chrome_class and not any(arg == f"--class={self.expected_chrome_class}" for arg in env.get("CHROME_DEVTOOLS_AXI_CHROME_ARGS", "").split()):
            return f"browser bridge is running without --class={self.expected_chrome_class}; run `surf-agent bridge-stop`, then retry"
        return None

    def _read_process_env(self, pid: int) -> dict[str, str] | None:
        try:
            raw = Path(f"/proc/{pid}/environ").read_bytes()
        except OSError:
            return None
        env: dict[str, str] = {}
        for item in raw.split(b"\0"):
            if not item or b"=" not in item:
                continue
            key, value = item.split(b"=", 1)
            env[key.decode(errors="replace")] = value.decode(errors="replace")
        return env

    def _read_pid_port(self) -> int | None:
        data = self._read_pid_data()
        return coerce_int(data.get("port")) if data else None

    def _read_pid_data(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self.pid_file.read_text())
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None


class SurfAgent:
    def __init__(
        self,
        *,
        axi_bin: str | None = None,
        chrome_bin: str | None = None,
        command_timeout_s: float | None = None,
        bridge_client: AxiBridgeClient | None = None,
        state_file: Path | None = None,
        thread: str = DEFAULT_THREAD,
        state_dir: Path | None = None,
        chrome_profile_dir: Path | None = None,
        chrome_class: str | None = None,
    ) -> None:
        self.axi_bin = axi_bin or os.environ.get("SURF_AGENT_AXI_BIN", DEFAULT_AXI_BIN)
        self.chrome_bin = chrome_bin or os.environ.get("SURF_AGENT_CHROME_BIN") or find_chrome_bin()
        self.command_timeout_s = command_timeout_s if command_timeout_s is not None else parse_timeout_env()
        self.state_file = state_file or default_state_file(thread=thread, state_dir=state_dir)
        self.state_dir = self.state_file.parent if state_file else default_state_dir(state_dir=state_dir)
        self.chrome_profile_dir = chrome_profile_dir or default_chrome_profile_dir()
        self.chrome_class = chrome_class or os.environ.get("SURF_AGENT_CHROME_CLASS") or DEFAULT_CHROME_CLASS
        self.chrome_debug_port = parse_port_env("SURF_AGENT_CHROME_DEBUG_PORT", DEFAULT_CHROME_DEBUG_PORT)
        self.browser_url = f"http://127.0.0.1:{self.chrome_debug_port}"
        self.bridge_client = bridge_client or AxiBridgeClient(
            timeout_s=self.command_timeout_s,
            expected_profile_dir=self.chrome_profile_dir if self._uses_dedicated_chrome_profile() else None,
            expected_chrome_class=self.chrome_class if self._uses_dedicated_chrome_profile() else None,
            expected_browser_url=self.browser_url if self._uses_dedicated_chrome_profile() else None,
        )

    def ensure_page(self, *, force_new: bool = False, url: str | None = None) -> AgentPage:
        if force_new:
            self._close_remembered_axi_page()
        else:
            state_page = self._load_axi_state()
            if state_page:
                return state_page

        created = self._create_axi_page(url)
        self._save_axi_state(created)
        return created

    def run_in_window(self, args: Sequence[str]) -> int:
        return self._run_axi_command(args)

    def execute_in_window(self, args: Sequence[str]) -> str:
        return self._execute_axi_command(args)

    def print_page_id(self, *, force_new: bool = False) -> None:
        print(self.ensure_page(force_new=force_new).page_id)

    def print_state(self, *, thread: str) -> None:
        self._print_axi_state(thread=thread)

    def print_list(self) -> None:
        self._print_axi_list()

    def reset_state(self) -> None:
        unlink_missing_ok(self.state_file)

    def close(self) -> int:
        return self._close_remembered_axi_page()

    def focus(self) -> int:
        page = self._require_current_axi_page()
        return self._select_axi_page(page.page_id, bring_to_front=True).returncode

    def close_matching(self, pattern: str) -> int:
        return self._close_matching_axi(pattern)

    def bridge_stop(self) -> int:
        output = self._run_axi_text(["stop"])
        if output:
            print(output, end="" if output.endswith("\n") else "\n")
        return 0

    def print_profile_show(self) -> None:
        payload = {
            "axi_bridge_port": int(os.environ.get("CHROME_DEVTOOLS_AXI_PORT", DEFAULT_AXI_PORT)),
            "browser_url": self.browser_url,
            "chrome_class": self.chrome_class,
            "chrome_debug_port": self.chrome_debug_port,
            "profile_dir": str(self.chrome_profile_dir),
        }
        print(json.dumps(payload, sort_keys=True))

    def profile_open(self, url: str = "about:blank") -> int:
        if self._chrome_debug_endpoint_ready():
            raise SurfAgentError(f"automated Surf Agent Chrome is running at {self.browser_url}; close Surf Agent windows or run `surf-agent bridge-stop` before `profile open`")
        if not self.chrome_bin:
            raise SurfAgentError("could not find Chrome executable for profile open; set SURF_AGENT_CHROME_BIN")
        self.chrome_profile_dir.mkdir(parents=True, exist_ok=True)
        command = [*shlex.split(self.chrome_bin), f"--class={self.chrome_class}", f"--user-data-dir={self.chrome_profile_dir}", "--new-window", url]
        proc = self._subprocess_run(command, check=False, text=True, capture_output=True, timeout=CHROME_NEW_WINDOW_TIMEOUT_S)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "Chrome profile open failed").strip()
            raise SurfAgentError(detail)
        return 0

    # AXI backend

    def _run_axi_command(self, args: Sequence[str]) -> int:
        if not args:
            print_help(sys.stderr)
            return 2
        output = self._execute_axi_command(args)
        if output:
            print(output, end="" if output.endswith("\n") else "\n")
        return 0

    def _execute_axi_command(self, args: Sequence[str]) -> str:
        command = first_command(args)
        if command is None:
            raise SurfAgentError("missing command", exit_code=2)
        if command in FORBIDDEN_COMMANDS:
            raise SurfAgentError(forbidden_message(command), exit_code=2)
        if command == "open":
            return self._axi_open(args)
        if command == "snapshot" and len(args) > 1:
            mode = parse_snapshot_flags(args)
            if mode == "baseline":
                raise SurfAgentError("snapshot --baseline is only supported inside do", exit_code=2)
            current = capture_snapshot(self)
            decision = choose_snapshot_diff(None, current)
            return decision.output

        axi_args, update_state = self._translate_axi_command(args)
        page = self._require_current_axi_page()
        output = self._run_axi_text(axi_args)
        if update_state:
            parsed = parse_axi_eval_json(output)
            if parsed is not None:
                self._save_axi_state(AgentPage(page.page_id, url=string_or_none(parsed.get("url")) or page.url, title=string_or_none(parsed.get("title")) or page.title))
        return output

    def _axi_open(self, args: Sequence[str]) -> str:
        if len(args) != 2:
            raise SurfAgentError(f"{args[0]} requires exactly one URL", exit_code=2)
        url = args[1]
        current = self._load_axi_state()
        if current:
            try:
                self._select_axi_page(current.page_id)
            except SurfAgentError:
                unlink_missing_ok(self.state_file)
            else:
                output = self._run_axi_text(["open", url])
                page = merge_page(AgentPage(current.page_id, url=url, title=current.title), find_page(parse_axi_pages(output), current.page_id))
                self._save_axi_state(page)
                return strip_axi_page_list(output)

        page = self._create_axi_page(url)
        self._save_axi_state(page)
        self._select_axi_page(page.page_id)
        output = self._run_axi_text(["open", url])
        page = merge_page(AgentPage(page.page_id, url=url, title=page.title), find_page(parse_axi_pages(output), page.page_id))
        self._save_axi_state(page)
        return strip_axi_page_list(output)

    def _translate_axi_command(self, args: Sequence[str]) -> tuple[list[str], bool]:
        command = first_command(args)
        if command is None:
            raise SurfAgentError("missing command", exit_code=2)
        values = list(args[1:])

        if command == "snapshot":
            if values:
                parse_snapshot_flags(args)
                raise SurfAgentError("snapshot --baseline and snapshot --diff are only supported by dedicated handlers", exit_code=2)
            return ["snapshot"], False
        if command == "text":
            if values:
                raise SurfAgentError("text does not accept arguments", exit_code=2)
            return ["text"], False
        if command == "eval":
            if not values:
                raise SurfAgentError("eval requires code", exit_code=2)
            return ["eval", " ".join(values)], False
        if command == "click":
            if len(values) != 1:
                raise SurfAgentError("click requires exactly one target", exit_code=2)
            return ["click", values[0]], False
        if command == "fill":
            if len(values) < 2:
                raise SurfAgentError("fill requires target and text", exit_code=2)
            return ["fill", values[0], " ".join(values[1:])], False
        if command == "type":
            if not values:
                raise SurfAgentError("type requires text", exit_code=2)
            return ["type", " ".join(values)], False
        if command == "press":
            if len(values) != 1:
                raise SurfAgentError("press requires exactly one key", exit_code=2)
            return ["press", values[0]], False
        if command == "scroll":
            if len(values) != 1 or values[0] not in {"up", "down", "top", "bottom"}:
                raise SurfAgentError("scroll requires direction: up, down, top, or bottom", exit_code=2)
            return ["scroll", values[0]], False
        if command == "screenshot":
            output_path = parse_screenshot_output(values)
            return ["screenshot", output_path], False
        if command == "back":
            if values:
                raise SurfAgentError("back does not accept arguments", exit_code=2)
            return ["back"], False
        if command == "wait":
            if len(values) != 1:
                raise SurfAgentError("wait requires one duration in milliseconds or text target", exit_code=2)
            return ["wait", values[0]], False

        raise SurfAgentError(f"unsupported browser command: {command}", exit_code=2)

    def _print_axi_state(self, *, thread: str) -> None:
        cached = self._load_axi_state()
        if cached is None:
            print(json.dumps({"backend": "axi", "thread": thread, "open": False}, sort_keys=True))
            return
        try:
            page = self._current_axi_page_from_state(cached)
        except SurfAgentError:
            unlink_missing_ok(self.state_file)
            print(json.dumps({"backend": "axi", "thread": thread, "open": False}, sort_keys=True))
            return
        self._save_axi_state(page)
        print(json.dumps(axi_state_payload(thread=thread, cached=page), sort_keys=True))

    def _print_axi_list(self) -> None:
        if not self.state_dir.exists():
            print(json.dumps({"backend": "axi", "threads": []}, sort_keys=True))
            return

        threads: list[dict[str, Any]] = []
        for state_file in sorted(self.state_dir.glob("*.json")):
            thread = state_file.stem
            cached = load_state_file(state_file)
            if cached is None:
                unlink_missing_ok(state_file)
                continue
            threads.append(axi_state_payload(thread=thread, cached=cached))

        print(json.dumps({"backend": "axi", "threads": threads}, sort_keys=True))

    def _close_remembered_axi_page(self) -> int:
        page = self._load_axi_state()
        if not page:
            return 0
        try:
            self._run_axi_text(["closepage", str(page.page_id)])
        except SurfAgentError:
            return 1
        self.reset_state()
        return 0

    def _close_matching_axi(self, pattern: str) -> int:
        pattern = pattern.strip()
        if not pattern:
            raise SurfAgentError("close-matching requires a thread glob pattern", exit_code=2)

        result: dict[str, Any] = {"pattern": pattern, "closed": [], "stale": [], "invalid": [], "failed": []}
        if not self.state_dir.exists():
            print(json.dumps(result, sort_keys=True))
            return 0

        for state_file in sorted(self.state_dir.glob("*.json")):
            thread = state_file.stem
            if not fnmatch.fnmatchcase(thread, pattern):
                continue
            cached = load_state_file(state_file)
            if cached is None:
                unlink_missing_ok(state_file)
                result["invalid"].append({"thread": thread})
                continue
            if not isinstance(cached, AgentPage):
                continue
            item = {"thread": thread, "page_id": cached.page_id}
            try:
                self._run_axi_text(["closepage", str(cached.page_id)])
            except SurfAgentError:
                result["failed"].append(item)
            else:
                unlink_missing_ok(state_file)
                result["closed"].append(item)

        print(json.dumps(result, sort_keys=True))
        return 1 if result["failed"] else 0

    def _require_current_axi_page(self) -> AgentPage:
        page = self._load_axi_state()
        if page is None:
            raise SurfAgentError("no remembered browser page for this thread; run `surf-agent open <url>` or `surf-agent new` first")
        try:
            self._select_axi_page(page.page_id)
        except SurfAgentError as exc:
            unlink_missing_ok(self.state_file)
            raise SurfAgentError(str(exc)) from exc
        return page

    def _current_axi_page_from_state(self, page: AgentPage) -> AgentPage:
        self._select_axi_page(page.page_id)
        output = self._run_axi_text(["eval", "JSON.stringify({title:document.title,url:location.href})"])
        parsed = parse_axi_eval_json(output)
        if parsed is None:
            return page
        return AgentPage(page.page_id, url=string_or_none(parsed.get("url")) or page.url, title=string_or_none(parsed.get("title")) or page.title)

    def _create_axi_page(self, url: str | None = None) -> AgentPage:
        page = self._new_dedicated_axi_window_page()
        if url is not None:
            return page
        welcome_url = surf_agent_welcome_url()
        self._select_axi_page(page.page_id)
        self._run_axi_text(["open", welcome_url])
        return AgentPage(page.page_id, url=welcome_url, title=SURF_AGENT_WINDOW_TITLE)

    def _new_dedicated_axi_window_page(self) -> AgentPage:
        before = self._axi_pages(allow_failure=True)
        before_ids = {page.page_id for page in before}
        self._open_chrome_window(surf_agent_app_url())
        pages = self._wait_for_new_axi_page(before_ids)
        new_pages = [page for page in pages if page.page_id not in before_ids]
        owned_page = self._find_owned_new_axi_page(new_pages)
        if owned_page is None:
            raise SurfAgentError(f"could not find new browser page titled {SURF_AGENT_WINDOW_TITLE!r}; before={sorted(before_ids)} after={[page.page_id for page in pages]}")
        return merge_page(AgentPage(owned_page.page_id, title=SURF_AGENT_WINDOW_TITLE), owned_page)

    def _find_owned_new_axi_page(self, candidates: Sequence[AgentPage]) -> AgentPage | None:
        matches: list[AgentPage] = []
        for page in candidates:
            self._select_axi_page(page.page_id)
            identity_output = self._run_axi_text(["eval", "JSON.stringify({title:document.title,href:location.href})"])
            if is_surf_agent_bootstrap_identity(identity_output):
                matches.append(page)
        if len(matches) == 1:
            return matches[0]
        return None

    def _wait_for_new_axi_page(self, before_ids: set[int]) -> list[AgentPage]:
        deadline = time.monotonic() + CHROME_NEW_WINDOW_TIMEOUT_S
        pages = self._axi_pages(allow_failure=False)
        while not any(page.page_id not in before_ids for page in pages) and time.monotonic() < deadline:
            # Chrome may register the new window slightly after the launcher exits.
            time.sleep(0.25)
            pages = self._axi_pages(allow_failure=False)
        return pages

    def _open_chrome_window(self, url: str) -> None:
        if not self.chrome_bin:
            raise SurfAgentError("could not find Chrome executable for dedicated Surf Agent window; set SURF_AGENT_CHROME_BIN")
        if self._uses_dedicated_chrome_profile():
            self._ensure_dedicated_chrome_running()
        command = [*shlex.split(self.chrome_bin), f"--class={self.chrome_class}"]
        if self._uses_dedicated_chrome_profile():
            command.append(f"--user-data-dir={self.chrome_profile_dir}")
        # Use a normal window for human-in-the-loop login/unblock UX: toolbar,
        # back/forward controls, and extension UI. Raw --app remains a possible
        # future mode if a bare app shell becomes more important than usability.
        command.extend(["--new-window", url])
        proc = self._subprocess_run(command, check=False, text=True, capture_output=True, timeout=CHROME_NEW_WINDOW_TIMEOUT_S)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "Chrome --new-window failed").strip()
            raise SurfAgentError(detail)

    def _refresh_and_save_axi_state(self, fallback: AgentPage) -> AgentPage:
        page = self._refresh_axi_page(fallback) or fallback
        self._save_axi_state(page)
        return page

    def _refresh_axi_page(self, fallback: AgentPage) -> AgentPage | None:
        pages = self._axi_pages(allow_failure=True)
        current = find_page(pages, fallback.page_id)
        return merge_page(fallback, current) if current else None

    def _select_axi_page(self, page_id: int, *, bring_to_front: bool = False) -> subprocess.CompletedProcess[str]:
        if bring_to_front:
            output = self._select_axi_page_via_bridge(page_id, bring_to_front=True)
            return subprocess.CompletedProcess(["selectpage", str(page_id)], 0, stdout=output, stderr="")
        output = self._run_axi_text(["selectpage", str(page_id)])
        return subprocess.CompletedProcess(["selectpage", str(page_id)], 0, stdout=output, stderr="")

    def _select_axi_page_via_bridge(self, page_id: int, *, bring_to_front: bool) -> str:
        args = {"pageId": page_id, "bringToFront": bring_to_front}
        try:
            return self.bridge_client.call_tool("select_page", args)
        except AxiBridgeUnavailable:
            if self._uses_dedicated_chrome_profile():
                self._ensure_dedicated_chrome_running()
            self._run_axi_cli_text(["start"])
            return self.bridge_client.call_tool("select_page", args)

    def _axi_pages(self, *, allow_failure: bool) -> list[AgentPage]:
        try:
            output = self._run_axi_text(["pages"])
        except SurfAgentError:
            if allow_failure:
                return []
            raise
        pages = parse_axi_pages(output)
        if not pages and output.strip() and not is_no_pages_output(output):
            raise SurfAgentError(f"could not parse browser pages output: {raw_prefix(output)}")
        return pages

    def _run_axi_text(self, args: Sequence[str]) -> str:
        bridge_output = self._run_axi_text_via_bridge(args)
        if bridge_output is not None:
            return bridge_output
        proc = self._run_axi_cli(args, check=False)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "browser command failed").strip()
            raise SurfAgentError(detail)
        return proc.stdout or ""

    def _run_axi_text_via_bridge(self, args: Sequence[str]) -> str | None:
        mapped = map_axi_cli_args_to_bridge(args)
        if mapped is None:
            return None
        tool_name, tool_args, formatter = mapped
        try:
            result = self.bridge_client.call_tool(tool_name, tool_args)
        except AxiBridgeUnavailable:
            if self._uses_dedicated_chrome_profile():
                self._ensure_dedicated_chrome_running()
            self._run_axi_cli_text(["start"])
            result = self.bridge_client.call_tool(tool_name, tool_args)
        return formatter(result)

    def _run_axi_cli_text(self, args: Sequence[str]) -> str:
        proc = self._run_axi_cli(args, check=False)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "browser command failed").strip()
            raise SurfAgentError(detail)
        return proc.stdout or ""

    def _run_axi_cli(self, args: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        command = [*shlex.split(self.axi_bin), *args]
        kwargs.setdefault("text", True)
        kwargs.setdefault("capture_output", True)
        kwargs.setdefault("timeout", self.command_timeout_s)
        if self._uses_dedicated_chrome_profile():
            self.chrome_profile_dir.mkdir(parents=True, exist_ok=True)
        kwargs.setdefault("env", default_axi_env(profile_dir=self.chrome_profile_dir, chrome_class=self.chrome_class, browser_url=self.browser_url))
        try:
            return self._subprocess_run(command, **kwargs)
        except subprocess.TimeoutExpired as exc:
            pretty = " ".join(args)
            raise SurfAgentError(
                f"browser command timed out after {self.command_timeout_s:g}s: {pretty}. browser bridge may be unavailable."
            ) from exc
        except FileNotFoundError as exc:
            raise SurfAgentError(f"browser helper executable not found: {shlex.split(self.axi_bin)[0]}") from exc

    def _load_axi_state(self) -> AgentPage | None:
        state = load_state_file(self.state_file)
        return state if isinstance(state, AgentPage) else None

    def _save_axi_state(self, page: AgentPage) -> None:
        save_state_file(self.state_file, page)

    def _ensure_dedicated_chrome_running(self) -> None:
        if self._chrome_debug_endpoint_ready():
            return
        if not self.chrome_bin:
            raise SurfAgentError("could not find Chrome executable for dedicated Surf Agent profile; set SURF_AGENT_CHROME_BIN")
        self.chrome_profile_dir.mkdir(parents=True, exist_ok=True)
        command = [
            *shlex.split(self.chrome_bin),
            f"--class={self.chrome_class}",
            f"--user-data-dir={self.chrome_profile_dir}",
            f"--remote-debugging-port={self.chrome_debug_port}",
            "--no-first-run",
            "--no-startup-window",
        ]
        self._subprocess_popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + CHROME_NEW_WINDOW_TIMEOUT_S
        while time.monotonic() < deadline:
            if self._chrome_debug_endpoint_ready():
                return
            time.sleep(0.25)
        raise SurfAgentError(f"Chrome did not expose debug endpoint at {self.browser_url}")

    def _chrome_debug_endpoint_ready(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.browser_url}/json/version", timeout=1.0) as response:
                return response.status == 200
        except (OSError, urllib.error.URLError, TimeoutError):
            return False

    def _uses_dedicated_chrome_profile(self) -> bool:
        return uses_dedicated_chrome_profile(os.environ)

    def _subprocess_run(self, command: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.run(command, **kwargs)

    def _subprocess_popen(self, command: Sequence[str], **kwargs: Any) -> subprocess.Popen[str]:
        return subprocess.Popen(command, **kwargs)


def default_state_file(*, thread: str = DEFAULT_THREAD, state_dir: Path | None = None) -> Path:
    return default_state_dir(state_dir=state_dir) / f"{safe_thread_name(thread)}.json"


def default_state_dir(*, state_dir: Path | None = None) -> Path:
    return state_dir or skill_state_dir()


def skill_dir() -> Path:
    return Path(__file__).resolve().parents[2]


def skill_state_dir() -> Path:
    return skill_dir() / ".state"


def default_chrome_profile_dir() -> Path:
    value = os.environ.get("SURF_AGENT_CHROME_PROFILE_DIR") or os.environ.get("CHROME_DEVTOOLS_AXI_USER_DATA_DIR")
    if value:
        return Path(value).expanduser()
    return skill_dir() / "chrome-profile"


def safe_thread_name(thread: str) -> str:
    value = thread.strip() or DEFAULT_THREAD
    allowed = all(ch.isalnum() or ch in {"-", "_", "."} for ch in value)
    if not allowed or value in {".", ".."} or value.startswith("."):
        raise SurfAgentError("--thread may contain only letters, numbers, '.', '-', and '_' and must not start with '.'", exit_code=2)
    return value


def parse_timeout_env() -> float:
    value = os.environ.get("SURF_AGENT_AXI_TIMEOUT", "") or os.environ.get("SURF_AGENT_COMMAND_TIMEOUT", "")
    if not value:
        return DEFAULT_AXI_TIMEOUT_S
    try:
        timeout = float(value)
    except ValueError as exc:
        raise SurfAgentError("SURF_AGENT_AXI_TIMEOUT must be a number", exit_code=2) from exc
    if timeout <= 0:
        raise SurfAgentError("SURF_AGENT_AXI_TIMEOUT must be greater than zero", exit_code=2)
    return timeout


def parse_port_env(name: str, default: str) -> int:
    raw = os.environ.get(name, default)
    value = coerce_int(raw)
    if value is None or value <= 0 or value > 65535:
        raise SurfAgentError(f"{name} must be a TCP port number", exit_code=2)
    return value


def default_axi_env(*, profile_dir: Path | None = None, chrome_class: str = DEFAULT_CHROME_CLASS, browser_url: str | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("CHROME_DEVTOOLS_AXI_PORT", DEFAULT_AXI_PORT)
    if uses_dedicated_chrome_profile(env):
        env.setdefault("CHROME_DEVTOOLS_AXI_BROWSER_URL", browser_url or f"http://127.0.0.1:{DEFAULT_CHROME_DEBUG_PORT}")
    return env


def uses_dedicated_chrome_profile(env: dict[str, str]) -> bool:
    return env.get("CHROME_DEVTOOLS_AXI_AUTO_CONNECT") != "1" and not env.get("CHROME_DEVTOOLS_AXI_BROWSER_URL")


def chrome_args_with_class(raw_args: str, chrome_class: str) -> str:
    args = raw_args.split()
    if not any(arg.startswith("--class=") for arg in args):
        args.append(f"--class={chrome_class}")
    return " ".join(args)


def find_chrome_bin() -> str | None:
    for candidate in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "brave-browser", "microsoft-edge"):
        found = shutil.which(candidate)
        if found:
            return found
    return None


def surf_agent_app_url() -> str:
    # Bootstrap identity for adopting the newly opened thread window before navigation.
    # If raw --app mode returns later, this short URL also keeps app-id manageable.
    html = f"<title>{SURF_AGENT_WINDOW_TITLE}</title>{SURF_AGENT_WINDOW_TITLE}"
    return "data:text/html," + quote(html, safe="")


def surf_agent_welcome_url() -> str:
    html = (
        "<!doctype html><html><head>"
        "<meta charset='utf-8'>"
        f"<title>{SURF_AGENT_WINDOW_TITLE}</title>"
        "<style>body{font-family:system-ui,sans-serif;margin:48px;max-width:760px;line-height:1.5}"
        "code{background:#eee;padding:2px 6px;border-radius:4px}</style>"
        "</head><body>"
        f"<h1>{SURF_AGENT_WINDOW_TITLE}</h1>"
        "<p>Dedicated browser window managed by <code>surf-agent</code>.</p>"
        "<p>This window is safe to target with window rules. It will navigate when you run <code>open &lt;url&gt;</code>.</p>"
        "</body></html>"
    )
    return "data:text/html;charset=utf-8," + quote(html, safe="")


def first_command(args: Sequence[str]) -> str | None:
    for arg in args:
        if arg == "--":
            continue
        if not arg.startswith("-"):
            return arg
    return None


def forbidden_message(command: str | None) -> str:
    if command in {"ai", "chatgpt", "claude", "gemini", "perplexity", "grok", "aistudio", "aistudio.build"}:
        return f"`{command}` is not supported by surf-agent."
    if command == "window.new":
        return "`window.new` is managed by surf-agent; use `surf-agent new`."
    return f"`{command}` violates one-page-per-thread policy."


def coerce_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_screenshot_output(values: Sequence[str]) -> str:
    if len(values) == 1:
        return values[0]
    if len(values) == 2 and values[0] == "--output":
        return values[1]
    raise SurfAgentError("screenshot requires a path or --output <path>", exit_code=2)


BridgeFormatter = Any
BridgeMapping = tuple[str, dict[str, Any], BridgeFormatter]


def map_axi_cli_args_to_bridge(args: Sequence[str]) -> BridgeMapping | None:
    if not args:
        return None
    command = args[0]
    values = list(args[1:])
    if command == "pages":
        return "list_pages", {}, format_bridge_identity
    if command == "selectpage" and len(values) == 1:
        return "select_page", {"pageId": parse_page_id_arg(values[0])}, format_bridge_empty
    if command == "open" and len(values) == 1:
        return "navigate_page", {"type": "url", "url": values[0]}, format_bridge_navigation
    if command == "eval" and values:
        return "evaluate_script", {"function": wrap_script_expression(" ".join(values))}, format_bridge_eval_result
    if command == "text" and not values:
        return "evaluate_script", {"function": "() => (document.body.innerText)"}, format_bridge_text_result
    if command == "snapshot" and not values:
        return "take_snapshot", {}, format_bridge_identity
    if command == "closepage" and len(values) == 1:
        return "close_page", {"pageId": parse_page_id_arg(values[0])}, format_bridge_identity
    if command == "screenshot" and len(values) == 1:
        return "take_screenshot", {"filePath": values[0]}, lambda result: f"screenshot: {values[0]}\n" if not result else result
    if command == "back" and not values:
        return "navigate_page", {"type": "back"}, format_bridge_identity
    if command == "wait" and len(values) == 1:
        target = values[0]
        if re.fullmatch(r"\d+", target):
            return "evaluate_script", {"function": f"() => new Promise(r => setTimeout(() => r({json.dumps(target)}), {int(target)}))"}, lambda _result: f"waited: {target}\n"
        return "wait_for", {"text": [target]}, lambda _result: f"waited: {target}\n"
    if command == "fill" and len(values) >= 2:
        return "fill", {"uid": values[0].removeprefix("@"), "value": " ".join(values[1:])}, format_bridge_identity
    if command == "type" and values:
        return "type_text", {"text": " ".join(values)}, format_bridge_identity
    if command == "press" and len(values) == 1:
        return "press_key", {"key": values[0]}, format_bridge_identity
    if command == "scroll" and len(values) == 1:
        scroll_fn = {"up": "window.scrollBy(0, -500)", "down": "window.scrollBy(0, 500)", "top": "window.scrollTo(0, 0)", "bottom": "window.scrollTo(0, document.body.scrollHeight)"}.get(values[0])
        if scroll_fn:
            return "evaluate_script", {"function": f"() => {{ {scroll_fn}; return true; }}"}, format_bridge_identity
    if command == "click" and len(values) == 1:
        return "click", {"uid": values[0].removeprefix("@")}, format_bridge_identity
    return None


def parse_page_id_arg(value: str) -> int:
    page_id_value = coerce_int(value)
    if page_id_value is None:
        raise SurfAgentError(f"invalid browser page id: {value}", exit_code=2)
    return page_id_value


def format_bridge_identity(result: str) -> str:
    return result or ""


def format_bridge_empty(_result: str) -> str:
    return ""


def format_bridge_navigation(result: str) -> str:
    return result or ""


def strip_axi_page_list(output: str) -> str:
    lines = output.splitlines(keepends=True)
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("## Pages") or re.match(r"^pages\[\d+\]", stripped, flags=re.IGNORECASE):
            return "".join(lines[:index]).rstrip() + ("\n" if index else "")
    return output


def format_bridge_eval_result(result: str) -> str:
    raw = parse_bridge_eval_result(result)
    return f"result: {raw}\n"


def format_bridge_text_result(result: str) -> str:
    raw = parse_bridge_eval_result(result)
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        text = raw
    else:
        text = decoded if isinstance(decoded, str) else raw
    return text + ("" if text.endswith("\n") else "\n")


def parse_bridge_eval_result(output: str) -> str:
    json_block = re.search(r"```json\n([\s\S]*?)\n```", output)
    if json_block:
        return json_block.group(1).strip()
    preamble = "Script ran on page and returned:"
    if preamble in output:
        return output[output.index(preamble) + len(preamble) :].strip()
    return output.strip()


def wrap_script_expression(source: str) -> str:
    trimmed = unwrap_no_arg_iife(source.strip())
    if re.match(r"^(async\s*)?(\(.*?\)\s*=>|[a-zA-Z_$][a-zA-Z0-9_$]*\s*=>|function[\s*(])", trimmed):
        return trimmed
    return f"() => ({trimmed})"


def unwrap_no_arg_iife(source: str) -> str:
    match = re.match(r"^\((.*)\)\s*\(\s*\)\s*;?$", source, flags=re.DOTALL)
    if not match:
        return source
    inner = match.group(1).strip()
    if re.match(r"^(async\s*)?(\(.*?\)\s*=>|[a-zA-Z_$][a-zA-Z0-9_$]*\s*=>|function[\s*(])", inner):
        return inner
    return source


def load_state_file(path: Path) -> AgentState | None:
    try:
        raw = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(raw, dict):
        return None

    page_id_value = coerce_int(raw.get("page_id") or raw.get("pageId"))
    if raw.get("backend") != "axi" or page_id_value is None:
        return None
    return AgentPage(page_id_value, string_or_none(raw.get("url")), string_or_none(raw.get("title")))


def save_state_file(path: Path, state: AgentState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"backend": "axi", "page_id": state.page_id, "url": state.url, "title": state.title}
    path.write_text(json.dumps(payload, sort_keys=True) + "\n")


def unlink_missing_ok(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def raw_prefix(output: str, limit: int = 200) -> str:
    text = " ".join(output.strip().split())
    return text[:limit] if text else "<empty>"


def axi_state_payload(*, thread: str, cached: AgentPage) -> dict[str, Any]:
    return {
        "backend": "axi",
        "thread": thread,
        "open": True,
        "page_id": cached.page_id,
        "url": cached.url,
        "title": cached.title,
    }


def merge_page(saved: AgentPage, current: AgentPage | None) -> AgentPage:
    if current is None:
        return saved
    title = current.title or saved.title
    if saved.title == SURF_AGENT_WINDOW_TITLE and current.title == "Surf":
        # AXI `pages` currently truncates titles at spaces in its CSV-ish output.
        title = saved.title
    return AgentPage(saved.page_id, url=current.url or saved.url, title=title)


def extract_page_id(output: str) -> int | None:
    patterns = [
        r"^\s*pageId\s*:\s*(\d+)\s*$",
        r"^\s*page[_ -]?id\s*[:=]\s*(\d+)\s*$",
        r"^\s*(?:opened|created)\s+page\s+(\d+)\b",
        r"^\s*page\s+(\d+)\b",
        r"^\s*[*-]\s*\[(\d+)\]",
    ]
    for pattern in patterns:
        match = re.search(pattern, output, flags=re.IGNORECASE | re.MULTILINE)
        if match:
            return coerce_int(match.group(1))
    return None


def parse_axi_eval_json(output: str) -> dict[str, Any] | None:
    value = parse_axi_eval_string(output)
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def is_surf_agent_bootstrap_identity(output: str) -> bool:
    value = parse_axi_eval_string(output)
    if value is None:
        return False
    if isinstance(value, dict):
        payload = value
    elif isinstance(value, str):
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            return False
    else:
        return False
    if not isinstance(payload, dict):
        return False
    return payload.get("title") == SURF_AGENT_WINDOW_TITLE and payload.get("href") == surf_agent_app_url()


def parse_axi_eval_string(output: str) -> Any:
    for line in output.splitlines():
        if not line.strip().lower().startswith("result:"):
            continue
        value: Any = line.split(":", 1)[1].strip()
        for _ in range(2):
            if not isinstance(value, str):
                break
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError:
                break
            value = decoded
        return value
    return None


def parse_axi_pages(output: str) -> list[AgentPage]:
    text = output.strip()
    if not text or is_no_pages_output(text):
        return []
    parsed = parse_axi_pages_json(text)
    if parsed:
        return parsed
    parsed = parse_mcp_pages_markdown(text)
    if parsed:
        return parsed
    pages: list[AgentPage] = []
    for line in text.splitlines():
        page = parse_axi_page_line(line)
        if page:
            pages.append(page)
    return pages


def is_no_pages_output(output: str) -> bool:
    normalized = " ".join(output.strip().lower().split())
    return normalized in {"no pages", "no pages open", "no open pages", "## pages no pages open"} or bool(re.search(r"pages\[\s*0\s*\]|0 pages open", output, flags=re.IGNORECASE))


def parse_mcp_pages_markdown(text: str) -> list[AgentPage]:
    pages: list[AgentPage] = []
    for line in text.splitlines():
        page = parse_mcp_page_line(line)
        if page is not None:
            pages.append(page)
    return pages


def parse_mcp_page_line(line: str) -> AgentPage | None:
    match = re.match(r"^\s*(?:[-*]\s*)?(\d+)\s*:\s*(.*?)\s*$", line)
    if not match:
        return None
    page_id_value = coerce_int(match.group(1))
    if page_id_value is None:
        return None
    rest = match.group(2).strip()
    rest = re.sub(r"\s*\[(?:selected|active)\]\s*$", "", rest, flags=re.IGNORECASE).strip()
    url = None
    title = rest or None
    url_match = re.search(r"\((https?://[^)]*|about:[^)]*|data:[^)]*|chrome://[^)]*)\)\s*$", rest)
    if url_match:
        url = url_match.group(1)
        title = rest[: url_match.start()].strip() or None
    return AgentPage(page_id_value, url=url, title=title)


def parse_axi_pages_json(text: str) -> list[AgentPage]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    return pages_from_json_value(data)


def pages_from_json_value(data: Any) -> list[AgentPage]:
    if isinstance(data, list):
        return [page for item in data if (page := page_from_json_object(item)) is not None]
    if isinstance(data, dict):
        page = page_from_json_object(data)
        if page is not None and any(key in data for key in ("page_id", "pageId", "id")):
            return [page]
        for key in ("pages", "items", "targets", "result"):
            value = data.get(key)
            pages = pages_from_json_value(value)
            if pages:
                return pages
    return []


def page_from_json_object(item: Any) -> AgentPage | None:
    if not isinstance(item, dict):
        return None
    page_id_value = coerce_int(item.get("page_id") or item.get("pageId") or item.get("id"))
    if page_id_value is None:
        return None
    return AgentPage(page_id_value, url=string_or_none(item.get("url")), title=string_or_none(item.get("title")))


def parse_axi_page_line(line: str) -> AgentPage | None:
    stripped = line.strip()
    if not stripped or is_axi_metadata_line(stripped):
        return None

    csv_page = parse_axi_csv_page_line(stripped)
    if csv_page is not None:
        return csv_page

    match = re.match(r"^\s*(?:[>*●-]\s*)?(?:Page\s+|page[_ -]?id[:= ]+|#)?\[?(\d+)\]?\s*(?::|-)?\s*(.*)$", line, flags=re.IGNORECASE)
    if not match:
        return None
    page_id_value = coerce_int(match.group(1))
    if page_id_value is None:
        return None
    rest = match.group(2).strip()
    url_match = re.search(r"(?:https?://|about:|data:|chrome://)\S+", rest)
    url = url_match.group(0).rstrip(",)") if url_match else None
    title = None
    quoted = re.search(r"[\"']([^\"']+)[\"']", rest)
    if quoted:
        title = quoted.group(1)
    elif url:
        before_url = rest[: url_match.start()].strip(" -:|") if url_match else ""
        after_url = rest[url_match.end() :].strip(" -:|") if url_match else ""
        title = before_url or after_url or None
    elif rest:
        title = rest.strip(" -:|") or None
    return AgentPage(page_id_value, url=url, title=title)


def is_axi_metadata_line(line: str) -> bool:
    return bool(re.match(r"^(?:pages|help)\[", line, flags=re.IGNORECASE))


def parse_axi_csv_page_line(line: str) -> AgentPage | None:
    try:
        row = next(csv.reader([line]))
    except csv.Error:
        return None
    if len(row) < 2:
        return None
    page_id_value = coerce_int(row[0].strip())
    if page_id_value is None:
        return None

    cells = [cell.strip() for cell in row[1:]]
    if cells and cells[-1].lower() in {"true", "false"}:
        cells = cells[:-1]
    cells = [cell for cell in cells if cell]
    if not cells:
        return AgentPage(page_id_value)

    url = next((cell for cell in cells if looks_like_url(cell)), None)
    title = next((cell for cell in cells if cell != url), None)
    if url is None and len(cells) == 1:
        title = cells[0]
    return AgentPage(page_id_value, url=url, title=title)


def looks_like_url(value: str) -> bool:
    return bool(re.match(r"^(?:https?://|about:|data:|chrome://)", value))


def find_page(pages: Sequence[AgentPage], wanted_id: int) -> AgentPage | None:
    for page in pages:
        if page.page_id == wanted_id:
            return page
    return None


SnapshotMode = str


def parse_snapshot_flags(args: Sequence[str]) -> SnapshotMode:
    values = list(args)
    if not values or values[0] != "snapshot":
        raise SurfAgentError("snapshot flags require snapshot command", exit_code=2)
    flags = values[1:]
    if not flags:
        return "snapshot"
    if flags == ["--baseline"]:
        return "baseline"
    if flags == ["--diff"]:
        return "diff"
    if "--baseline" in flags and "--diff" in flags:
        raise SurfAgentError("snapshot --baseline and --diff conflict", exit_code=2)
    raise SurfAgentError("usage: snapshot [--baseline | --diff]", exit_code=2)


def capture_snapshot(agent: SurfAgent) -> SnapshotCapture:
    page = agent._require_current_axi_page()
    text = agent._run_axi_text(["snapshot"])
    # Read live metadata after the snapshot so origin gating is based on the
    # current page, without persisting any hidden snapshot baseline state.
    current = capture_current_page_metadata(agent, fallback=page)
    return SnapshotCapture(
        text=text,
        page_id=current.page_id,
        url=current.url,
        title=current.title,
        origin=url_origin(current.url),
        url_without_fragment=url_without_fragment(current.url),
    )


def capture_current_page_metadata(agent: SurfAgent, *, fallback: AgentPage) -> AgentPage:
    try:
        output = agent._run_axi_text(["eval", "JSON.stringify({title:document.title,url:location.href})"])
    except SurfAgentError:
        # Metadata only gates noisy diffs; a successful snapshot should not fail
        # because this auxiliary read is unavailable.
        return fallback
    parsed = parse_axi_eval_json(output)
    if parsed is None:
        return fallback
    return AgentPage(
        fallback.page_id,
        url=string_or_none(parsed.get("url")) or fallback.url,
        title=string_or_none(parsed.get("title")) or fallback.title,
    )


def url_origin(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def url_without_fragment(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    return urlunparse(parsed._replace(fragment=""))


def unified_snapshot_diff(before: SnapshotCapture, after: SnapshotCapture) -> str:
    return "".join(
        difflib.unified_diff(
            before.text.splitlines(keepends=True),
            after.text.splitlines(keepends=True),
            fromfile="baseline",
            tofile="current",
        )
    )


def count_diff_hunks(diff_text: str) -> int:
    return sum(1 for line in diff_text.splitlines() if line.startswith("@@"))


def choose_snapshot_diff(before: SnapshotCapture | None, after: SnapshotCapture) -> SnapshotDiffDecision:
    if before is None:
        return SnapshotDiffDecision(snapshot_fallback_output(after.text, "no baseline"), used_diff=False, reason="no baseline")
    if before.page_id != after.page_id:
        return SnapshotDiffDecision(snapshot_fallback_output(after.text, "page changed"), used_diff=False, reason="page changed")
    if before.origin and after.origin and before.origin != after.origin:
        return SnapshotDiffDecision(snapshot_fallback_output(after.text, "origin changed"), used_diff=False, reason="origin changed")

    diff_text = unified_snapshot_diff(before, after)
    if not diff_text:
        return SnapshotDiffDecision(format_snapshot_header("diff", "no changes"), used_diff=True, reason="no changes")

    diff_chars = len(diff_text)
    full_chars = len(after.text)
    saved_chars = full_chars - diff_chars
    hunk_count = count_diff_hunks(diff_text)

    if diff_chars > full_chars * SNAPSHOT_DIFF_MAX_RATIO:
        return SnapshotDiffDecision(snapshot_fallback_output(after.text, "diff too large"), used_diff=False, reason="diff too large")
    if saved_chars < SNAPSHOT_DIFF_MIN_SAVED_CHARS:
        return SnapshotDiffDecision(snapshot_fallback_output(after.text, f"saved chars < {SNAPSHOT_DIFF_MIN_SAVED_CHARS}"), used_diff=False, reason="saved chars too small")
    if hunk_count > SNAPSHOT_DIFF_MAX_HUNKS:
        return SnapshotDiffDecision(snapshot_fallback_output(after.text, f"hunks > {SNAPSHOT_DIFF_MAX_HUNKS}"), used_diff=False, reason="too many hunks")
    return SnapshotDiffDecision(format_snapshot_header("diff", "") + diff_text, used_diff=True, reason="")


def snapshot_fallback_output(snapshot_text: str, reason: str) -> str:
    return format_snapshot_header("fallback", reason) + snapshot_text


def format_snapshot_header(kind: str, reason: str) -> str:
    if not reason:
        return ""
    label = "snapshot-diff" if kind == "diff" else "snapshot fallback"
    return f"# {label}: {reason}\n"


DO_SEPARATORS = {"::", "--then"}
DO_SHELL_OPERATORS = {"|", "&&", "||", ";"}
DO_FORBIDDEN_COMMANDS = (MANAGEMENT_COMMANDS - {"state"}) | {"list"}
def run_do(agent: SurfAgent, *, thread: str, argv: Sequence[str], stdin: Any = None, stdout: Any = None, stderr: Any = None) -> int:
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    try:
        options, steps = parse_do_invocation(argv, stdin=stdin)
    except SurfAgentError as exc:
        print(f"surf-agent: {exc}", file=stderr)
        return exc.exit_code

    context = DoContext()
    emitted: list[tuple[int, DoStep, str]] = []
    json_records: list[dict[str, Any]] = []
    for index, step in enumerate(steps, start=1):
        try:
            output = execute_do_step(agent, step, thread=thread, context=context)
        except SurfAgentError as exc:
            if options.jsonl:
                json_records.append(do_error_record(index, step, exc))
                write_jsonl_records(json_records, stdout)
            else:
                print(f"surf-agent do: step {index} `{step.display}` failed: {exc}", file=stderr)
            return exc.exit_code

        if should_emit_step(step, index=index, total=len(steps), options=options):
            if options.jsonl:
                json_records.append(do_success_record(index, step, output))
            else:
                emitted.append((index, step, output))

    if options.jsonl:
        write_jsonl_records(json_records, stdout)
    elif not options.quiet:
        write_plain_do_outputs(emitted, stdout)
    return 0


def parse_do_invocation(argv: Sequence[str], *, stdin: Any) -> tuple[DoOptions, list[DoStep]]:
    args = list(argv)
    jsonl = False
    quiet = False
    while args and args[0] in {"--jsonl", "--quiet"}:
        flag = args.pop(0)
        if flag == "--jsonl":
            jsonl = True
        elif flag == "--quiet":
            quiet = True
    options = DoOptions(jsonl=jsonl, quiet=quiet)

    if not args or args == ["-"]:
        if hasattr(stdin, "isatty") and stdin.isatty():
            raise SurfAgentError("do requires stdin script or steps separated by ::", exit_code=2)
        return options, parse_do_script(stdin.read())
    return options, parse_do_argv_steps(args)


def parse_do_script(script: str) -> list[DoStep]:
    steps: list[DoStep] = []
    for line_number, line in enumerate(script.splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            tokens = shlex.split(line, comments=False)
        except ValueError as exc:
            raise SurfAgentError(f"do line {line_number}: {exc}", exit_code=2) from exc
        if not tokens:
            continue
        steps.append(parse_do_step(tokens, context=f"do line {line_number}"))
    if not steps:
        raise SurfAgentError("do script is empty", exit_code=2)
    return steps


def parse_do_argv_steps(args: Sequence[str]) -> list[DoStep]:
    steps: list[DoStep] = []
    current: list[str] = []
    for token in args:
        if token in DO_SHELL_OPERATORS:
            raise SurfAgentError("use :: or --then between do steps; shell operators chain separate surf-agent invocations", exit_code=2)
        if token in DO_SEPARATORS:
            if not current:
                raise SurfAgentError("empty do step", exit_code=2)
            steps.append(parse_do_step(current, context="do"))
            current = []
            continue
        current.append(token)
    if current:
        steps.append(parse_do_step(current, context="do"))
    if not steps:
        raise SurfAgentError("do chain is empty", exit_code=2)
    return steps


def parse_do_step(tokens: Sequence[str], *, context: str) -> DoStep:
    args: list[str] = []
    emit = False
    quiet = False
    literal = False
    for token in tokens:
        if not literal and token == "--":
            literal = True
            continue
        if not literal and token == "--emit":
            emit = True
            continue
        if not literal and token == "--quiet":
            quiet = True
            continue
        args.append(token)
    if emit and quiet:
        raise SurfAgentError(f"{context}: --emit and --quiet conflict", exit_code=2)
    if not args:
        raise SurfAgentError(f"{context}: empty command", exit_code=2)
    command = args[0]
    if command in DO_FORBIDDEN_COMMANDS:
        raise SurfAgentError(f"{context}: `{command}` is not allowed inside do", exit_code=2)
    return DoStep(args=args, emit=emit, quiet=quiet)


def execute_do_step(agent: SurfAgent, step: DoStep, *, thread: str, context: DoContext) -> str:
    if step.command == "state":
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            agent.print_state(thread=thread)
        return output.getvalue()
    if step.command == "snapshot":
        mode = parse_snapshot_flags(step.args)
        if mode == "baseline":
            context.snapshot_baseline = capture_snapshot(agent)
            return ""
        if mode == "diff":
            current = capture_snapshot(agent)
            decision = choose_snapshot_diff(context.snapshot_baseline, current)
            context.snapshot_baseline = current
            return decision.output
    return agent.execute_in_window(step.args)


def should_emit_step(step: DoStep, *, index: int, total: int, options: DoOptions) -> bool:
    if options.quiet or step.quiet:
        return False
    return step.emit or index == total


def do_success_record(index: int, step: DoStep, output: str) -> dict[str, Any]:
    return {"step": index, "command": step.command, "status": "success", "output": output}


def do_error_record(index: int, step: DoStep, error: SurfAgentError) -> dict[str, Any]:
    return {"step": index, "command": step.command, "status": "error", "error": {"type": "runtime" if error.exit_code == 1 else "usage", "message": str(error)}}


def write_jsonl_records(records: Sequence[dict[str, Any]], stdout: Any) -> None:
    for record in records:
        print(json.dumps(record, sort_keys=True), file=stdout)


def write_plain_do_outputs(outputs: Sequence[tuple[int, DoStep, str]], stdout: Any) -> None:
    nonempty = [(index, step, output) for index, step, output in outputs if output]
    if not nonempty:
        return
    if len(nonempty) == 1:
        output = nonempty[0][2]
        print(output, end="" if output.endswith("\n") else "\n", file=stdout)
        return
    for index, step, output in nonempty:
        command_json = json.dumps(step.display)
        fence = markdown_fence_for(command_json, output)
        print(f'{fence}surf-step index={index} command={command_json}', file=stdout)
        print(output, end="" if output.endswith("\n") else "\n", file=stdout)
        print(fence, file=stdout)


def markdown_fence_for(*values: str) -> str:
    longest_tilde_run = 0
    for value in values:
        for match in re.finditer(r"~+", value):
            longest_tilde_run = max(longest_tilde_run, len(match.group(0)))
    return "~" * max(3, longest_tilde_run + 1)


def parse_agent_args(argv: Sequence[str]) -> tuple[AgentConfig, list[str]]:
    thread = DEFAULT_THREAD
    rest = list(argv)
    i = 0
    while i < len(rest):
        arg = rest[i]
        if arg == "--":
            return AgentConfig(thread=safe_thread_name(thread)), rest[i + 1 :]
        if arg == "--thread":
            if i + 1 >= len(rest):
                raise SurfAgentError(f"{arg} requires a value", exit_code=2)
            thread = rest[i + 1]
            del rest[i : i + 2]
            continue
        if arg.startswith("--thread="):
            thread = arg.split("=", 1)[1]
            del rest[i]
            continue
        break
    return AgentConfig(thread=safe_thread_name(thread)), rest


def print_help(stream: Any) -> None:
    stream.write(
        "surf-agent: threaded browser helper using a persistent browser bridge\n\n"
        "Usage:\n"
        "  surf-agent [--thread ID] state                 print current page state; does not open a page\n"
        "  surf-agent list                                list remembered browser threads and clean stale entries\n"        "  surf-agent [--thread ID] new                   replace/create dedicated thread window, print page id\n"
        "  surf-agent [--thread ID] close                 close remembered thread page/window; browser bridge stays alive\n"
        "  surf-agent [--thread ID] focus                 select remembered thread page\n"
        "  surf-agent profile show                         print dedicated profile configuration JSON\n"
        "  surf-agent profile open [url]                   open dedicated profile without automation/debug port\n"
        "  surf-agent close-all                           close all remembered thread pages/windows\n"
        "  surf-agent close-matching <glob>               close remembered thread pages/windows whose thread names match\n"
        "  surf-agent [--thread ID] reset                 clear thread state without closing page\n"
        "  surf-agent [--thread ID] bridge-stop           explicit destructive browser bridge stop\n"
        "  surf-agent [--thread ID] do [-]                run newline-separated steps from stdin\n"
        "  surf-agent [--thread ID] <command...>          run supported browser command in thread page\n\n"
        "Supported browser commands:\n"
        "  open <url>, snapshot, text, eval <code>, click <target>, fill <target> <text>, type <text>,\n"
        "  press <key>, scroll up|down|top|bottom, screenshot [--output] <path>, back, wait <ms|text>.\n\n"
        "Examples:\n"
        "  surf-agent --thread main state\n"
        "  surf-agent --thread main open https://example.com\n"
        "  surf-agent --thread main snapshot\n"
        "  printf 'open https://example.com\\nsnapshot\\n' | surf-agent --thread main do\n"
        "  surf-agent profile open https://x.com\n"
        "  surf-agent --thread docs screenshot --output /tmp/shot.png\n"
        "  surf-agent close-matching 'agent-run-*'\n\n"
        "State: skill-local .state/<thread>.json plus chrome-profile/.\n"
        "Browser bridge: dedicated profile env is embedded; setup/login may be needed once. New threads start in a window titled Surf Agent.\n"
    )


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        config, argv = parse_agent_args(argv)
        agent = SurfAgent(thread=config.thread)
        if not argv or argv[0] in {"help", "--help", "-h"}:
            print_help(sys.stdout)
            return 0 if argv else 2
        command = argv[0]
        if command == "state":
            agent.print_state(thread=config.thread)
            return 0
        if command == "list":
            agent.print_list()
            return 0
        if command == "page-id":
            agent.print_page_id()
            return 0
        if command == "new":
            agent.print_page_id(force_new=True)
            return 0
        if command == "reset":
            agent.reset_state()
            return 0
        if command == "close":
            return agent.close()
        if command == "focus":
            return agent.focus()
        if command == "profile":
            if len(argv) < 2:
                raise SurfAgentError("profile requires subcommand: show or open", exit_code=2)
            if argv[1] == "show" and len(argv) == 2:
                agent.print_profile_show()
                return 0
            if argv[1] == "open" and len(argv) <= 3:
                return agent.profile_open(argv[2] if len(argv) == 3 else "about:blank")
            raise SurfAgentError("usage: surf-agent profile show | profile open [url]", exit_code=2)
        if command == "close-all":
            return agent.close_matching("*")
        if command == "close-matching":
            if len(argv) < 2:
                raise SurfAgentError("close-matching requires a thread glob pattern", exit_code=2)
            return agent.close_matching(argv[1])
        if command == "bridge-stop":
            return agent.bridge_stop()
        if command == "do":
            return run_do(agent, thread=config.thread, argv=argv[1:])
        if command == "--":
            argv = argv[1:]
        return agent.run_in_window(argv)
    except SurfAgentError as exc:
        print(f"surf-agent: {exc}", file=sys.stderr)
        return exc.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
