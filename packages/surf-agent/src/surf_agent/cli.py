from __future__ import annotations

import contextlib
import importlib.util
import difflib
import io
import json
import os
import re
import signal
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

from platformdirs import PlatformDirs

from .backends import (
    AxiBridgeClient,
    AxiBridgeConfigMismatch,
    AgentPage,
    ScreenshotOptions,
    AxiBridgeUnavailable,
    CamoufoxBridgeClient,
    PatchrightBridgeClient,
    extract_page_id,
    find_page,
    format_bridge_eval_result,
    format_bridge_text_result,
    is_no_pages_output,
    is_surf_agent_bootstrap_identity,
    load_state_file,
    merge_page,
    parse_axi_eval_json,
    parse_axi_eval_string,
    parse_axi_pages,
    parse_bridge_eval_result,
    save_state_file,
    strip_axi_page_list,
    surf_agent_app_url,
    unwrap_no_arg_iife,
    wrap_script_expression,
    create_backend,
    map_axi_cli_args_to_bridge,
)
from . import config as persisted_config
from .chrome_lifecycle import ChromeLifecycleCoordinator, axi_destination_identity_unprovable, destination_browser_family, find_active_chrome_roots
from .cookie_import import CookieImporter
from .constants import (
    CAMOUFOX_BACKEND,
    DEFAULT_PATCHRIGHT_APP_ID,
    DEFAULT_PATCHRIGHT_PORT,
    PATCHRIGHT_BACKEND,
    CHROME_NEW_WINDOW_TIMEOUT_S,
    DEFAULT_AXI_BIN,
    DEFAULT_AXI_PORT,
    DEFAULT_AXI_TIMEOUT_S,
    DEFAULT_BACKEND,
    DEFAULT_CAMOUFOX_APP_ID,
    DEFAULT_CAMOUFOX_PORT,
    DEFAULT_CHROME_CLASS,
    DEFAULT_CHROME_DEBUG_PORT,
    DEFAULT_THREAD,
    SNAPSHOT_DIFF_MAX_HUNKS,
    SNAPSHOT_DIFF_MAX_RATIO,
    SNAPSHOT_DIFF_MIN_SAVED_CHARS,
    SURF_AGENT_WINDOW_TITLE,
)
from .errors import SurfAgentError

__all__ = [
    "APP_DIRS",
    "AgentPage",
    "AxiBridgeClient",
    "AxiBridgeConfigMismatch",
    "AxiBridgeUnavailable",
    "CamoufoxBridgeClient",
    "PatchrightBridgeClient",
    "ScreenshotOptions",
    "SurfAgent",
    "SurfAgentError",
    "backend_config_file",
    "choose_snapshot_diff",
    "create_backend",
    "default_camoufox_profile_dir",
    "default_chrome_profile_dir",
    "default_firefox_profile_dir",
    "default_state_dir",
    "extract_page_id",
    "find_page",
    "format_bridge_eval_result",
    "format_bridge_text_result",
    "is_no_pages_output",
    "is_surf_agent_bootstrap_identity",
    "load_state_file",
    "main",
    "map_axi_cli_args_to_bridge",
    "merge_page",
    "parse_agent_args",
    "parse_axi_eval_json",
    "parse_axi_eval_string",
    "parse_axi_pages",
    "parse_bridge_eval_result",
    "parse_do_argv_steps",
    "parse_do_script",
    "parse_eval_code",
    "parse_screenshot_output",
    "run_do",
    "save_state_file",
    "skill_data_dir",
    "strip_axi_page_list",
    "surf_agent_app_url",
    "surf_agent_config_dir",
    "surf_agent_data_dir",
    "surf_agent_state_dir",
    "unwrap_no_arg_iife",
    "wrap_script_expression",
]

APP_DIRS = PlatformDirs("surf-agent", appauthor=False)
SURF_AGENT_GIT_PACKAGE = "git+https://github.com/ewgdg/browser-skills.git#subdirectory=packages/surf-agent"


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
    "bridge",
    "bridge-stop",
    "setup",
    "backend",
}


