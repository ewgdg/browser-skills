#!/usr/bin/env python3
"""Run ordinary Python with the skill's released Surf dependency."""

import contextlib
import hashlib
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import unquote, urlparse

USAGE = (
    "Usage: python3 run.py FILE|- [script arguments...]\n"
    "       python3 run.py --new-session [--name SLUG] [--ttl SECONDS]"
    " [--timeout SECONDS] - [arguments...]\n"
    "       python3 run.py --session ID [--timeout SECONDS] - [arguments...]\n"
    "       python3 run.py --session ID --reset\n"
    "       python3 run.py --kill-session ID\n"
    "       python3 run.py --list-sessions\n"
)

SESSION_COMMAND = ["-m", "surf_agent.session"]
SESSION_MODES = ("--new-session", "--session", "--kill-session", "--list-sessions")
SESSION_VALUES = ("--name", "--ttl", "--timeout")

# What the runtime is installed into. The launcher owns this environment instead of
# letting uv build a temporary one per call: uv deletes the environments it creates
# for `uv run --with`, and a session worker has to outlive the launcher that made it.
PYTHON_REQUEST = "3.11"
ENVIRONMENT_MARKER = ".surf-requirement"
ENVIRONMENT_DIR_ENV = "SURF_AGENT_ENV_DIR"
# A build that another call is running must not be pruned out from under it.
BUILD_GRACE_S = 60.0


def dependency_requirement() -> str | None:
    override = os.environ.get("SURF_AGENT_DEPENDENCY")
    if override is not None:
        wheel = Path(override).expanduser()
        if not wheel.is_absolute() or wheel.suffix != ".whl" or not wheel.is_file():
            print("SURF_AGENT_DEPENDENCY must be an absolute path to a built wheel.",
                  file=sys.stderr)
            return None
        return f"surf-agent[patchright] @ {wheel.as_uri()}"
    revision = (Path(__file__).resolve().parents[1] / "runtime-revision").read_text().strip()
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        print(
            "This Surf skill has a missing or invalid runtime pin, so it is not "
            "correctly installed; update or reinstall it. For local development, "
            "set SURF_AGENT_DEPENDENCY to an absolute built-wheel path.",
            file=sys.stderr,
        )
        return None
    return (
        "surf-agent[patchright] @ git+https://github.com/ewgdg/browser-skills.git@"
        f"{revision}#subdirectory=packages/surf-agent"
    )


def environment_root() -> Path:
    """Where requirement-keyed environments live.

    A cache rather than a state directory: every environment is rebuildable from
    its requirement, and each one costs about 140 MB, so they are treated as
    something that may be dropped rather than something to protect.
    """
    override = os.environ.get(ENVIRONMENT_DIR_ENV)
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        cache_home = os.environ.get("XDG_CACHE_HOME")
        base = Path(cache_home).expanduser() if cache_home else Path.home() / ".cache"
    return base / "surf-agent" / "envs"


def _requirement_material(requirement: str) -> str:
    """Extra key material for a requirement that names a file.

    A rebuilt wheel keeps its path, so the path alone would keep the environment
    built from the previous bytes; the file's identity is part of the key instead.
    """
    head, separator, tail = requirement.partition("file://")
    if not separator:
        return ""
    location = unquote(urlparse("file://" + tail).path)
    try:
        info = Path(location).stat()
    except OSError:
        return ""
    return f"{info.st_mtime_ns}:{info.st_size}"


def environment_dir(requirement: str) -> Path:
    """The environment for one requirement, keyed by its digest and its files."""
    material = f"{requirement}\0{_requirement_material(requirement)}"
    digest = hashlib.sha256(material.encode()).hexdigest()[:16]
    return environment_root() / digest


def _usable(directory: Path, requirement: str) -> bool:
    try:
        recorded = (directory / ENVIRONMENT_MARKER).read_text()
    except OSError:
        return False
    return recorded == requirement and (directory / "bin" / "python").exists()


def _report_installer_failure(result: subprocess.CompletedProcess) -> None:
    detail = (result.stderr or result.stdout or "").strip()
    tail = "\n".join(detail.splitlines()[-5:]) or "no output"
    print(f"surf: could not prepare the Surf runtime:\n{tail}", file=sys.stderr)


