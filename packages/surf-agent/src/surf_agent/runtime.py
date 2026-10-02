"""Browser lifecycle, configuration paths, and process ownership."""

from __future__ import annotations

import importlib.util
import os
import shlex
import shutil
import signal
import time
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import quote

from platformdirs import PlatformDirs

from . import config as persisted_config
from .backends import PatchrightBackend, PatchrightBridgeClient
from .chrome_lifecycle import ChromeLifecycleCoordinator, find_active_chrome_roots
from .constants import (
    CHROME_EXECUTABLE_CANDIDATES,
    DEFAULT_COMMAND_TIMEOUT_S,
    DEFAULT_PATCHRIGHT_APP_ID,
    DEFAULT_PATCHRIGHT_PORT,
    DEFAULT_THREAD,
    PATCHRIGHT_BROWSER_FAMILY,
    SURF_AGENT_WINDOW_TITLE,
)
from .cookie_import import CookieImporter
from .processes import iter_process_args
from .errors import SurfAgentError

APP_DIRS = PlatformDirs("surf-agent", appauthor=False)


class SurfAgent:
    def __init__(
        self,
        *,
        chrome_bin: str | None = None,
        command_timeout_s: float | None = None,
        thread: str = DEFAULT_THREAD,
        patchright_profile_dir: Path | None = None,
        patchright_app_id: str | None = None,
        patchright_class: str | None = None,
    ) -> None:
        self.chrome_bin = (
            chrome_bin or os.environ.get("SURF_AGENT_CHROME_BIN") or find_chrome_bin()
        )
        self.command_timeout_s = (
            command_timeout_s if command_timeout_s is not None else parse_timeout_env()
        )
        self.thread = safe_thread_name(thread)
        self.patchright_profile_dir = (
            patchright_profile_dir or default_patchright_profile_dir()
        )
        self.patchright_app_id = (
            patchright_app_id
            or os.environ.get("SURF_AGENT_PATCHRIGHT_APP_ID")
            or os.environ.get("SURF_AGENT_PATCHRIGHT_CLASS")
            or DEFAULT_PATCHRIGHT_APP_ID
        )
        self.patchright_class = (
            patchright_class
            or os.environ.get("SURF_AGENT_PATCHRIGHT_CLASS")
            or self.patchright_app_id
        )
        self.patchright_port = parse_port_env(
            "SURF_AGENT_PATCHRIGHT_PORT", DEFAULT_PATCHRIGHT_PORT
        )
        self.patchright_client = PatchrightBridgeClient(
            timeout_s=self.command_timeout_s,
            port=self.patchright_port,
            profile_dir=self.patchright_profile_dir,
        )
        cookie_source = persisted_config.get_cookie_source(path=config_file())
        self.cookie_import_enabled = cookie_source is not None
        self.cookie_import_startup_error: str | None = None
        if cookie_source is not None and cookie_source.family != PATCHRIGHT_BROWSER_FAMILY:
            self.cookie_import_startup_error = "cookie source browser family does not match the Surf destination browser"
        importer = (
            CookieImporter(
                config=cookie_source,
                destination_root=self.patchright_profile_dir,
                state_root=surf_agent_state_dir(),
                destination_family=PATCHRIGHT_BROWSER_FAMILY,
                process_inspector=lambda profile: bool(
                    find_active_chrome_roots(profile)
                ),
            )
            if self.cookie_import_enabled and self.cookie_import_startup_error is None
            else None
        )
        self.lifecycle = ChromeLifecycleCoordinator(
            destination_root=self.patchright_profile_dir,
            state_root=surf_agent_state_dir(),
            importer=importer,
            process_inspector=lambda profile: bool(find_active_chrome_roots(profile)),
        )
        self.patchright_client.before_start = self._patchright_startup_guard
        self.browser_backend = PatchrightBackend(
            self, client=self.patchright_client, welcome_url=surf_agent_welcome_url
        )

    def profile_open(self, url: str = "about:blank") -> int:
        return self.browser_backend.profile_open(
            url,
            profile_dir=str(self.patchright_profile_dir),
            app_id=self.patchright_app_id,
            window_class=self.patchright_class,
        )

    def _patchright_startup_guard(self):
        if self.cookie_import_startup_error:
            raise SurfAgentError(self.cookie_import_startup_error)
        return self.lifecycle.launch_guard(
            health_check=self.patchright_client._health_ok
        )

    def force_cookie_import(self):
        if self.cookie_import_startup_error:
            raise SurfAgentError(self.cookie_import_startup_error)
        return self.lifecycle.import_now()


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


