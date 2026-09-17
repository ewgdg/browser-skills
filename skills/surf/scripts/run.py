#!/usr/bin/env python3
"""Run ordinary Python with the skill's released Surf dependency."""

import json
import math
import os
from pathlib import Path
import re
import shutil
import sys

USAGE = (
    "Usage: python3 run.py FILE|- [script arguments...]\n"
    "       python3 run.py --session NAME [--timeout SECONDS] - [arguments...]\n"
    "       python3 run.py --session NAME --reset\n"
)

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


def split_options(arguments: list[str]) -> tuple[str | None, str | None, bool, list[str]]:
    """Take only leading options; everything from FILE|- onward belongs to Python."""
    session = None
    timeout = None
    reset = False
    index = 0
    while index < len(arguments) and arguments[index].startswith("--"):
        option = arguments[index]
        if option == "--session" and index + 1 < len(arguments):
            session = arguments[index + 1]
            index += 2
        elif option == "--timeout" and index + 1 < len(arguments):
            timeout = arguments[index + 1]
            index += 2
        elif option == "--reset":
            reset = True
            index += 1
        else:
            break
    return session, timeout, reset, arguments[index:]


def session_arguments(
    session: str, timeout: str | None, reset: bool, rest: list[str]
) -> list[str] | None:
    """The Python arguments for session mode, or None after reporting a usage error."""
    if not session:
        print(USAGE, file=sys.stderr, end="")
        return None
    if timeout is not None:
        try:
            value = float(timeout)
        except ValueError:
            value = 0.0
        if not math.isfinite(value) or value <= 0:
            print(f"--timeout expects positive seconds, got {timeout!r}.", file=sys.stderr)
            return None
    if reset:
        if rest or timeout is not None:
            print("--reset discards bindings and takes no source or timeout.", file=sys.stderr)
            return None
        return ["-m", "surf_agent.session", "reset", "--session", session]
    if not rest or rest[0] != "-":
        print(
            "Session mode runs one cell from stdin: pass '-' as the source. "
            "Use a file without --session for an ordinary script.",
            file=sys.stderr,
        )
        return None
    command = ["-m", "surf_agent.session", "cell", "--session", session]
    if timeout is not None:
        command += ["--timeout", timeout]
    return command + rest


def main():
    session, timeout, reset, rest = split_options(sys.argv[1:])
    if session is None:
        if reset or timeout is not None:
            print(f"--timeout and --reset require --session.\n{USAGE}", file=sys.stderr, end="")
            return 2
        if not rest:
            print(USAGE, file=sys.stderr, end="")
            return 2
        python_arguments = ["--", *rest]
    else:
        session_command = session_arguments(session, timeout, reset, rest)
        if session_command is None:
            return 2
        python_arguments = session_command

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
    if session is not None:
        environment[WORKER_COMMAND_ENV] = json.dumps(
            [*uv_command, "-m", "surf_agent.session", "worker"]
        )
    # Execute Python directly: uv must not reinterpret script metadata or change
    # ordinary Python's stdin, sibling imports, arguments, or exception behavior.
    os.execve(uv, [*uv_command, *python_arguments], environment)


if __name__ == "__main__":
    raise SystemExit(main())