@dataclass(frozen=True)
class AgentConfig:
    thread: str = DEFAULT_THREAD


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
        camoufox_profile_dir: Path | None = None,
        patchright_profile_dir: Path | None = None,
        chrome_class: str | None = None,
        patchright_app_id: str | None = None,
        patchright_class: str | None = None,
    ) -> None:
        self.axi_bin = axi_bin or os.environ.get("SURF_AGENT_AXI_BIN", DEFAULT_AXI_BIN)
        self.chrome_bin = chrome_bin or os.environ.get("SURF_AGENT_CHROME_BIN") or find_chrome_bin()
        self.command_timeout_s = command_timeout_s if command_timeout_s is not None else parse_timeout_env()
        self.state_file = state_file or default_state_file(thread=thread, state_dir=state_dir)
        self.state_dir = self.state_file.parent if state_file else default_state_dir(state_dir=state_dir)
        self.backend = parse_backend_env()
        self.chrome_profile_dir = chrome_profile_dir or default_chrome_profile_dir()
        self.camoufox_profile_dir = camoufox_profile_dir or default_camoufox_profile_dir()
        self.patchright_profile_dir = patchright_profile_dir or default_patchright_profile_dir()
        self.chrome_class = chrome_class or os.environ.get("SURF_AGENT_CHROME_CLASS") or DEFAULT_CHROME_CLASS
        self.camoufox_app_id = os.environ.get("SURF_AGENT_CAMOUFOX_APP_ID") or os.environ.get("SURF_AGENT_CAMOUFOX_CLASS") or DEFAULT_CAMOUFOX_APP_ID
        self.patchright_app_id = patchright_app_id or os.environ.get("SURF_AGENT_PATCHRIGHT_APP_ID") or os.environ.get("SURF_AGENT_PATCHRIGHT_CLASS") or DEFAULT_PATCHRIGHT_APP_ID
        self.patchright_class = patchright_class or os.environ.get("SURF_AGENT_PATCHRIGHT_CLASS") or self.patchright_app_id
        self.chrome_debug_port = parse_port_env("SURF_AGENT_CHROME_DEBUG_PORT", DEFAULT_CHROME_DEBUG_PORT)
        self.camoufox_port = parse_port_env("SURF_AGENT_CAMOUFOX_PORT", DEFAULT_CAMOUFOX_PORT)
        self.patchright_port = parse_port_env("SURF_AGENT_PATCHRIGHT_PORT", DEFAULT_PATCHRIGHT_PORT)
        self.browser_url = f"http://127.0.0.1:{self.chrome_debug_port}"
        self.camoufox_client = CamoufoxBridgeClient(timeout_s=self.command_timeout_s, port=self.camoufox_port, profile_dir=self.camoufox_profile_dir)
        self.patchright_client = PatchrightBridgeClient(timeout_s=self.command_timeout_s, port=self.patchright_port, profile_dir=self.patchright_profile_dir)
        cookie_source = persisted_config.get_cookie_source(path=backend_config_file())
        self.cookie_import_enabled = cookie_source is not None and self.backend != CAMOUFOX_BACKEND
        destination_profile = self.chrome_profile_dir if self.backend == DEFAULT_BACKEND else self.patchright_profile_dir
        self.destination_family = destination_browser_family(backend=self.backend, executable=self.chrome_bin)
        self.cookie_import_startup_error: str | None = None
        if self.cookie_import_enabled and self.backend == DEFAULT_BACKEND and axi_destination_identity_unprovable(os.environ):
            self.cookie_import_startup_error = "cannot prove the AXI destination profile while an auto-connect or browser URL override is active"
        elif self.cookie_import_enabled and self.destination_family is None:
            self.cookie_import_startup_error = "cannot prove the browser family of the AXI destination executable"
        elif self.cookie_import_enabled and cookie_source is not None and cookie_source.family != self.destination_family:
            self.cookie_import_startup_error = "cookie source browser family does not match the selected Surf destination browser"
        importer = (
            CookieImporter(
                config=cookie_source,
                destination_root=destination_profile,
                state_root=surf_agent_state_dir(),
                destination_family=self.destination_family,
                process_inspector=lambda profile: bool(find_active_chrome_roots(profile)),
            )
            if self.cookie_import_enabled and self.cookie_import_startup_error is None
            else None
        )
        self.lifecycle = ChromeLifecycleCoordinator(
            destination_root=destination_profile,
            state_root=surf_agent_state_dir(),
            importer=importer,
            process_inspector=lambda profile: bool(find_active_chrome_roots(profile)),
        )
        self.patchright_client.before_start = self._patchright_startup_guard
        self.bridge_client = bridge_client or AxiBridgeClient(
            timeout_s=self.command_timeout_s,
            expected_profile_dir=self.chrome_profile_dir if self._uses_dedicated_chrome_profile() else None,
            expected_chrome_class=self.chrome_class if self._uses_dedicated_chrome_profile() else None,
            expected_browser_url=self.browser_url if self._uses_dedicated_chrome_profile() else None,
        )
        self.browser_backend = create_backend(
            self,
            self.backend,
            camoufox_client=self.camoufox_client,
            patchright_client=self.patchright_client,
            welcome_url=surf_agent_welcome_url,
        )

    def run_in_window(self, args: Sequence[str]) -> int:
        if not args:
            print_help(sys.stderr)
            return 2
        output = self.execute_in_window(args)
        if output:
            print(output, end="" if output.endswith("\n") else "\n")
        return 0

    def execute_in_window(self, args: Sequence[str]) -> str:
        command = first_command(args)
        if command is None:
            raise SurfAgentError("missing command", exit_code=2)
        if command in FORBIDDEN_COMMANDS:
            raise SurfAgentError(forbidden_message(command), exit_code=2)
        values = tuple(args[1:])
        if command == "snapshot" and len(args) > 1:
            mode = parse_snapshot_flags(args)
            if mode == "baseline":
                raise SurfAgentError("snapshot --baseline is only supported inside do", exit_code=2)
            current = capture_snapshot(self)
            decision = choose_snapshot_diff(None, current)
            return decision.output
        if command == "open":
            require_arg_count(values, 1, "open requires exactly one URL")
            return self.browser_backend.open(values[0])
        if command == "new":
            reject_args(command, values)
            return self.browser_backend.new()
        if command == "snapshot":
            reject_args(command, values)
            return self.browser_backend.snapshot()
        if command == "text":
            reject_args(command, values)
            return self.browser_backend.text()
        if command == "click":
            require_arg_count(values, 1, "click requires exactly one target")
            return self.browser_backend.click(values[0])
        if command == "fill":
            if len(values) < 2:
                raise SurfAgentError("fill requires target and text", exit_code=2)
            return self.browser_backend.fill(values[0], " ".join(values[1:]))
        if command == "type":
            if not values:
                raise SurfAgentError("type requires text", exit_code=2)
            return self.browser_backend.type_text(" ".join(values))
        if command == "press":
            require_arg_count(values, 1, "press requires exactly one key")
            return self.browser_backend.press(values[0])
        if command == "scroll":
            require_arg_count(values, 1, "scroll requires direction: up, down, top, or bottom")
            if values[0] not in {"up", "down", "top", "bottom"}:
                raise SurfAgentError("scroll requires direction: up, down, top, or bottom", exit_code=2)
            return self.browser_backend.scroll(values[0])
        if command == "wait":
            require_arg_count(values, 1, "wait requires one duration in milliseconds or text target")
            return self.browser_backend.wait(values[0])
        if command == "screenshot":
            return self.browser_backend.screenshot(parse_screenshot_output(values))
        if command == "eval":
            return self.browser_backend.evaluate(parse_eval_code(values, stdin=sys.stdin))
        if command == "back":
            reject_args(command, values)
            return self.browser_backend.back()
        raise SurfAgentError(f"unsupported browser command: {command}", exit_code=2)

    def print_page_id(self, *, force_new: bool = False) -> None:
        self.browser_backend.print_page_id(force_new=force_new)

    def print_state(self, *, thread: str) -> None:
        self.browser_backend.print_state(thread=thread)

    def print_list(self) -> None:
        self.browser_backend.print_list()

    def reset_state(self) -> None:
        unlink_missing_ok(self.state_file)

    def close(self) -> int:
        return self.browser_backend.close()

    def focus(self) -> int:
        return self.browser_backend.focus()

    def close_matching(self, pattern: str) -> int:
        return self.browser_backend.close_matching(pattern)

    def bridge_stop(self) -> int:
        return self.browser_backend.bridge_stop()

    def print_profile_show(self) -> None:
        payload = {
            "backend": self.backend,
            "axi_bridge_port": int(os.environ.get("CHROME_DEVTOOLS_AXI_PORT", DEFAULT_AXI_PORT)),
            "browser_url": self.browser_url,
            "camoufox_bridge_port": self.camoufox_port,
            "camoufox_profile_dir": str(self.camoufox_profile_dir),
            "chrome_class": self.chrome_class,
            "chrome_debug_port": self.chrome_debug_port,
            "patchright_bridge_port": self.patchright_port,
            "patchright_profile_dir": str(self.patchright_profile_dir),
            "patchright_app_id": self.patchright_app_id,
            "patchright_class": self.patchright_class,
            "profile_dir": str(self.chrome_profile_dir),
        }
        print(json.dumps(payload, sort_keys=True))

    def profile_open(self, url: str = "about:blank") -> int:
        if self.backend == CAMOUFOX_BACKEND:
            return self._camoufox_profile_open(url)
        if self.backend == PATCHRIGHT_BACKEND:
            return self._patchright_profile_open(url)
        with self._axi_startup_guard():
            if self._chrome_debug_endpoint_ready():
                raise SurfAgentError(f"automated Surf Agent Chrome is running at {self.browser_url}; close Surf Agent windows or run `surf-agent bridge stop` before `profile open`")
            if not self.chrome_bin:
                raise SurfAgentError("could not find Chrome executable for profile open; set SURF_AGENT_CHROME_BIN")
            self.chrome_profile_dir.mkdir(parents=True, exist_ok=True)
            command = [*shlex.split(self.chrome_bin), f"--class={self.chrome_class}", f"--user-data-dir={self.chrome_profile_dir}", "--new-window", url]
            proc = self._subprocess_run(command, check=False, text=True, capture_output=True, timeout=CHROME_NEW_WINDOW_TIMEOUT_S)
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout or "Chrome profile open failed").strip()
                raise SurfAgentError(detail)
            return 0

    def _camoufox_profile_open(self, url: str = "about:blank") -> int:
        return self.browser_backend.profile_open(
            url, profile_dir=str(self.camoufox_profile_dir), app_id=self.camoufox_app_id
        )

    def _patchright_profile_open(self, url: str = "about:blank") -> int:
        return self.browser_backend.profile_open(
            url,
            profile_dir=str(self.patchright_profile_dir),
            app_id=self.patchright_app_id,
            window_class=self.patchright_class,
        )

    def setup_camoufox(self) -> int:
        return setup_camoufox_backend()

    def setup_patchright(self) -> int:
        return setup_patchright_backend()

    def print_help_to_stderr(self) -> None:
        print_help(sys.stderr)

    def _axi_startup_guard(self):
        if self.cookie_import_startup_error:
            raise SurfAgentError(self.cookie_import_startup_error)
        return self.lifecycle.launch_guard(health_check=self._chrome_debug_endpoint_ready)

    def _patchright_startup_guard(self):
        if self.cookie_import_startup_error:
            raise SurfAgentError(self.cookie_import_startup_error)
        return self.lifecycle.launch_guard(health_check=self.patchright_client._health_ok)

    def force_cookie_import(self):
        if self.cookie_import_startup_error:
            raise SurfAgentError(self.cookie_import_startup_error)
        return self.lifecycle.import_now()

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
    return state_dir or surf_agent_state_dir() / "threads"


