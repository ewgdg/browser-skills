#!/usr/bin/env python3
"""Run ordinary Python with the skill's released Surf dependency."""

import os
from pathlib import Path
import re
import shutil
import sys


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 run.py FILE|- [script arguments...]", file=sys.stderr)
        return 2

    override = os.environ.get("SURF_AGENT_DEPENDENCY")
    if override is not None:
        wheel = Path(override).expanduser()
        if not wheel.is_absolute() or wheel.suffix != ".whl" or not wheel.is_file():
            print("SURF_AGENT_DEPENDENCY must be an absolute path to a built wheel.",
                  file=sys.stderr)
            return 2
        dependency = f"surf-agent[patchright] @ {wheel.as_uri()}"
    else:
        revision = (Path(__file__).resolve().parents[1] / "runtime-revision").read_text().strip()
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            print(
                "Surf runtime is UNRELEASED or has an invalid revision. "
                "Release requires a reachable commit pin; for local validation set "
                "SURF_AGENT_DEPENDENCY to an absolute built-wheel path.",
                file=sys.stderr,
            )
            return 2
        dependency = (
            "surf-agent[patchright] @ git+https://github.com/ewgdg/browser-skills.git@"
            f"{revision}#subdirectory=packages/surf-agent"
        )

    uv = shutil.which("uv")
    if uv is None:
        print("Surf requires uv: https://docs.astral.sh/uv/getting-started/installation/",
              file=sys.stderr)
        return 127
    # Execute Python directly: uv must not reinterpret script metadata or change
    # ordinary Python's stdin, sibling imports, arguments, or exception behavior.
    os.execv(uv, [
        uv, "run", "--no-project", "--no-config", "--isolated", "--python", "3.11",
        "--with", dependency, "--", "python", "--", *sys.argv[1:],
    ])


if __name__ == "__main__":
    raise SystemExit(main())
