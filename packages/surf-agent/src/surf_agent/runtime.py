"""Browser lifecycle, configuration paths, and process ownership."""

from __future__ import annotations

import importlib.util
import os
import shlex
import shutil
import signal
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import quote

from platformdirs import PlatformDirs

from . import config as persisted_config
from .backends import (
    AxiBridgeClient,
    PatchrightBridgeClient,
    create_backend,
)
from .chrome_lifecycle import (
    ChromeLifecycleCoordinator,
    axi_destination_identity_unprovable,
    destination_browser_family,
    find_active_chrome_roots,
)
from .constants import (
    AXI_BACKEND,
    CHROME_NEW_WINDOW_TIMEOUT_S,
    DEFAULT_AXI_BIN,
    DEFAULT_AXI_PORT,
    DEFAULT_AXI_TIMEOUT_S,
    DEFAULT_CHROME_CLASS,
    DEFAULT_CHROME_DEBUG_PORT,
    DEFAULT_PATCHRIGHT_APP_ID,
    DEFAULT_PATCHRIGHT_PORT,
    DEFAULT_THREAD,
    PATCHRIGHT_BACKEND,
    SURF_AGENT_WINDOW_TITLE,
)
from .cookie_import import CookieImporter
from .errors import SurfAgentError

APP_DIRS = PlatformDirs("surf-agent", appauthor=False)


