#!/usr/bin/env python3
"""Run the Google Search CLI with the runtime pinned by the sibling Surf skill."""

import importlib.util
import os
from pathlib import Path
import sys

# Sharing Surf's launcher and pin keeps both skills on one surf-agent revision,
# since they drive the same browser and state without a version handshake.
SURF_LAUNCHER = Path(__file__).resolve().parents[2] / "surf" / "scripts" / "run.py"


def load_surf_launcher():
    if not SURF_LAUNCHER.is_file():
        print(f"Google Search requires the Surf skill installed beside it at "
              f"{SURF_LAUNCHER.parents[1]}.", file=sys.stderr)
        return None
    specification = importlib.util.spec_from_file_location("surf_launcher", SURF_LAUNCHER)
    launcher = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(launcher)
    return launcher


def main():
    surf = load_surf_launcher()
    if surf is None:
        return 2
    requirements = [
        surf.dependency_requirement(),
        surf.dependency_requirement(
            "surf-google-search", extras="", override_variable="SURF_GOOGLE_SEARCH_DEPENDENCY",
        ),
    ]
    if None in requirements:
        return 2
    uv_command = surf.uv_python_command(requirements)
    if uv_command is None:
        return 127
    # -P keeps the caller's working directory off sys.path, so a same-named local
    # module cannot shadow the pinned package.
    os.execv(uv_command[0], [*uv_command, "-P", "-m", "surf_google_search", *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
