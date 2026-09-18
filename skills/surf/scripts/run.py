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

# What the runtime is installed into. The launcher keeps one uv project of its own
# instead of letting uv build a temporary environment per call: `uv run --with` forks
# the child and stays alive as its parent so that it can delete that environment
# afterwards, which for a session means a uv process for its whole idle timeout.
# `uv tool install` cannot be used here at all - it requires console scripts, and this
# package deliberately has none, so uv removes the tool again.
PYTHON_REQUEST = "3.11"
PROJECT_FILE = "pyproject.toml"
LOCK_FILE = "uv.lock"
INSTALLED_STAMP = "installed-requirement"
ENVIRONMENT_DIR_ENV = "SURF_AGENT_ENV_DIR"
PROJECT_TEMPLATE = """\
# Written by the Surf launcher: it owns the environment in .venv, do not edit.
[project]
name = "surf-runtime"
version = "0"
requires-python = ">=3.11"
dependencies = ["{requirement}"]

[tool.uv]
package = false
"""


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
    """The directory holding the runtime environment uv manages for this skill.

    A cache rather than a state directory: it is rebuildable from the requirement, so
    losing it costs one install and never data. `SURF_AGENT_ENV_DIR` moves it.
    """
    override = os.environ.get(ENVIRONMENT_DIR_ENV)
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        cache_home = os.environ.get("XDG_CACHE_HOME")
        base = Path(cache_home).expanduser() if cache_home else Path.home() / ".cache"
    return base / "surf-agent"


def declared_requirement(requirement: str) -> str:
    """The requirement as uv should see it, with a file's digest when it names one.

    A rebuilt development wheel keeps its path, and uv's lock refuses the changed
    bytes with a hash mismatch rather than installing them; declaring the digest turns
    a rebuild into a requirement change, which is answered by re-resolving.
    """
    head, separator, tail = requirement.partition("file://")
    if not separator:
        return requirement
    location = unquote(urlparse("file://" + tail).path)
    try:
        digest = hashlib.sha256(Path(location).read_bytes()).hexdigest()
    except OSError:
        return requirement
    return f"{head}file://{tail.split('#', 1)[0]}#sha256={digest}"


def _installed_requirement(project: Path) -> str:
    try:
        return (project / INSTALLED_STAMP).read_text()
    except OSError:
        return ""


def _report_installer_failure(result: subprocess.CompletedProcess) -> None:
    detail = (result.stderr or result.stdout or "").strip()
    tail = "\n".join(detail.splitlines()[-5:]) or "no output"
    print(f"surf: could not prepare the Surf runtime:\n{tail}", file=sys.stderr)


def ensure_environment(uv: str, requirement: str) -> Path | None:
    """The interpreter of the environment uv keeps for *requirement*.

    Built once and then reused by every later call, including the session workers
    that outlive the launcher: uv owns the project directory, its lock file and the
    virtual environment inside it, so a new revision updates one environment instead
    of accumulating them. Returns None after reporting why it could not be prepared.
    """
    project = environment_root()
    python = project / ".venv" / "bin" / "python"
    declared = declared_requirement(requirement)
    if python.exists() and _installed_requirement(project) == declared:
        # Nothing to do: a steady-state call starts no uv process at all.
        return python
    try:
        project.mkdir(parents=True, exist_ok=True)
        (project / PROJECT_FILE).write_text(PROJECT_TEMPLATE.format(requirement=declared))
        # A lock from the previous requirement refuses the new one outright, and
        # re-resolving costs one cached resolution.
        with contextlib.suppress(OSError):
            (project / LOCK_FILE).unlink()
    except OSError as exc:
        print(f"surf: could not prepare {project}: {exc}", file=sys.stderr)
        return None
    result = subprocess.run(
        [uv, "sync", "--project", str(project), "--python", PYTHON_REQUEST, "--no-config"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        _report_installer_failure(result)
        return None
    try:
        (project / INSTALLED_STAMP).write_text(declared)
    except OSError as exc:
        print(f"surf: could not record the installed runtime: {exc}", file=sys.stderr)
        return None
    return python


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
