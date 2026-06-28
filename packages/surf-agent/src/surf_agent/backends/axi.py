from __future__ import annotations

import csv
import fnmatch
import json
import os
import re
import shlex
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import quote

from ..constants import AXI_BRIDGE_PID_FILE, CHROME_NEW_WINDOW_TIMEOUT_S, DEFAULT_AXI_PORT, SURF_AGENT_WINDOW_TITLE
from ..errors import BridgeUnavailable, SurfAgentError
from .base import AgentPage, ScreenshotOptions


class AxiBridgeUnavailable(BridgeUnavailable):
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
        pid = _coerce_int(pid_data.get("pid")) if pid_data else None
        pid_port = _coerce_int(pid_data.get("port")) if pid_data else None
        env = self._read_process_env(pid) if pid is not None and pid_port == port else None
        mismatch = self._bridge_env_mismatch(env)
        if mismatch:
            raise AxiBridgeConfigMismatch(mismatch)

    def _bridge_env_mismatch(self, env: dict[str, str] | None) -> str | None:
        if self.expected_profile_dir is None:
            return None
        expected_profile = str(self.expected_profile_dir)
        if env is None:
            return f"browser bridge is already running on port {os.environ.get('CHROME_DEVTOOLS_AXI_PORT', DEFAULT_AXI_PORT)}, but surf-agent cannot verify it uses the dedicated profile; run `surf-agent bridge stop`, then retry"
        browser_url = env.get("CHROME_DEVTOOLS_AXI_BROWSER_URL")
        if env.get("CHROME_DEVTOOLS_AXI_AUTO_CONNECT") == "1":
            return "browser bridge is running against an explicit/user Chrome connection; run `surf-agent bridge stop`, then retry so surf-agent can use its dedicated profile"
        if self.expected_browser_url is not None:
            if browser_url != self.expected_browser_url:
                return f"browser bridge is running against browser URL {browser_url!r}, expected {self.expected_browser_url!r}; run `surf-agent bridge stop`, then retry"
            return None
        if browser_url:
            return "browser bridge is running against an explicit/user Chrome connection; run `surf-agent bridge stop`, then retry so surf-agent can use its dedicated profile"
        if env.get("CHROME_DEVTOOLS_AXI_USER_DATA_DIR") != expected_profile:
            return f"browser bridge is running with profile {env.get('CHROME_DEVTOOLS_AXI_USER_DATA_DIR')!r}, expected {expected_profile!r}; run `surf-agent bridge stop`, then retry"
        if self.expected_chrome_class and not any(arg == f"--class={self.expected_chrome_class}" for arg in env.get("CHROME_DEVTOOLS_AXI_CHROME_ARGS", "").split()):
            return f"browser bridge is running without --class={self.expected_chrome_class}; run `surf-agent bridge stop`, then retry"
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
        return _coerce_int(data.get("port")) if data else None

    def _read_pid_data(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self.pid_file.read_text())
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None