def surf_agent_home() -> Path | None:
    value = os.environ.get("SURF_AGENT_HOME")
    return Path(value).expanduser() if value else None


def surf_agent_config_dir() -> Path:
    return surf_agent_home() or Path(APP_DIRS.user_config_dir)


def surf_agent_state_dir() -> Path:
    return surf_agent_home() or Path(APP_DIRS.user_state_dir)


def surf_agent_data_dir() -> Path:
    return surf_agent_home() or Path(APP_DIRS.user_data_dir)


def skill_data_dir() -> Path:
    return surf_agent_data_dir()


def backend_config_file() -> Path:
    return surf_agent_config_dir() / "config.json"


def default_chrome_profile_dir() -> Path:
    value = os.environ.get("SURF_AGENT_CHROME_PROFILE_DIR") or os.environ.get("CHROME_DEVTOOLS_AXI_USER_DATA_DIR")
    if value:
        return Path(value).expanduser()
    return surf_agent_data_dir() / "profiles" / "chrome"


def default_firefox_profile_dir() -> Path:
    value = os.environ.get("SURF_AGENT_FIREFOX_PROFILE_DIR")
    if value:
        return Path(value).expanduser()
    return surf_agent_data_dir() / "profiles" / "firefox"


def default_camoufox_profile_dir() -> Path:
    value = os.environ.get("SURF_AGENT_CAMOUFOX_PROFILE_DIR")
    if value:
        return Path(value).expanduser()
    return default_firefox_profile_dir()


