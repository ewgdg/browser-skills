#!/usr/bin/env python3
"""Run ordinary Python with the skill's released Surf dependency."""

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import sys

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

# Read by surf_agent.session so a detached worker re-enters this same dependency
# resolution instead of trusting the temporary environment uv removes on exit.
WORKER_COMMAND_ENV = "SURF_SESSION_WORKER_COMMAND"


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


@dataclass(frozen=True)
class Invocation:
    session_mode: bool
    python_arguments: list[str]


def positive_seconds(value: str, option: str) -> str | None:
    try:
        seconds = float(value)
    except ValueError:
        seconds = 0.0
    if not math.isfinite(seconds) or seconds <= 0:
        print(f"{option} expects positive seconds, got {value!r}.", file=sys.stderr)
        return None
    return value


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
        return Invocation(True, [*SESSION_COMMAND, "kill", "--session", kill_target])
    if mode == "--list-sessions":
        if extras:
            print(f"--list-sessions takes no other options.\n{USAGE}", file=sys.stderr, end="")
            return None
        return Invocation(True, [*SESSION_COMMAND, "list"])
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

    if timeout is not None and positive_seconds(timeout, "--timeout") is None:
        return None
    if idle_timeout is not None and mode != "--new-session":
        print("--ttl applies when a session is created.", file=sys.stderr)
        return None
    if name is not None and mode != "--new-session":
        print("--name applies when a session is created.", file=sys.stderr)
        return None
    if idle_timeout is not None and positive_seconds(idle_timeout, "--ttl") is None:
        return None
    if mode == "--session" and reset:
        if rest or timeout is not None:
            print("--reset discards bindings and takes no source or timeout.", file=sys.stderr)
            return None
        return Invocation(True, [*SESSION_COMMAND, "reset", "--session", session])
    if not rest or rest[0] != "-":
        print(
            "A session cell is read from stdin: pass '-' as the source. "
            "Use a file without a session option for an ordinary script.",
            file=sys.stderr,
        )
        return None
    command = [*SESSION_COMMAND, "cell"]
    if mode == "--new-session":
        command.append("--new-session")
        if name is not None:
            command += ["--name", name]
        if idle_timeout is not None:
            command += ["--ttl", idle_timeout]
    else:
        command += ["--session", session]
    if timeout is not None:
        command += ["--timeout", timeout]
    return Invocation(True, command + rest)


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
    uv_command = [
        uv, "run", "--no-project", "--no-config", "--isolated", "--python", "3.11",
        "--with", dependency, "--", "python",
    ]
    environment = dict(os.environ)
    if invocation.session_mode:
        environment[WORKER_COMMAND_ENV] = json.dumps(
            [*uv_command, *SESSION_COMMAND, "worker"]
        )
    # Execute Python directly: uv must not reinterpret script metadata or change
    # ordinary Python's stdin, sibling imports, arguments, or exception behavior.
    os.execve(uv, [*uv_command, *invocation.python_arguments], environment)


if __name__ == "__main__":
    raise SystemExit(main())