def _live_command_lines() -> list[str]:
    """Running processes' command lines, from /proc where it exists and `ps` elsewhere."""
    if Path("/proc").is_dir():
        lines = []
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                raw = (entry / "cmdline").read_bytes()
            except OSError:
                continue
            if raw:
                lines.append(raw.replace(b"\0", b" ").decode(errors="replace"))
        return lines
    try:
        listing = subprocess.run(
            ["ps", "-ww", "-Ao", "pid=,command="],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return listing.stdout.splitlines()


def prune_environments(root: Path, keep: Path) -> None:
    """Drop environments nothing is using, so skill updates cannot pile up.

    A session worker outlives the call that started it, so an environment named by
    a live command line survives even when it is not the current one. Everything
    else is rebuildable from its requirement and only costs disk while it waits.
    """
    try:
        entries = list(root.iterdir())
    except OSError:
        return
    commands = _live_command_lines()
    for entry in entries:
        if entry == keep or not entry.is_dir() or entry.name.startswith("build-"):
            continue
        try:
            if time.time() - entry.stat().st_mtime < BUILD_GRACE_S:
                continue
        except OSError:
            continue
        if any(str(entry) in command for command in commands):
            continue
        shutil.rmtree(entry, ignore_errors=True)


def ensure_environment(uv: str, requirement: str) -> Path | None:
    """The interpreter of a persistent environment holding *requirement*.

    Built once with uv and reused by every later call, including the session
    workers that outlive the launcher. Returns None after reporting why it could
    not be built, so the caller can fail without pretending to have a runtime.
    """
    target = environment_dir(requirement)
    if _usable(target, requirement):
        # Recency keeps a warm environment from being pruned while it is in use.
        with contextlib.suppress(OSError):
            os.utime(target, None)
        return target / "bin" / "python"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        build = Path(tempfile.mkdtemp(prefix="build-", dir=target.parent))
    except OSError as exc:
        print(f"surf: could not prepare {target.parent}: {exc}", file=sys.stderr)
        return None
    try:
        for command in (
            [uv, "venv", "--no-config", "--python", PYTHON_REQUEST, str(build)],
            [uv, "pip", "install", "--no-config",
             "--python", str(build / "bin" / "python"), requirement],
        ):
            result = subprocess.run(command, capture_output=True, text=True)
            if result.returncode != 0:
                _report_installer_failure(result)
                return None
        (build / ENVIRONMENT_MARKER).write_text(requirement)
        try:
            # The rename is what makes an environment appear complete or not at all.
            os.rename(build, target)
        except OSError:
            # Another call finished first, or an unusable environment is in the way.
            if not _usable(target, requirement):
                shutil.rmtree(target, ignore_errors=True)
                os.rename(build, target)
    finally:
        shutil.rmtree(build, ignore_errors=True)
    prune_environments(target.parent, target)
    return target / "bin" / "python"


@dataclass(frozen=True)
class Invocation:
    session_mode: bool
    python_arguments: list[str]


def positive_seconds(value: str, option: str) -> float | None:
    try:
        seconds = float(value)
    except ValueError:
        seconds = 0.0
    if not math.isfinite(seconds) or seconds <= 0:
        print(f"{option} expects positive seconds, got {value!r}.", file=sys.stderr)
        return None
    return seconds


def run_request(request: dict) -> list[str]:
    """The runtime command for one request.

    The launcher owns the command-line grammar; the runtime receives a validated
    request, so the two never hold separate copies of the same options.
    """
    return [*SESSION_COMMAND, "run", json.dumps(request)]


def parse_arguments(arguments: list[str]) -> Invocation | None:
    """Parse leading options; everything from FILE|- onward belongs to Python.

    Returns None after reporting why a call cannot be built.
    """
    modes: list[str] = []
    values: dict[str, list[str]] = {}
    reset = False
    index = 0
    while index < len(arguments) and arguments[index].startswith("--"):
        option = arguments[index]
        if option == "--reset":
            reset = True
            index += 1
            continue
        if option not in SESSION_MODES and option not in SESSION_VALUES:
            break
        if option in SESSION_MODES:
            modes.append(option)
            if option in ("--new-session", "--list-sessions"):
                index += 1
                continue
        if index + 1 >= len(arguments):
            print(f"{option} expects a value.\n{USAGE}", file=sys.stderr, end="")
            return None
        values.setdefault(option, []).append(arguments[index + 1])
        index += 2
    rest = arguments[index:]

    repeated = [option for option, seen in values.items() if len(seen) > 1]
    if repeated:
        print(f"{repeated[0]} was given more than once.", file=sys.stderr)
        return None
    if len(modes) > 1:
        print(
            "Pass exactly one of --new-session, --session, --kill-session or --list-sessions.",
            file=sys.stderr,
        )
        return None
    mode = modes[0] if modes else None
    session = (values.get("--session") or [None])[0]
    kill_target = (values.get("--kill-session") or [None])[0]
    timeout = (values.get("--timeout") or [None])[0]
    idle_timeout = (values.get("--ttl") or [None])[0]
    name = (values.get("--name") or [None])[0]
    extras = rest or reset or timeout or idle_timeout or name

    if mode == "--kill-session":
        if extras:
            print(f"--kill-session takes only a session id.\n{USAGE}", file=sys.stderr, end="")
            return None
        return Invocation(True, run_request({"op": "kill", "session": kill_target}))
    if mode == "--list-sessions":
        if extras:
            print(f"--list-sessions takes no other options.\n{USAGE}", file=sys.stderr, end="")
            return None
        return Invocation(True, run_request({"op": "list"}))
    if mode is None:
        if reset or timeout or idle_timeout or name:
            print(
                "--reset, --ttl, --name and --timeout require --new-session or --session.",
                file=sys.stderr,
            )
            return None
        if not rest:
            print(USAGE, file=sys.stderr, end="")
            return None
        # A leading double dash stops option parsing, so the script keeps its own
        # arguments exactly as an ordinary Python run would receive them.
        return Invocation(False, ["--", *rest])

    timeout_value = None
    if timeout is not None:
        timeout_value = positive_seconds(timeout, "--timeout")
        if timeout_value is None:
            return None
    idle_timeout_value = None
    if idle_timeout is not None:
        if mode != "--new-session":
            print("--ttl applies when a session is created.", file=sys.stderr)
            return None
        idle_timeout_value = positive_seconds(idle_timeout, "--ttl")
        if idle_timeout_value is None:
            return None
    if name is not None and mode != "--new-session":
        print("--name applies when a session is created.", file=sys.stderr)
        return None
    if mode == "--session" and reset:
        if rest or timeout is not None:
            print("--reset discards bindings and takes no source or timeout.", file=sys.stderr)
            return None
        return Invocation(True, run_request({"op": "reset", "session": session}))
    if not rest or rest[0] != "-":
        print(
            "A session cell is read from stdin: pass '-' as the source. "
            "Use a file without a session option for an ordinary script.",
            file=sys.stderr,
        )
        return None
    request = {
        "op": "cell",
        "mode": "new" if mode == "--new-session" else "reuse",
        "argv": rest,
    }
    if mode == "--new-session":
        if name is not None:
            request["name"] = name
        if idle_timeout_value is not None:
            request["ttl"] = idle_timeout_value
    else:
        request["session"] = session
    if timeout_value is not None:
        request["timeout"] = timeout_value
    return Invocation(True, run_request(request))


def main():
    invocation = parse_arguments(sys.argv[1:])
    if invocation is None:
        return 2

    dependency = dependency_requirement()
    if dependency is None:
        return 2
    uv = shutil.which("uv")
    if uv is None:
        print("Surf requires uv: https://docs.astral.sh/uv/getting-started/installation/",
              file=sys.stderr)
        return 127
    python = ensure_environment(uv, dependency)
    if python is None:
        return 2
    # Execute the environment's Python directly: uv must not reinterpret script
    # metadata or change ordinary Python's stdin, sibling imports, arguments or
    # exception behavior, and a session worker keeps running after this exits.
    os.execve(str(python), [str(python), *invocation.python_arguments], dict(os.environ))


if __name__ == "__main__":
    raise SystemExit(main())