def default_patchright_profile_dir() -> Path:
    value = os.environ.get("SURF_AGENT_PATCHRIGHT_PROFILE_DIR")
    if value:
        return Path(value).expanduser()
    return default_chrome_profile_dir()


def parse_backend_env() -> str:
    return resolve_backend_preference()[0]


def resolve_backend_preference() -> tuple[str, str]:
    return persisted_config.resolve_backend_preference(path=backend_config_file())


def validate_backend_name(value: str, *, source: str = "backend") -> str:
    return persisted_config.validate_backend_name(value, source=source)


def load_backend_config() -> dict[str, Any]:
    return persisted_config.load_config(backend_config_file())


def write_backend_config(config: dict[str, Any]) -> None:
    persisted_config.write_config(backend_config_file(), config)


def show_backend_config() -> int:
    backend, source = resolve_backend_preference()
    print(json.dumps({"backend": backend, "source": source, "config_file": str(backend_config_file())}, sort_keys=True))
    return 0


def set_backend_config(backend: str) -> int:
    backend = validate_backend_name(backend)
    previous_backend = current_backend_for_cleanup()
    persisted_config.set_backend(backend, path=backend_config_file())
    if previous_backend is not None and previous_backend != backend:
        cleanup_backend_runtime(previous_backend)
    print(json.dumps({"backend": backend, "config_file": str(backend_config_file())}, sort_keys=True))
    return 0


def current_backend_for_cleanup() -> str | None:
    try:
        configured = load_backend_config().get("backend")
        if isinstance(configured, str) and configured.strip():
            return validate_backend_name(configured, source=str(backend_config_file()))
        return DEFAULT_BACKEND
    except SurfAgentError:
        return None


def cleanup_backend_runtime(backend: str) -> None:
    try:
        agent = SurfAgent()
        if backend == DEFAULT_BACKEND:
            from .backends import AxiBackend

            with contextlib.redirect_stdout(io.StringIO()):
                AxiBackend(agent).bridge_stop()
            return
        if backend == PATCHRIGHT_BACKEND:
            with contextlib.redirect_stdout(io.StringIO()):
                agent.patchright_client.stop()
            stop_patchright_runtime(agent.patchright_profile_dir, port=agent.patchright_port)
            return
        if backend == CAMOUFOX_BACKEND:
            with contextlib.redirect_stdout(io.StringIO()):
                agent.camoufox_client.stop()
            stop_module_bridge_processes("surf_agent.backends.camoufox.bridge", port=agent.camoufox_port, profile_dir=agent.camoufox_profile_dir)
    except Exception as exc:
        print(f"surf-agent: warning: could not clean up previous {backend} runtime: {exc}", file=sys.stderr)