def config_file() -> Path:
    return surf_agent_config_dir() / "config.json"


def default_patchright_profile_dir() -> Path:
    value = os.environ.get("SURF_AGENT_PATCHRIGHT_PROFILE_DIR")
    if value:
        return Path(value).expanduser()
    return surf_agent_data_dir() / "profiles" / "chrome"


def safe_thread_name(thread: str) -> str:
    value = thread.strip() or DEFAULT_THREAD
    allowed = all(ch.isalnum() or ch in {"-", "_", "."} for ch in value)
    if not allowed or value in {".", ".."} or value.startswith("."):
        raise SurfAgentError(
            "--thread may contain only letters, numbers, '.', '-', and '_' and must not start with '.'",
            exit_code=2,
        )
    return value


def parse_timeout_env() -> float:
    value = os.environ.get("SURF_AGENT_COMMAND_TIMEOUT", "")
    if not value:
        return DEFAULT_COMMAND_TIMEOUT_S
    try:
        timeout = float(value)
    except ValueError as exc:
        raise SurfAgentError(
            "SURF_AGENT_COMMAND_TIMEOUT must be a number", exit_code=2
        ) from exc
    if timeout <= 0:
        raise SurfAgentError(
            "SURF_AGENT_COMMAND_TIMEOUT must be greater than zero", exit_code=2
        )
    return timeout


def parse_port_env(name: str, default: str) -> int:
    raw = os.environ.get(name, default)
    value = coerce_int(raw)
    if value is None or value <= 0 or value > 65535:
        raise SurfAgentError(f"{name} must be a TCP port number", exit_code=2)
    return value


def stop_patchright_runtime(profile_dir: Path, *, port: int) -> list[int]:
    stopped = stop_module_bridge_processes(
        "surf_agent.backends.patchright.bridge", port=port, profile_dir=profile_dir
    )
    stopped.extend(
        terminate_processes(
            find_chrome_root_processes(profile_dir)
        )
    )
    return stopped


def stop_module_bridge_processes(
    module: str, *, port: int, profile_dir: Path
) -> list[int]:
    return terminate_processes(
        find_module_bridge_processes(module, port=port, profile_dir=profile_dir)
    )


def find_chrome_root_processes(profile_dir: Path) -> list[int]:
    """Find automation-owned Chrome roots: Patchright drives Chrome over a pipe."""
    wanted_profile = str(profile_dir)
    pids: list[int] = []
    for pid, args in iter_process_args():
        if pid == os.getpid() or not args:
            continue
        if any(arg.startswith("--type=") for arg in args):
            continue
        if not has_arg_value(args, "--user-data-dir", wanted_profile):
            continue
        if "--remote-debugging-pipe" in args:
            pids.append(pid)
    return pids


def find_module_bridge_processes(
    module: str, *, port: int, profile_dir: Path
) -> list[int]:
    wanted_profile = str(profile_dir)
    wanted_port = str(port)
    pids: list[int] = []
    for pid, args in iter_process_args():
        if pid == os.getpid() or module not in args:
            continue
        if has_arg_value(args, "--port", wanted_port) and has_arg_value(
            args, "--profile-dir", wanted_profile
        ):
            pids.append(pid)
    return pids


def has_arg_value(args: Sequence[str], option: str, value: str) -> bool:
    for index, arg in enumerate(args):
        if arg == option and index + 1 < len(args) and args[index + 1] == value:
            return True
        if arg == f"{option}={value}":
            return True
    return False


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


def find_chrome_bin() -> str | None:
    """The first installed Chrome-family browser, as a shlex-quoted command word."""
    for candidate in CHROME_EXECUTABLE_CANDIDATES:
        found = shutil.which(candidate)
        if found:
            # Callers shlex-split this value like SURF_AGENT_CHROME_BIN, and macOS
            # bundle paths contain spaces.
            return shlex.quote(found)
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
        "<p>This window is safe to target with window rules. It will navigate when you run <code>Thread(name).open(url)</code>.</p>"
        "</body></html>"
    )
    return "data:text/html;charset=utf-8," + quote(html, safe="")


def coerce_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


SnapshotMode = str


def python_module_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None