class AxiBackend:
    name = "axi"

    def __init__(self, agent: Any) -> None:
        self.agent = agent

    def ensure_page(self, *, force_new: bool = False, url: str | None = None) -> Any:
        if force_new:
            self._close_remembered_axi_page()
        else:
            state_page = self._load_axi_state()
            if state_page:
                return state_page

        created = self._create_axi_page(url)
        self._save_axi_state(created)
        return created

    def print_page_id(self, *, force_new: bool = False) -> None:
        print(self.ensure_page(force_new=force_new).page_id)

    def print_state(self, *, thread: str) -> None:
        self._print_axi_state(thread=thread)

    def print_list(self) -> None:
        self._print_axi_list()

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

    def capture_snapshot(self) -> Any:
        page = self._require_current_axi_page()
        text = self._run_axi_text(["snapshot"])
        # Metadata only gates diff quality; keep state persistence out of snapshots.
        current = self.capture_page_metadata(fallback=page)
        return _cli().snapshot_capture_from_page(text=text, page=current)

    def capture_page_metadata(self, *, fallback: AgentPage) -> AgentPage:
        try:
            output = self._run_axi_text(["eval", "JSON.stringify({title:document.title,url:location.href})"])
        except SurfAgentError:
            return fallback
        parsed = parse_axi_eval_json(output)
        if parsed is None:
            return fallback
        return AgentPage(
            fallback.page_id,
            url=_string_or_none(parsed.get("url")) or fallback.url,
            title=_string_or_none(parsed.get("title")) or fallback.title,
        )

    def open(self, url: str) -> str:
        current = self._load_axi_state()
        if current:
            try:
                self._select_axi_page(current.page_id)
            except SurfAgentError:
                _unlink_missing_ok(self.agent.state_file)
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

    def new(self) -> str:
        page = self.ensure_page(force_new=True)
        return f"{page.page_id}\n"

    def snapshot(self) -> str:
        return self._run_current(["snapshot"])

    def text(self) -> str:
        return self._run_current(["text"])

    def click(self, target: str) -> str:
        return self._run_current(["click", target])

    def fill(self, target: str, text: str) -> str:
        return self._run_current(["fill", target, text])

    def type_text(self, text: str) -> str:
        return self._run_current(["type", text])

    def press(self, key: str) -> str:
        return self._run_current(["press", key])

    def scroll(self, direction: str) -> str:
        return self._run_current(["scroll", direction])

    def wait(self, target: str) -> str:
        return self._run_current(["wait", target])

    def back(self) -> str:
        return self._run_current(["back"])

    def screenshot(self, options: ScreenshotOptions) -> str:
        args = ["screenshot", options.path]
        if options.full_page:
            args = ["screenshot", "--full-page", options.path]
        return self._run_current(args)

    def evaluate(self, code: str) -> str:
        return self._run_current(["eval", code])

    def _run_current(self, axi_args: Sequence[str]) -> str:
        self._require_current_axi_page()
        return self._run_axi_text(axi_args)

    def _print_axi_state(self, *, thread: str) -> None:
        cli = _cli()
        cached = self._load_axi_state()
        if cached is None:
            print(json.dumps({"backend": "axi", "thread": thread, "open": False}, sort_keys=True))
            return
        try:
            page = self._current_axi_page_from_state(cached)
        except SurfAgentError:
            _unlink_missing_ok(self.agent.state_file)
            print(json.dumps({"backend": "axi", "thread": thread, "open": False}, sort_keys=True))
            return
        self._save_axi_state(page)
        print(json.dumps(axi_state_payload(thread=thread, cached=page), sort_keys=True))

    def _print_axi_list(self) -> None:
        cli = _cli()
        if not self.agent.state_dir.exists():
            print(json.dumps({"backend": "axi", "threads": []}, sort_keys=True))
            return

        threads: list[dict[str, Any]] = []
        for state_file in sorted(self.agent.state_dir.glob("*.json")):
            thread = state_file.stem
            cached = load_state_file(state_file)
            if cached is None:
                _unlink_missing_ok(state_file)
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
        self.agent.reset_state()
        return 0

    def _close_matching_axi(self, pattern: str) -> int:
        cli = _cli()
        pattern = pattern.strip()
        if not pattern:
            raise SurfAgentError("close-matching requires a thread glob pattern", exit_code=2)

        result: dict[str, Any] = {"pattern": pattern, "closed": [], "stale": [], "invalid": [], "failed": []}
        if not self.agent.state_dir.exists():
            print(json.dumps(result, sort_keys=True))
            return 0

        for state_file in sorted(self.agent.state_dir.glob("*.json")):
            thread = state_file.stem
            if not fnmatch.fnmatchcase(thread, pattern):
                continue
            cached = load_state_file(state_file)
            if cached is None:
                _unlink_missing_ok(state_file)
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
                _unlink_missing_ok(state_file)
                result["closed"].append(item)

        print(json.dumps(result, sort_keys=True))
        return 1 if result["failed"] else 0

    def _require_current_axi_page(self) -> Any:
        cli = _cli()
        page = self._load_axi_state()
        if page is None:
            raise SurfAgentError("no remembered browser page for this thread; run `surf-agent open <url>` or `surf-agent new` first")
        try:
            self._select_axi_page(page.page_id)
        except SurfAgentError as exc:
            _unlink_missing_ok(self.agent.state_file)
            raise SurfAgentError(str(exc)) from exc
        return page

    def _current_axi_page_from_state(self, page: Any) -> Any:
        cli = _cli()
        self._select_axi_page(page.page_id)
        output = self._run_axi_text(["eval", "JSON.stringify({title:document.title,url:location.href})"])
        parsed = parse_axi_eval_json(output)
        if parsed is None:
            return page
        return AgentPage(page.page_id, url=_string_or_none(parsed.get("url")) or page.url, title=_string_or_none(parsed.get("title")) or page.title)

    def _create_axi_page(self, url: str | None = None) -> Any:
        cli = _cli()
        page = self._new_dedicated_axi_window_page()
        if url is not None:
            return page
        welcome_url = _cli().surf_agent_welcome_url()
        self._select_axi_page(page.page_id)
        self._run_axi_text(["open", welcome_url])
        return AgentPage(page.page_id, url=welcome_url, title=SURF_AGENT_WINDOW_TITLE)

    def _new_dedicated_axi_window_page(self) -> Any:
        cli = _cli()
        before = self._axi_pages(allow_failure=True)
        before_ids = {page.page_id for page in before}
        self._open_chrome_window(surf_agent_app_url())
        pages = self._wait_for_new_axi_page(before_ids)
        new_pages = [page for page in pages if page.page_id not in before_ids]
        owned_page = self._find_owned_new_axi_page(new_pages)
        if owned_page is None:
            raise SurfAgentError(f"could not find new browser page titled {SURF_AGENT_WINDOW_TITLE!r}; before={sorted(before_ids)} after={[page.page_id for page in pages]}")
        return merge_page(AgentPage(owned_page.page_id, title=SURF_AGENT_WINDOW_TITLE), owned_page)

    def _find_owned_new_axi_page(self, candidates: Sequence[Any]) -> Any | None:
        matches: list[Any] = []
        for page in candidates:
            self._select_axi_page(page.page_id)
            identity_output = self._run_axi_text(["eval", "JSON.stringify({title:document.title,href:location.href})"])
            if is_surf_agent_bootstrap_identity(identity_output):
                matches.append(page)
        if len(matches) == 1:
            return matches[0]
        return None

    def _wait_for_new_axi_page(self, before_ids: set[int]) -> list[Any]:
        deadline = time.monotonic() + CHROME_NEW_WINDOW_TIMEOUT_S
        pages = self._axi_pages(allow_failure=False)
        while not any(page.page_id not in before_ids for page in pages) and time.monotonic() < deadline:
            # Chrome may register the new window slightly after the launcher exits.
            time.sleep(0.25)
            pages = self._axi_pages(allow_failure=False)
        return pages

    def _open_chrome_window(self, url: str) -> None:
        if not self.agent.chrome_bin:
            raise SurfAgentError("could not find Chrome executable for dedicated Surf Agent window; set SURF_AGENT_CHROME_BIN")
        if self.agent._uses_dedicated_chrome_profile():
            self._ensure_dedicated_chrome_running()
        command = [*shlex.split(self.agent.chrome_bin), f"--class={self.agent.chrome_class}"]
        if self.agent._uses_dedicated_chrome_profile():
            command.append(f"--user-data-dir={self.agent.chrome_profile_dir}")
        # Use a normal window for human-in-the-loop login/unblock UX: toolbar,
        # back/forward controls, and extension UI. Raw --app remains a possible
        # future mode if a bare app shell becomes more important than usability.
        command.extend(["--new-window", url])
        proc = self.agent._subprocess_run(command, check=False, text=True, capture_output=True, timeout=CHROME_NEW_WINDOW_TIMEOUT_S)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "Chrome --new-window failed").strip()
            raise SurfAgentError(detail)

    def _refresh_and_save_axi_state(self, fallback: Any) -> Any:
        page = self._refresh_axi_page(fallback) or fallback
        self._save_axi_state(page)
        return page

    def _refresh_axi_page(self, fallback: Any) -> Any | None:
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
            return self.agent.bridge_client.call_tool("select_page", args)
        except AxiBridgeUnavailable:
            if self.agent._uses_dedicated_chrome_profile():
                self._ensure_dedicated_chrome_running()
            self._run_axi_cli_text(["start"])
            return self.agent.bridge_client.call_tool("select_page", args)

    def _axi_pages(self, *, allow_failure: bool) -> list[Any]:
        cli = _cli()
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
            result = self.agent.bridge_client.call_tool(tool_name, tool_args)
        except AxiBridgeUnavailable:
            if self.agent._uses_dedicated_chrome_profile():
                self._ensure_dedicated_chrome_running()
            self._run_axi_cli_text(["start"])
            result = self.agent.bridge_client.call_tool(tool_name, tool_args)
        return formatter(result)

    def _run_axi_cli_text(self, args: Sequence[str]) -> str:
        proc = self._run_axi_cli(args, check=False)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "browser command failed").strip()
            raise SurfAgentError(detail)
        return proc.stdout or ""

    def _run_axi_cli(self, args: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        command = [*shlex.split(self.agent.axi_bin), *args]
        kwargs.setdefault("text", True)
        kwargs.setdefault("capture_output", True)
        kwargs.setdefault("timeout", self.agent.command_timeout_s)
        if self.agent._uses_dedicated_chrome_profile():
            self.agent.chrome_profile_dir.mkdir(parents=True, exist_ok=True)
        kwargs.setdefault(
            "env",
            _cli().default_axi_env(profile_dir=self.agent.chrome_profile_dir, chrome_class=self.agent.chrome_class, browser_url=self.agent.browser_url),
        )
        try:
            return self.agent._subprocess_run(command, **kwargs)
        except subprocess.TimeoutExpired as exc:
            pretty = " ".join(args)
            raise SurfAgentError(
                f"browser command timed out after {self.agent.command_timeout_s:g}s: {pretty}. browser bridge may be unavailable."
            ) from exc
        except FileNotFoundError as exc:
            raise SurfAgentError(f"browser helper executable not found: {shlex.split(self.agent.axi_bin)[0]}") from exc

    def _load_axi_state(self) -> Any | None:
        state = load_state_file(self.agent.state_file)
        return state if isinstance(state, AgentPage) else None

    def _save_axi_state(self, page: Any) -> None:
        save_state_file(self.agent.state_file, page)

    def _ensure_dedicated_chrome_running(self) -> None:
        if self.agent._chrome_debug_endpoint_ready():
            return
        if not self.agent.chrome_bin:
            raise SurfAgentError("could not find Chrome executable for dedicated Surf Agent profile; set SURF_AGENT_CHROME_BIN")
        self.agent.chrome_profile_dir.mkdir(parents=True, exist_ok=True)
        command = [
            *shlex.split(self.agent.chrome_bin),
            f"--class={self.agent.chrome_class}",
            f"--user-data-dir={self.agent.chrome_profile_dir}",
            f"--remote-debugging-port={self.agent.chrome_debug_port}",
            "--no-first-run",
            "--no-startup-window",
        ]
        self.agent._subprocess_popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + CHROME_NEW_WINDOW_TIMEOUT_S
        while time.monotonic() < deadline:
            if self.agent._chrome_debug_endpoint_ready():
                return
            time.sleep(0.25)
        raise SurfAgentError(f"Chrome did not expose debug endpoint at {self.agent.browser_url}")


def surf_agent_app_url() -> str:
    # Bootstrap identity for adopting the newly opened thread window before navigation.
    html = f"<title>{SURF_AGENT_WINDOW_TITLE}</title>{SURF_AGENT_WINDOW_TITLE}"
    return "data:text/html," + quote(html, safe="")


def strip_axi_page_list(output: str) -> str:
    lines = output.splitlines(keepends=True)
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("## Pages") or re.match(r"^pages\[\d+\]", stripped, flags=re.IGNORECASE):
            return "".join(lines[:index]).rstrip() + ("\n" if index else "")
    return output


def load_state_file(path: Path) -> AgentPage | None:
    try:
        raw = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(raw, dict):
        return None
    page_id_value = _coerce_int(raw.get("page_id") or raw.get("pageId"))
    if raw.get("backend") != "axi" or page_id_value is None:
        return None
    return AgentPage(page_id_value, _string_or_none(raw.get("url")), _string_or_none(raw.get("title")))


def save_state_file(path: Path, state: AgentPage) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"backend": "axi", "page_id": state.page_id, "url": state.url, "title": state.title}
    path.write_text(json.dumps(payload, sort_keys=True) + "\n")