def reset_backend_config() -> int:
    persisted_config.reset_backend(path=backend_config_file())
    backend, source = resolve_backend_preference()
    print(json.dumps({"backend": backend, "source": source, "config_file": str(backend_config_file())}, sort_keys=True))
    return 0


def cookie_source_commands(argv: Sequence[str]) -> int:
    if parse_backend_env() == CAMOUFOX_BACKEND:
        raise SurfAgentError("Camoufox does not support live cookie import", exit_code=1)
    if not argv:
        raise SurfAgentError("usage: surf-agent profile cookie-source show|set|reset", exit_code=2)
    action = argv[0]
    path = backend_config_file()
    if action == "show" and len(argv) == 1:
        source = persisted_config.get_cookie_source(path=path)
        print(json.dumps({"configured": source is not None, "cookie_source": source.to_json() if source else None}, sort_keys=True))
        return 0
    if action == "reset" and len(argv) == 1:
        persisted_config.reset_cookie_source(path=path)
        print(json.dumps({"configured": False, "config_file": str(path)}, sort_keys=True))
        return 0
    if action != "set":
        raise SurfAgentError("usage: surf-agent profile cookie-source show | cookie-source set --source DIR --source-profile NAME (--domain DOMAIN ... | --all-domains) | cookie-source reset", exit_code=2)
    source_path: str | None = None
    profile: str | None = None
    domains: list[str] = []
    all_domains = False
    index = 1
    while index < len(argv):
        option = argv[index]
        if option in {"--source", "--source-profile", "--domain"}:
            if index + 1 >= len(argv):
                raise SurfAgentError(f"{option} requires a value", exit_code=2)
            value = argv[index + 1]
            if option == "--source":
                if source_path is not None:
                    raise SurfAgentError("--source may be provided once", exit_code=2)
                source_path = value
            elif option == "--source-profile":
                if profile is not None:
                    raise SurfAgentError("--source-profile may be provided once", exit_code=2)
                profile = value
            else:
                domains.append(value)
            index += 2
            continue
        if option == "--all-domains":
            if all_domains:
                raise SurfAgentError("--all-domains may be provided once", exit_code=2)
            all_domains = True
            index += 1
            continue
        raise SurfAgentError(f"unsupported cookie-source option: {option}", exit_code=2)
    if source_path is None or profile is None or (all_domains == bool(domains)):
        raise SurfAgentError("cookie-source set requires --source, --source-profile, and exactly one of --domain or --all-domains", exit_code=2)
    if not sys.platform.startswith("linux"):
        raise SurfAgentError("live cookie import is supported only on Linux")
    scope = persisted_config.CookieScope.all() if all_domains else persisted_config.CookieScope.from_domains(domains)
    source = persisted_config.resolve_cookie_source(source=source_path, profile=profile, scope=scope)
    backend = parse_backend_env()
    if backend == DEFAULT_BACKEND and axi_destination_identity_unprovable(os.environ):
        raise SurfAgentError("cannot prove the AXI destination profile while an auto-connect or browser URL override is active")
    destination_family = destination_browser_family(
        backend=backend,
        executable=os.environ.get("SURF_AGENT_CHROME_BIN") or find_chrome_bin(),
    )
    if destination_family is None:
        raise SurfAgentError("could not prove the browser family of the selected Surf destination")
    if source.family != destination_family:
        raise SurfAgentError("cookie source browser family does not match the selected Surf destination browser")
    persisted_config.set_cookie_source(source, path=path)
    print(json.dumps({"configured": True, "cookie_source": source.to_json(), "config_file": str(path)}, sort_keys=True))
    return 0


def import_cookies_command() -> int:
    source = persisted_config.get_cookie_source(path=backend_config_file())
    if source is None:
        raise SurfAgentError("no cookie source is configured; run `surf-agent profile cookie-source set ...` first")
    agent = SurfAgent()
    if agent.backend == CAMOUFOX_BACKEND:
        raise SurfAgentError("Camoufox does not support live cookie import")
    if agent.backend == DEFAULT_BACKEND and (os.environ.get("CHROME_DEVTOOLS_AXI_AUTO_CONNECT") == "1" or os.environ.get("CHROME_DEVTOOLS_AXI_BROWSER_URL")):
        raise SurfAgentError("cannot prove the AXI destination profile while an auto-connect or browser URL override is active")
    result = agent.force_cookie_import()
    print(json.dumps({"imported_rows": result.imported_rows, "destination": str(result.destination) if result.destination else None}, sort_keys=True))
    return 0

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


def stop_axi_chrome_runtime(profile_dir: Path, *, debug_port: int) -> list[int]:
    return terminate_processes(find_chrome_root_processes(profile_dir, remote_debugging_port=debug_port))


