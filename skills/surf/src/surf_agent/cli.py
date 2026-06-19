from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

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

MANAGEMENT_COMMANDS = {"help", "state", "list", "id", "window-id", "new", "reset", "forget", "close", "focus", "close-all", "close-matching"}
DEFAULT_THREAD = "default"


@dataclass(frozen=True)
class AgentConfig:
    thread: str = DEFAULT_THREAD


@dataclass(frozen=True)
class AgentWindow:
    window_id: int
    tab_id: int | None = None


class SurfAgentError(RuntimeError):
    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class SurfAgent:
    def __init__(self, *, surf_bin: str | None = None, state_file: Path | None = None, thread: str = DEFAULT_THREAD, state_dir: Path | None = None) -> None:
        self.surf_bin = surf_bin or os.environ.get("SURF_AGENT_BIN", "surf")
        self.state_file = state_file or default_state_file(thread=thread, state_dir=state_dir)
        self.state_dir = self.state_file.parent if state_file else default_state_dir(state_dir=state_dir)

    def ensure_window(self, *, force_new: bool = False) -> AgentWindow:
        if force_new:
            self._close_remembered_window()
        else:
            state_window = self._load_state()
            if state_window and self._validate_window(state_window):
                return state_window

        created = self._create_window()
        if not self._validate_window(created):
            raise SurfAgentError(f"created surf window {created.window_id}, but validation failed")
        self._save_state(created)
        return created

    def run_in_window(self, surf_args: Sequence[str]) -> int:
        if not surf_args:
            print_help(sys.stderr)
            return 2
        command = first_command(surf_args)
        if command in FORBIDDEN_COMMANDS:
            raise SurfAgentError(forbidden_message(command), exit_code=2)
        window = self.ensure_window()
        proc = self._subprocess_run([self.surf_bin, "--window-id", str(window.window_id), *surf_args], check=False)
        return proc.returncode

    def print_window_id(self, *, force_new: bool = False) -> None:
        print(self.ensure_window(force_new=force_new).window_id)

    def print_state(self, *, thread: str) -> None:
        cached = self._load_state()
        if cached is None:
            print(json.dumps({"thread": thread, "open": False}, sort_keys=True))
            return

        windows = self._list_windows(allow_failure=False)
        current = find_window(windows, cached.window_id)
        if current is None:
            unlink_missing_ok(self.state_file)
            print(json.dumps({"thread": thread, "open": False}, sort_keys=True))
            return

        page = self._page_state(cached.window_id)
        payload = {
            "thread": thread,
            "open": True,
            "window_id": cached.window_id,
            "tab_id": cached.tab_id,
            "url": page.get("url"),
            "title": page.get("title"),
        }
        print(json.dumps(payload, sort_keys=True))

    def print_list(self) -> None:
        if not self.state_dir.exists():
            print(json.dumps({"threads": []}, sort_keys=True))
            return

        windows = self._list_windows(allow_failure=False)
        threads: list[dict[str, Any]] = []
        for state_file in sorted(self.state_dir.glob("*.json")):
            thread = state_file.stem
            cached = load_state_file(state_file)
            if cached is None:
                unlink_missing_ok(state_file)
                continue
            current = find_window(windows, cached.window_id)
            if current is None:
                unlink_missing_ok(state_file)
                continue
            threads.append(
                state_payload(thread=thread, cached=cached, page=self._page_state(cached.window_id))
            )

        print(json.dumps({"threads": threads}, sort_keys=True))

    def forget(self) -> None:
        unlink_missing_ok(self.state_file)

    def close(self) -> int:
        return self._close_remembered_window()

    def focus(self) -> int:
        window = self._load_state()
        if not window:
            raise SurfAgentError("no remembered window for this thread", exit_code=1)
        proc = self._focus_window(window.window_id)
        return proc.returncode

    def _close_remembered_window(self) -> int:
        window = self._load_state()
        if not window:
            return 0
        proc = self._close_window(window.window_id)
        self.forget()
        return proc.returncode

    def close_matching(self, pattern: str) -> int:
        pattern = pattern.strip()
        if not pattern:
            raise SurfAgentError("close-matching requires a thread glob pattern", exit_code=2)

        result: dict[str, Any] = {"pattern": pattern, "closed": [], "stale": [], "invalid": [], "failed": []}
        if not self.state_dir.exists():
            print(json.dumps(result, sort_keys=True))
            return 0

        windows = self._list_windows(allow_failure=True)
        open_window_ids = {window_id(window) for window in windows}
        open_window_ids.discard(None)

        for state_file in sorted(self.state_dir.glob("*.json")):
            thread = state_file.stem
            if not fnmatch.fnmatchcase(thread, pattern):
                continue
            cached = load_state_file(state_file)
            if cached is None:
                unlink_missing_ok(state_file)
                result["invalid"].append({"thread": thread})
                continue
            item = {"thread": thread, "window_id": cached.window_id}
            if cached.window_id not in open_window_ids:
                unlink_missing_ok(state_file)
                result["stale"].append(item)
                continue
            proc = self._close_window(cached.window_id, quiet=True)
            if proc.returncode == 0:
                unlink_missing_ok(state_file)
                result["closed"].append(item)
            else:
                result["failed"].append(item)

        print(json.dumps(result, sort_keys=True))
        return 1 if result["failed"] else 0

    def _close_window(self, window_id: int, *, quiet: bool = False) -> subprocess.CompletedProcess[str]:
        kwargs: dict[str, Any] = {"check": False}
        if quiet:
            kwargs.update({"text": True, "capture_output": True})
        return self._subprocess_run([self.surf_bin, "window.close", str(window_id)], **kwargs)

    def _focus_window(self, window_id: int) -> subprocess.CompletedProcess[str]:
        return self._subprocess_run([self.surf_bin, "window.focus", str(window_id)], check=False)

    def _create_window(self) -> AgentWindow:
        before = self._list_windows(allow_failure=True)
        before_ids = {window_id(w) for w in before}
        before_ids.discard(None)

        # Open without URL so WM rules can identify/route the agent-owned window before navigation.
        # Keep it unfocused so agent browsing does not steal focus from the user.
        proc = self._run_json(["window.new", "--unfocused"])
        created_id = extract_window_id(proc)
        windows = self._list_windows(allow_failure=False)

        if created_id is None:
            after_ids = [window_id(w) for w in windows]
            new_ids = [wid for wid in after_ids if wid is not None and wid not in before_ids]
            if len(new_ids) == 1:
                created_id = new_ids[0]
        if created_id is None:
            raise SurfAgentError("could not determine new surf window id")

        window = find_window(windows, created_id)
        tab_id = first_tab_id(window) if window else None
        return AgentWindow(created_id, tab_id)

    def _validate_window(self, window: AgentWindow) -> bool:
        windows = self._list_windows(allow_failure=True)
        current = find_window(windows, window.window_id)
        if current is None:
            return False

        tabs = tabs_for_window(current)
        tab_count = tab_count_for_window(current, tabs)
        if tab_count > 1:
            raise SurfAgentError(
                f"agent window {window.window_id} has {tab_count} tabs; one tab per window required. Run `surf-agent close` or `surf-agent reset`.",
                exit_code=2,
            )
        if window.tab_id is not None and tabs:
            ids = {tab_id(tab) for tab in tabs}
            if window.tab_id not in ids:
                return False
        return True

    def _list_windows(self, *, allow_failure: bool) -> list[dict[str, Any]]:
        try:
            # Avoid `window.list --tabs`: surf can emit truncated/malformed JSON when
            # user-owned windows contain many tabs or very long URLs. `tabCount` is
            # enough for one-tab validation; page metadata is fetched per owned window.
            data = self._run_json(["window.list"])
        except SurfAgentError:
            if allow_failure:
                return []
            raise
        return parse_windows(data)

    def _page_state(self, window_id: int) -> dict[str, Any]:
        try:
            data = self._run_json(["--window-id", str(window_id), "page.state"])
        except SurfAgentError:
            return {}
        return data if isinstance(data, dict) else {}

    def _run_json(self, args: Sequence[str]) -> Any:
        command = [self.surf_bin, *args, "--json"]
        try:
            proc = self._subprocess_run(command, text=True, capture_output=True, check=False)
        except FileNotFoundError as exc:
            raise SurfAgentError("surf executable not found") from exc
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "surf command failed").strip()
            raise SurfAgentError(detail)
        stdout = (proc.stdout or "").strip()
        if not stdout:
            return None
        try:
            return json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise SurfAgentError(f"surf returned non-JSON output: {stdout[:200]}") from exc

    def _load_state(self) -> AgentWindow | None:
        return load_state_file(self.state_file)

    def _save_state(self, window: AgentWindow) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {"window_id": window.window_id, "tab_id": window.tab_id}
        self.state_file.write_text(json.dumps(payload, sort_keys=True) + "\n")

    def _subprocess_run(self, command: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.run(command, **kwargs)


def default_state_file(*, thread: str = DEFAULT_THREAD, state_dir: Path | None = None) -> Path:
    return default_state_dir(state_dir=state_dir) / f"{safe_thread_name(thread)}.json"


def default_state_dir(*, state_dir: Path | None = None) -> Path:
    return state_dir or skill_state_dir()


def skill_state_dir() -> Path:
    return Path(__file__).resolve().parents[2] / ".state"


def safe_thread_name(thread: str) -> str:
    value = thread.strip() or DEFAULT_THREAD
    allowed = all(ch.isalnum() or ch in {"-", "_", "."} for ch in value)
    if not allowed or value in {".", ".."} or value.startswith("."):
        raise SurfAgentError("--thread may contain only letters, numbers, '.', '-', and '_' and must not start with '.'", exit_code=2)
    return value


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
    return f"`{command}` violates one-tab-per-agent-window policy."


def coerce_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def load_state_file(path: Path) -> AgentWindow | None:
    try:
        raw = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    wid = coerce_int(raw.get("window_id")) if isinstance(raw, dict) else None
    if wid is None:
        return None
    return AgentWindow(wid, coerce_int(raw.get("tab_id")))


def unlink_missing_ok(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def state_payload(*, thread: str, cached: AgentWindow, page: dict[str, Any] | None = None) -> dict[str, Any]:
    page = page or {}
    return {
        "thread": thread,
        "open": True,
        "window_id": cached.window_id,
        "tab_id": cached.tab_id,
        "url": page.get("url"),
        "title": page.get("title"),
    }


def extract_window_id(data: Any) -> int | None:
    if isinstance(data, dict):
        for key in ("window_id", "windowId", "id"):
            value = coerce_int(data.get(key))
            if value is not None:
                return value
        for key in ("window", "result"):
            value = extract_window_id(data.get(key))
            if value is not None:
                return value
    if isinstance(data, str):
        match = re.search(r"(?:Window\s+|--window-id\s+)(\d+)", data)
        if match:
            return coerce_int(match.group(1))
    return None


def parse_windows(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("windows", "items", "result"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        nested = data.get("result")
        if isinstance(nested, dict):
            return parse_windows(nested)
    return []


def window_id(window: dict[str, Any]) -> int | None:
    return coerce_int(window.get("id") or window.get("windowId") or window.get("window_id"))


def tab_id(tab: dict[str, Any]) -> int | None:
    return coerce_int(tab.get("id") or tab.get("tabId") or tab.get("tab_id"))


def tabs_for_window(window: dict[str, Any]) -> list[dict[str, Any]]:
    tabs = window.get("tabs")
    return [tab for tab in tabs if isinstance(tab, dict)] if isinstance(tabs, list) else []


def tab_count_for_window(window: dict[str, Any], tabs: list[dict[str, Any]]) -> int:
    return coerce_int(window.get("tabCount") or window.get("tab_count")) or len(tabs)


def first_tab_id(window: dict[str, Any] | None) -> int | None:
    if not window:
        return None
    tabs = tabs_for_window(window)
    return tab_id(tabs[0]) if tabs else None


def find_window(windows: Sequence[dict[str, Any]], wanted_id: int) -> dict[str, Any] | None:
    for window in windows:
        if window_id(window) == wanted_id:
            return window
    return None


def parse_agent_args(argv: Sequence[str]) -> tuple[AgentConfig, list[str]]:
    thread = DEFAULT_THREAD
    rest = list(argv)
    i = 0
    while i < len(rest):
        arg = rest[i]
        if arg == "--":
            return AgentConfig(thread=safe_thread_name(thread)), rest[i + 1 :]
        if arg in {"--thread", "--thread-id"}:
            if i + 1 >= len(rest):
                raise SurfAgentError(f"{arg} requires a value", exit_code=2)
            thread = rest[i + 1]
            del rest[i : i + 2]
            continue
        if arg.startswith("--thread="):
            thread = arg.split("=", 1)[1]
            del rest[i]
            continue
        if arg.startswith("--thread-id="):
            thread = arg.split("=", 1)[1]
            del rest[i]
            continue
        break
    return AgentConfig(thread=safe_thread_name(thread)), rest


def print_help(stream: Any) -> None:
    stream.write(
        "surf-agent: agent-owned one-window helper for generic browser use\n\n"
        "Usage:\n"
        "  surf-agent [--thread ID] state              print current page state and clean stale entry\n"
        "  surf-agent list                             list threads and clean stale entries\n"
        "  surf-agent [--thread ID] new                replace/create thread window, print id\n"
        "  surf-agent [--thread ID] close              close remembered thread window\n"
        "  surf-agent [--thread ID] focus              focus remembered thread window for user handoff\n"
        "  surf-agent close-all                        close all remembered thread windows\n"
        "  surf-agent close-matching <glob>            close remembered thread windows whose thread names match\n"
        "  surf-agent [--thread ID] reset              forget thread state\n"
        "  surf-agent [--thread ID] <surf command...>  run browser command in thread window\n\n"
        "Examples:\n"
        "  surf-agent --thread main state\n"
        "  surf-agent list\n"
        "  surf-agent --thread main go https://example.com\n"
        "  surf-agent --thread main page.read --compact --depth 3\n"
        "  surf-agent --thread docs screenshot --output /tmp/shot.png\n"
        "  surf-agent close-matching 'agent-run-*'\n\n"
        "State: skill-local .state/<thread>.json.\n"
        "Rules: no tab.new/tab.switch/tab.close/window.new through this helper.\n"
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
        if command in {"id", "window-id"}:
            agent.print_window_id()
            return 0
        if command == "new":
            agent.print_window_id(force_new=True)
            return 0
        if command in {"reset", "forget"}:
            agent.forget()
            return 0
        if command == "close":
            return agent.close()
        if command == "focus":
            return agent.focus()
        if command == "close-all":
            return agent.close_matching("*")
        if command == "close-matching":
            if len(argv) < 2:
                raise SurfAgentError("close-matching requires a thread glob pattern", exit_code=2)
            return agent.close_matching(argv[1])
        if command == "--":
            argv = argv[1:]
        return agent.run_in_window(argv)
    except SurfAgentError as exc:
        print(f"surf-agent: {exc}", file=sys.stderr)
        return exc.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