def _unlink_missing_ok(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _string_or_none(value: Any) -> str | None:
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
            return _coerce_int(match.group(1))
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
    page_id_value = _coerce_int(match.group(1))
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
    page_id_value = _coerce_int(item.get("page_id") or item.get("pageId") or item.get("id"))
    if page_id_value is None:
        return None
    return AgentPage(page_id_value, url=_string_or_none(item.get("url")), title=_string_or_none(item.get("title")))


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
    page_id_value = _coerce_int(match.group(1))
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
    page_id_value = _coerce_int(row[0].strip())
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
        page_id = parse_page_id_arg(values[0])
        return "select_page", {"pageId": page_id}, lambda result: format_bridge_select_page(result, page_id)
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
    page_id_value = _coerce_int(value)
    if page_id_value is None:
        raise SurfAgentError(f"invalid browser page id: {value}", exit_code=2)
    return page_id_value


def format_bridge_identity(result: str) -> str:
    return result or ""


def format_bridge_empty(_result: str) -> str:
    return ""


def format_bridge_select_page(result: str, page_id: int) -> str:
    text = result.strip()
    if not text or text.lower() in {"selected", "focused"}:
        return ""
    if find_page(parse_axi_pages(text), page_id) is not None:
        return ""
    raise SurfAgentError(f"could not select browser page {page_id}")


def format_bridge_navigation(result: str) -> str:
    return result or ""


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


def _cli() -> Any:
    import surf_agent.cli as cli

    return cli


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