def stop_patchright_runtime(profile_dir: Path, *, port: int) -> list[int]:
    stopped = stop_module_bridge_processes("surf_agent.backends.patchright.bridge", port=port, profile_dir=profile_dir)
    stopped.extend(terminate_processes(find_chrome_root_processes(profile_dir, remote_debugging_pipe=True)))
    return stopped


def stop_module_bridge_processes(module: str, *, port: int, profile_dir: Path) -> list[int]:
    return terminate_processes(find_module_bridge_processes(module, port=port, profile_dir=profile_dir))


def find_chrome_root_processes(profile_dir: Path, *, remote_debugging_port: int | None = None, remote_debugging_pipe: bool = False) -> list[int]:
    wanted_profile = str(profile_dir)
    pids: list[int] = []
    for pid, args in iter_process_args():
        if pid == os.getpid() or not args:
            continue
        if any(arg.startswith("--type=") for arg in args):
            continue
        if not has_arg_value(args, "--user-data-dir", wanted_profile):
            continue
        if remote_debugging_port is not None and has_arg_value(args, "--remote-debugging-port", str(remote_debugging_port)):
            pids.append(pid)
            continue
        if remote_debugging_pipe and "--remote-debugging-pipe" in args:
            pids.append(pid)
    return pids


def find_module_bridge_processes(module: str, *, port: int, profile_dir: Path) -> list[int]:
    wanted_profile = str(profile_dir)
    wanted_port = str(port)
    pids: list[int] = []
    for pid, args in iter_process_args():
        if pid == os.getpid() or module not in args:
            continue
        if has_arg_value(args, "--port", wanted_port) and has_arg_value(args, "--profile-dir", wanted_profile):
            pids.append(pid)
    return pids


def has_arg_value(args: Sequence[str], option: str, value: str) -> bool:
    for index, arg in enumerate(args):
        if arg == option and index + 1 < len(args) and args[index + 1] == value:
            return True
        if arg == f"{option}={value}":
            return True
    return False


def iter_process_args(proc_dir: Path = Path("/proc")) -> list[tuple[int, list[str]]]:
    processes: list[tuple[int, list[str]]] = []
    try:
        entries = list(proc_dir.iterdir())
    except OSError:
        return processes
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        args = [part.decode(errors="replace") for part in raw.split(b"\0") if part]
        if args:
            processes.append((int(entry.name), args))
    return processes


def terminate_processes(pids: Sequence[int], *, timeout_s: float = 2.0) -> list[int]:
    stopped: list[int] = []
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
            stopped.append(pid)
        except ProcessLookupError:
            continue
        except OSError:
            continue
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline and any(process_exists(pid) for pid in stopped):
        time.sleep(0.05)
    for pid in stopped:
        if not process_exists(pid):
            continue
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    return stopped


def process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return True


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




def require_arg_count(values: Sequence[str], expected: int, message: str) -> None:
    if len(values) != expected:
        raise SurfAgentError(message, exit_code=2)


def reject_args(command: str, values: Sequence[str]) -> None:
    if values:
        raise SurfAgentError(f"{command} does not accept arguments", exit_code=2)


def parse_eval_code(values: Sequence[str], *, stdin: Any | None = None) -> str:
    source_kind: str | None = None
    source_value: str | None = None
    inline: list[str] = []
    index = 0
    while index < len(values):
        value = values[index]
        if value == "--stdin":
            if source_kind is not None or inline:
                raise SurfAgentError("eval accepts exactly one source: code, --stdin, or --file <path>", exit_code=2)
            source_kind = "stdin"
            index += 1
            continue
        if value == "--file":
            if source_kind is not None or inline:
                raise SurfAgentError("eval accepts exactly one source: code, --stdin, or --file <path>", exit_code=2)
            index += 1
            if index >= len(values):
                raise SurfAgentError("eval --file requires a path", exit_code=2)
            source_kind = "file"
            source_value = values[index]
            index += 1
            continue
        if value.startswith("--") and not inline:
            raise SurfAgentError(f"unsupported eval option: {value}", exit_code=2)
        if source_kind is not None:
            raise SurfAgentError("eval accepts exactly one source: code, --stdin, or --file <path>", exit_code=2)
        inline.extend(values[index:])
        break

    if inline:
        return " ".join(inline)
    if source_kind == "stdin":
        return (stdin or sys.stdin).read()
    if source_kind == "file":
        assert source_value is not None
        try:
            return Path(source_value).read_text(encoding="utf-8")
        except OSError as exc:
            raise SurfAgentError(f"could not read eval file {source_value}: {exc}", exit_code=2) from exc
    raise SurfAgentError("eval requires code, --stdin, or --file <path>", exit_code=2)


