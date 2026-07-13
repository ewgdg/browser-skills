from __future__ import annotations

import contextlib
import fcntl
import os
import shlex
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Iterator, Protocol, Sequence

from .errors import SurfAgentError


class CookieImportRunner(Protocol):
    def run(self, force: bool) -> object: ...


class ChromeLifecycleCoordinator:
    """Coordinates import, destination launches, and idle stopping under one lock."""

    def __init__(
        self,
        *,
        destination_root: Path,
        state_root: Path,
        importer: CookieImportRunner | None = None,
        process_inspector: Callable[[Path], bool] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.destination_root = destination_root
        self.state_root = state_root
        self.importer = importer
        self.process_inspector = process_inspector or (lambda _path: False)
        self.sleeper = sleeper
        self.clock = clock
        self._local = threading.local()

    @property
    def lock_file(self) -> Path:
        return self.state_root / "chrome-lifecycle.lock"

    @contextlib.contextmanager
    def launch_guard(self, *, health_check: Callable[[], bool]) -> Iterator[None]:
        """Serialize a destination launch and preflight only when it is needed."""
        with self.locked():
            if health_check():
                yield
                return
            self._import_if_configured(force=False)
            # Another process may have completed startup while this caller waited.
            if health_check():
                yield
                return
            yield

    # Kept as the narrow callback seam used by LocalBridgeClient.
    startup_guard = launch_guard

    def import_now(self) -> object:
        """Force an explicit import while the same launch lock is held."""
        with self.locked():
            return self._import_if_configured(force=True, required=True)

    def _import_if_configured(self, *, force: bool, required: bool = False) -> object | None:
        if self.importer is None:
            if required:
                raise SurfAgentError("no cookie source is configured; run `surf-agent profile cookie-source set ...` first")
            return None
        # Check while holding the lifecycle lock. CookieImporter checks again before
        # mutation, so an external launch between these checks still fails closed.
        if self.process_inspector(self.destination_root):
            raise SurfAgentError("destination Surf browser profile is active; cannot refresh cookies before startup")
        return self.importer.run(force=force)

    def stop_if_idle(self, *, page_inventory: Callable[[], Sequence[object] | None], stop: Callable[[], object]) -> bool:
        """Stop only after two successful, zero user-visible-page inventories."""
        with self.locked():
            first = page_inventory()
            if first is None:
                self._warn_unknown_inventory()
                return False
            if first:
                return False
            self.sleeper(2.0)
            second = page_inventory()
            if second is None:
                self._warn_unknown_inventory()
                return False
            if second:
                return False
            try:
                stop()
            except Exception as exc:
                print(f"surf-agent: warning: could not stop idle browser bridge: {exc}", file=sys.stderr)
                return False
            return True

    def _warn_unknown_inventory(self) -> None:
        print("surf-agent: warning: could not verify whether user-visible browser pages remain; leaving bridge running", file=sys.stderr)

    @contextlib.contextmanager
    def locked(self) -> Iterator[None]:
        depth = getattr(self._local, "depth", 0)
        if depth:
            self._local.depth = depth + 1
            try:
                yield
            finally:
                self._local.depth -= 1
            return
        descriptor: int | None = None
        try:
            self.state_root.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(self.lock_file, os.O_CREAT | os.O_RDWR, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            self._local.depth = 1
            yield
        except OSError as exc:
            raise SurfAgentError(f"could not acquire browser lifecycle lock: {exc}") from exc
        finally:
            self._local.depth = 0
            if descriptor is not None:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)


def browser_executable_family(executable: str | None) -> str | None:
    if not executable:
        return None
    try:
        name = Path(shlex.split(executable)[0]).name.lower()
    except (IndexError, ValueError):
        return None
    if name in {"google-chrome", "google-chrome-stable", "google-chrome-beta", "google-chrome-unstable", "chrome"}:
        return "chrome"
    if name in {"chromium", "chromium-browser"}:
        return "chromium"
    if "brave" in name:
        return "brave"
    if name in {"microsoft-edge", "microsoft-edge-stable", "microsoft-edge-beta", "microsoft-edge-dev"}:
        return "edge"
    return None


def destination_browser_family(*, backend: str, executable: str | None) -> str | None:
    """Prove the family of the executable that owns the destination profile."""
    if backend == "patchright":
        return "chrome"  # Patchright explicitly launches the Chrome channel.
    return browser_executable_family(executable) if backend == "axi" else None

def find_active_chrome_roots(profile_dir: Path, *, process_args: Callable[[], Sequence[tuple[int, Sequence[str]]]] | None = None) -> list[int]:
    """Find any browser root using exactly this resolved user-data root."""
    wanted = profile_dir.expanduser().resolve(strict=False)
    inspector = process_args or _iter_process_args
    found: list[int] = []
    for pid, args in inspector():
        if pid == os.getpid() or not args or any(argument.startswith("--type=") for argument in args):
            continue
        value = _option_value(args, "--user-data-dir")
        if value is None:
            continue
        try:
            candidate = Path(value).expanduser().resolve(strict=False)
        except OSError:
            continue
        if candidate == wanted:
            found.append(pid)
    return found


def axi_destination_identity_unprovable(environ: dict[str, str]) -> bool:
    return environ.get("CHROME_DEVTOOLS_AXI_AUTO_CONNECT") == "1" or bool(environ.get("CHROME_DEVTOOLS_AXI_BROWSER_URL"))


def _option_value(args: Sequence[str], option: str) -> str | None:
    for index, argument in enumerate(args):
        if argument == option and index + 1 < len(args):
            return str(args[index + 1])
        if argument.startswith(f"{option}="):
            return argument.split("=", 1)[1]
    return None


def _iter_process_args() -> list[tuple[int, list[str]]]:
    result: list[tuple[int, list[str]]] = []
    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        return result
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        args = [part.decode(errors="replace") for part in raw.split(b"\0") if part]
        if args:
            result.append((int(entry.name), args))
    return result
