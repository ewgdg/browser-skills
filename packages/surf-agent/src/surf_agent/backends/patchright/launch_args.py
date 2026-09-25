"""Patchright's own Chrome flags, read from the installed Patchright rather than copied into Surf."""

from __future__ import annotations

import shlex
import tempfile
from pathlib import Path
from typing import Any

# Patchright appends this as the startup page of every persistent launch.
STARTUP_PAGE_ARG = "about:blank"
# The stub exits at once; a slow answer means Patchright never started it.
CAPTURE_TIMEOUT_MS = 10_000


async def capture_default_args(chromium: Any, **launch_options: Any) -> list[str]:
    """The flags Patchright would pass to Chrome for these launch options.

    Patchright launches a stub instead of Chrome. The stub records its argv and
    exits, so no browser starts and nothing takes focus. A launch with
    ignore_default_args=True needs this full list, and reading it here keeps Surf
    in step with whatever Patchright version is installed.
    """
    with tempfile.TemporaryDirectory(prefix="surf-patchright-args-") as directory:
        recorded = Path(directory) / "argv"
        stub = Path(directory) / "chrome"
        # /bin/sh rather than a Python shebang: an interpreter path containing spaces
        # breaks a shebang line. NUL separators keep every argument intact.
        stub.write_text(
            "#!/bin/sh\n"
            f"for arg in \"$@\"; do printf '%s\\0' \"$arg\"; done > {shlex.quote(str(recorded))}\n"
        )
        stub.chmod(0o700)
        failure: Exception | None = None
        try:
            await chromium.launch_persistent_context(
                executable_path=str(stub), timeout=CAPTURE_TIMEOUT_MS, **launch_options
            )
        except Exception as exc:  # expected: the stub exits instead of answering Patchright
            failure = exc
        if not recorded.exists():
            raise RuntimeError("could not read Patchright's Chrome flags: its launch never ran the executable") from failure
        return recorded.read_bytes().decode().split("\0")[:-1]