class SurfAgent:
    def __init__(
        self,
        *,
        backend: str | None = None,
        axi_bin: str | None = None,
        chrome_bin: str | None = None,
        command_timeout_s: float | None = None,
        bridge_client: AxiBridgeClient | None = None,
        state_file: Path | None = None,
        thread: str = DEFAULT_THREAD,
        state_dir: Path | None = None,
        chrome_profile_dir: Path | None = None,
        patchright_profile_dir: Path | None = None,
        chrome_class: str | None = None,
        patchright_app_id: str | None = None,
        patchright_class: str | None = None,
    ) -> None:
        self.axi_bin = axi_bin or os.environ.get("SURF_AGENT_AXI_BIN", DEFAULT_AXI_BIN)
        self.chrome_bin = (
            chrome_bin or os.environ.get("SURF_AGENT_CHROME_BIN") or find_chrome_bin()
        )
        self.command_timeout_s = (
            command_timeout_s if command_timeout_s is not None else parse_timeout_env()
        )
        self.state_file = state_file or default_state_file(
            thread=thread, state_dir=state_dir
        )
        self.state_dir = (
            self.state_file.parent
            if state_file
            else default_state_dir(state_dir=state_dir)
        )
        self.backend = validate_backend_name(backend) if backend is not None else parse_backend_env()
        self.chrome_profile_dir = chrome_profile_dir or default_chrome_profile_dir()
        self.patchright_profile_dir = (
            patchright_profile_dir or default_patchright_profile_dir()
        )
        self.chrome_class = (
            chrome_class
            or os.environ.get("SURF_AGENT_CHROME_CLASS")
            or DEFAULT_CHROME_CLASS
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
        self.chrome_debug_port = parse_port_env(
            "SURF_AGENT_CHROME_DEBUG_PORT", DEFAULT_CHROME_DEBUG_PORT
        )
        self.patchright_port = parse_port_env(
            "SURF_AGENT_PATCHRIGHT_PORT", DEFAULT_PATCHRIGHT_PORT
        )
        self.browser_url = f"http://127.0.0.1:{self.chrome_debug_port}"
        self.patchright_client = PatchrightBridgeClient(
            timeout_s=self.command_timeout_s,
            port=self.patchright_port,
            profile_dir=self.patchright_profile_dir,
        )
        cookie_source = persisted_config.get_cookie_source(path=backend_config_file())
        self.cookie_import_enabled = cookie_source is not None
        destination_profile = (
            self.chrome_profile_dir
            if self.backend == AXI_BACKEND
            else self.patchright_profile_dir
        )
        self.destination_family = destination_browser_family(
            backend=self.backend, executable=self.chrome_bin
        )
        self.cookie_import_startup_error: str | None = None
        if (
            self.cookie_import_enabled
            and self.backend == AXI_BACKEND
            and axi_destination_identity_unprovable(os.environ)
        ):
            self.cookie_import_startup_error = "cannot prove the AXI destination profile while an auto-connect or browser URL override is active"
        elif self.cookie_import_enabled and self.destination_family is None:
            self.cookie_import_startup_error = (
                "cannot prove the browser family of the AXI destination executable"
            )
        elif (
            self.cookie_import_enabled
            and cookie_source is not None
            and cookie_source.family != self.destination_family
        ):
            self.cookie_import_startup_error = "cookie source browser family does not match the selected Surf destination browser"
        importer = (
            CookieImporter(
                config=cookie_source,
                destination_root=destination_profile,
                state_root=surf_agent_state_dir(),
                destination_family=self.destination_family,
                process_inspector=lambda profile: bool(
                    find_active_chrome_roots(profile)
                ),
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
            expected_profile_dir=self.chrome_profile_dir
            if self._uses_dedicated_chrome_profile()
            else None,
            expected_chrome_class=self.chrome_class
            if self._uses_dedicated_chrome_profile()
            else None,
            expected_browser_url=self.browser_url
            if self._uses_dedicated_chrome_profile()
            else None,
        )
        self.browser_backend = create_backend(
            self,
            self.backend,
            patchright_client=self.patchright_client,
            welcome_url=surf_agent_welcome_url,
        )

    def reset_state(self) -> None:
        unlink_missing_ok(self.state_file)

    def profile_open(self, url: str = "about:blank") -> int:
        if self.backend == PATCHRIGHT_BACKEND:
            return self._patchright_profile_open(url)
        with self._axi_startup_guard():
            if self._chrome_debug_endpoint_ready():
                raise SurfAgentError(
                    f"automated Surf Agent Chrome is running at {self.browser_url}; close Surf Agent windows or run `Browser().stop_bridge()` before `Browser().open_profile()`"
                )
            if not self.chrome_bin:
                raise SurfAgentError(
                    "could not find Chrome executable for profile open; set SURF_AGENT_CHROME_BIN"
                )
            self.chrome_profile_dir.mkdir(parents=True, exist_ok=True)
            command = [
                *shlex.split(self.chrome_bin),
                f"--class={self.chrome_class}",
                f"--user-data-dir={self.chrome_profile_dir}",
                "--new-window",
                url,
            ]
            proc = self._subprocess_run(
                command,
                check=False,
                text=True,
                capture_output=True,
                timeout=CHROME_NEW_WINDOW_TIMEOUT_S,
            )
            if proc.returncode != 0:
                detail = (
                    proc.stderr or proc.stdout or "Chrome profile open failed"
                ).strip()
                raise SurfAgentError(detail)
            return 0

    def _patchright_profile_open(self, url: str = "about:blank") -> int:
        return self.browser_backend.profile_open(
            url,
            profile_dir=str(self.patchright_profile_dir),
            app_id=self.patchright_app_id,
            window_class=self.patchright_class,
        )

    def _axi_startup_guard(self):
        if self.cookie_import_startup_error:
            raise SurfAgentError(self.cookie_import_startup_error)
        return self.lifecycle.launch_guard(
            health_check=self._chrome_debug_endpoint_ready
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

    def _chrome_debug_endpoint_ready(self) -> bool:
        try:
            with urllib.request.urlopen(
                f"{self.browser_url}/json/version", timeout=1.0
            ) as response:
                return response.status == 200
        except (OSError, urllib.error.URLError, TimeoutError):
            return False

    def _uses_dedicated_chrome_profile(self) -> bool:
        return uses_dedicated_chrome_profile(os.environ)

    def _subprocess_run(
        self, command: Sequence[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(command, **kwargs)

    def _subprocess_popen(
        self, command: Sequence[str], **kwargs: Any
    ) -> subprocess.Popen[str]:
        return subprocess.Popen(command, **kwargs)


def default_state_file(
    *, thread: str = DEFAULT_THREAD, state_dir: Path | None = None
) -> Path:
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
    value = os.environ.get("SURF_AGENT_CHROME_PROFILE_DIR") or os.environ.get(
        "CHROME_DEVTOOLS_AXI_USER_DATA_DIR"
    )
    if value:
        return Path(value).expanduser()
    return surf_agent_data_dir() / "profiles" / "chrome"


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
    value = os.environ.get("SURF_AGENT_AXI_TIMEOUT", "") or os.environ.get(
        "SURF_AGENT_COMMAND_TIMEOUT", ""
    )
    if not value:
        return DEFAULT_AXI_TIMEOUT_S
    try:
        timeout = float(value)
    except ValueError as exc:
        raise SurfAgentError(
            "SURF_AGENT_AXI_TIMEOUT must be a number", exit_code=2
        ) from exc
    if timeout <= 0:
        raise SurfAgentError(
            "SURF_AGENT_AXI_TIMEOUT must be greater than zero", exit_code=2
        )
    return timeout


def parse_port_env(name: str, default: str) -> int:
    raw = os.environ.get(name, default)
    value = coerce_int(raw)
    if value is None or value <= 0 or value > 65535:
        raise SurfAgentError(f"{name} must be a TCP port number", exit_code=2)
    return value


def default_axi_env(
    *,
    profile_dir: Path | None = None,
    chrome_class: str = DEFAULT_CHROME_CLASS,
    browser_url: str | None = None,
) -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("CHROME_DEVTOOLS_AXI_PORT", DEFAULT_AXI_PORT)
    if uses_dedicated_chrome_profile(env):
        env.setdefault(
            "CHROME_DEVTOOLS_AXI_BROWSER_URL",
            browser_url or f"http://127.0.0.1:{DEFAULT_CHROME_DEBUG_PORT}",
        )
    return env


def uses_dedicated_chrome_profile(env: dict[str, str]) -> bool:
    return env.get("CHROME_DEVTOOLS_AXI_AUTO_CONNECT") != "1" and not env.get(
        "CHROME_DEVTOOLS_AXI_BROWSER_URL"
    )


def stop_axi_chrome_runtime(profile_dir: Path, *, debug_port: int) -> list[int]:
    return terminate_processes(
        find_chrome_root_processes(profile_dir, remote_debugging_port=debug_port)
    )


def stop_patchright_runtime(profile_dir: Path, *, port: int) -> list[int]:
    stopped = stop_module_bridge_processes(
        "surf_agent.backends.patchright.bridge", port=port, profile_dir=profile_dir
    )
    stopped.extend(
        terminate_processes(
            find_chrome_root_processes(profile_dir, remote_debugging_pipe=True)
        )
    )
    return stopped


def stop_module_bridge_processes(
    module: str, *, port: int, profile_dir: Path
) -> list[int]:
    return terminate_processes(
        find_module_bridge_processes(module, port=port, profile_dir=profile_dir)
    )


def find_chrome_root_processes(
    profile_dir: Path,
    *,
    remote_debugging_port: int | None = None,
    remote_debugging_pipe: bool = False,
) -> list[int]:
    wanted_profile = str(profile_dir)
    pids: list[int] = []
    for pid, args in iter_process_args():
        if pid == os.getpid() or not args:
            continue
        if any(arg.startswith("--type=") for arg in args):
            continue
        if not has_arg_value(args, "--user-data-dir", wanted_profile):
            continue
        if remote_debugging_port is not None and has_arg_value(
            args, "--remote-debugging-port", str(remote_debugging_port)
        ):
            pids.append(pid)
            continue
        if remote_debugging_pipe and "--remote-debugging-pipe" in args:
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


def find_chrome_bin() -> str | None:
    for candidate in (
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "brave-browser",
        "microsoft-edge",
    ):
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


def unlink_missing_ok(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


SnapshotMode = str


def python_module_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None