def parse_screenshot_output(values: Sequence[str]) -> ScreenshotOptions:
    path: str | None = None
    full_page = False
    index = 0
    while index < len(values):
        value = values[index]
        if value == "--full-page":
            if full_page:
                raise SurfAgentError("screenshot --full-page was provided more than once", exit_code=2)
            full_page = True
            index += 1
            continue
        if value == "--output":
            index += 1
            if index >= len(values):
                raise SurfAgentError("screenshot --output requires a path", exit_code=2)
            if path is not None:
                raise SurfAgentError("screenshot requires exactly one output path", exit_code=2)
            path = values[index]
            index += 1
            continue
        if value.startswith("--"):
            raise SurfAgentError(f"unsupported screenshot option: {value}", exit_code=2)
        if path is not None:
            raise SurfAgentError("screenshot requires exactly one output path", exit_code=2)
        path = value
        index += 1
    if path is None:
        raise SurfAgentError("screenshot requires a path or --output <path>", exit_code=2)
    return ScreenshotOptions(path=path, full_page=full_page)

def unlink_missing_ok(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


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
    return agent.browser_backend.capture_snapshot()


def snapshot_capture_from_page(*, text: str, page: AgentPage) -> SnapshotCapture:
    return SnapshotCapture(
        text=text,
        page_id=page.page_id,
        url=page.url,
        title=page.title,
        origin=url_origin(page.url),
        url_without_fragment=url_without_fragment(page.url),
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


def setup_camoufox_backend() -> int:
    package_installed = python_module_available("camoufox")
    browser_installed = False
    browser_status = "unknown"
    if package_installed:
        browser_installed, browser_status = camoufox_browser_status()

    if package_installed and browser_installed:
        print(
            "Camoufox appears set up.\n"
            "Python package: installed\n"
            "Browser: installed\n"
            "Select it with:\n"
            "  surf-agent backend set camoufox"
        )
        return 0

    print(
        "Camoufox setup is manual for safety.\n"
        f"Python package: {'installed' if package_installed else 'missing'}\n"
        f"Browser: {browser_status}\n"
        "Install Camoufox Python support with:\n"
        f"  uv tool install \"surf-agent[camoufox] @ {SURF_AGENT_GIT_PACKAGE}\"\n"
        "Install/update the Camoufox browser yourself with:\n"
        "  python -m camoufox sync\n"
        "  python -m camoufox set official/prerelease\n"
        "  python -m camoufox fetch\n"
        "Then select it with:\n"
        "  surf-agent backend set camoufox"
    )
    return 0


def setup_patchright_backend() -> int:
    package_installed = python_module_available("patchright")
    chrome_bin = os.environ.get("SURF_AGENT_CHROME_BIN") or find_chrome_bin()

    if package_installed and chrome_bin:
        print(
            "Patchright appears set up.\n"
            "Python package: installed\n"
            f"Chrome: {chrome_bin}\n"
            "Select it with:\n"
            "  surf-agent backend set patchright"
        )
        return 0

    print(
        "Patchright setup is manual for safety.\n"
        f"Python package: {'installed' if package_installed else 'missing'}\n"
        f"Chrome: {chrome_bin or 'missing'}\n"
        "Install Google Chrome yourself, then make it available on PATH "
        "as `google-chrome` or set SURF_AGENT_CHROME_BIN.\n"
        "Install Patchright Python support with:\n"
        f"  uv tool install \"surf-agent[patchright] @ {SURF_AGENT_GIT_PACKAGE}\"\n"
        "Then select it with:\n"
        "  surf-agent backend set patchright"
    )
    return 0


def python_module_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def camoufox_browser_status() -> tuple[bool, str]:
    command = [sys.executable, "-m", "camoufox", "version"]
    try:
        proc = subprocess.run(command, check=False, text=True, capture_output=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, f"unknown ({exc})"

    output = "\n".join(part for part in (proc.stdout, proc.stderr) if part).strip()
    if proc.returncode != 0:
        return False, f"missing ({output or f'exit code {proc.returncode}'})"
    if re.search(r"(?im)^\s*Installed\s+Yes\s*$", output):
        return True, "installed"
    return False, "missing"


def print_help(stream: Any) -> None:
    stream.write(
        "surf-agent: threaded browser helper using a persistent browser bridge\n\n"
        "Usage:\n"
        "  surf-agent [--thread ID] state                 print current page state; does not open a page\n"
        "  surf-agent list                                list remembered browser threads and clean stale entries\n"
        "  surf-agent [--thread ID] new                   replace/create dedicated thread window, print page id\n"
        "  surf-agent [--thread ID] close                 close remembered thread page/window; idle bridge may stop after grace\n"
        "  surf-agent [--thread ID] focus                 select remembered thread page\n"
        "  surf-agent profile show                         print dedicated profile configuration JSON\n"
        "  surf-agent profile open [url]                   open dedicated profile without automation/debug port\n"
        "  surf-agent profile cookie-source show|set|reset  manage explicit live Chrome cookie source\n"
        "  surf-agent profile import-cookies                force a live cookie refresh into an inactive profile\n"
        "  surf-agent backend show                         print selected backend and source\n"
        "  surf-agent backend set axi|camoufox|patchright   persist default backend\n"
        "  surf-agent backend reset                        clear persisted backend\n"
        "  surf-agent setup camoufox|patchright            check setup and print manual backend setup steps\n"
        "  surf-agent close-all                           close all remembered thread pages/windows\n"
        "  surf-agent close-matching <glob>               close remembered thread pages/windows whose thread names match\n"
        "  surf-agent [--thread ID] reset                 clear thread state without closing page\n"
        "  surf-agent [--thread ID] bridge stop           explicit destructive browser bridge stop\n"
        "  surf-agent [--thread ID] do [-]                run newline-separated steps from stdin\n"
        "  surf-agent [--thread ID] <command...>          run supported browser command in thread page\n\n"
        "Supported browser commands:\n"
        "  open <url>, snapshot, text, eval <code|--stdin|--file path>, click <target>, fill <target> <text>, type <text>,\n"
        "  press <key>, scroll up|down|top|bottom, screenshot [--full-page] [--output] <path>, back, wait <ms|text>.\n\n"
        "Examples:\n"
        "  surf-agent --thread main state\n"
        "  surf-agent --thread main open https://example.com\n"
        "  surf-agent --thread main snapshot\n"
        "  printf 'document.title' | surf-agent --thread main eval --stdin\n"
        "  surf-agent --thread main eval --file /tmp/script.js\n"
        "  printf 'open https://example.com\\nsnapshot\\n' | surf-agent --thread main do\n"
        "  surf-agent profile open https://x.com\n"
        "  surf-agent profile cookie-source set --source ~/.config/google-chrome --source-profile Default --domain github.com\n"
        "  surf-agent profile import-cookies\n"
        "  surf-agent backend set camoufox\n"
        "  surf-agent backend set patchright\n"
        "  surf-agent setup camoufox\n"
        "  surf-agent setup patchright\n"
        "  surf-agent --thread docs screenshot --output /tmp/shot.png\n"
        "  surf-agent close-matching 'agent-run-*'\n\n"
        "State: platform user dirs by default; set SURF_AGENT_HOME to keep config, threads, and profiles together.\n"
        "Browser bridge: dedicated profile env is embedded; setup/login may be needed once. New threads start in a window titled Surf Agent.\n"
    )


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        config, argv = parse_agent_args(argv)
        if not argv or argv[0] in {"help", "--help", "-h"}:
            print_help(sys.stdout)
            return 0 if argv else 2
        command = argv[0]
        if command == "backend":
            if len(argv) == 2 and argv[1] == "show":
                return show_backend_config()
            if len(argv) == 3 and argv[1] == "set":
                return set_backend_config(argv[2])
            if len(argv) == 2 and argv[1] == "reset":
                return reset_backend_config()
            raise SurfAgentError("usage: surf-agent backend show | backend set axi|camoufox|patchright | backend reset", exit_code=2)
        if command == "setup":
            if len(argv) == 2 and argv[1] == CAMOUFOX_BACKEND:
                return setup_camoufox_backend()
            if len(argv) == 2 and argv[1] == PATCHRIGHT_BACKEND:
                return setup_patchright_backend()
            raise SurfAgentError("usage: surf-agent setup camoufox|patchright", exit_code=2)
        if command == CAMOUFOX_BACKEND and len(argv) == 2 and argv[1] == "setup":
            return setup_camoufox_backend()
        if command == PATCHRIGHT_BACKEND and len(argv) == 2 and argv[1] == "setup":
            return setup_patchright_backend()
        if command == "profile" and len(argv) >= 2 and argv[1] == "cookie-source":
            return cookie_source_commands(argv[2:])
        if command == "profile" and len(argv) == 2 and argv[1] == "import-cookies":
            return import_cookies_command()
        agent = SurfAgent(thread=config.thread)
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
                raise SurfAgentError("profile requires subcommand: show, open, cookie-source, or import-cookies", exit_code=2)
            if argv[1] == "show" and len(argv) == 2:
                agent.print_profile_show()
                return 0
            if argv[1] == "open" and len(argv) <= 3:
                return agent.profile_open(argv[2] if len(argv) == 3 else "about:blank")
            raise SurfAgentError("usage: surf-agent profile show | profile open [url] | profile cookie-source ... | profile import-cookies", exit_code=2)
        if command == "close-all":
            return agent.close_matching("*")
        if command == "close-matching":
            if len(argv) < 2:
                raise SurfAgentError("close-matching requires a thread glob pattern", exit_code=2)
            return agent.close_matching(argv[1])
        if command == "bridge":
            if len(argv) == 2 and argv[1] == "stop":
                return agent.bridge_stop()
            raise SurfAgentError("usage: surf-agent bridge stop", exit_code=2)
        if command == "bridge-stop":
            return agent.bridge_stop()
        if command == "do":
            return run_do(agent, thread=config.thread, argv=argv[1:])
        if command == "--":
            argv = argv[1:]
        return agent.run_in_window(argv)
    except KeyboardInterrupt:
        print("surf-agent: interrupted", file=sys.stderr)
        return 130
    except SurfAgentError as exc:
        print(f"surf-agent: {exc}", file=sys.stderr)
        return exc.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
