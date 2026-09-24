"""Command lines of running processes, with argument boundaries intact."""

from __future__ import annotations

import psutil


def iter_process_args() -> list[tuple[int, list[str]]]:
    # psutil reads argv as the kernel stores it on Linux and macOS. Parsing `ps`
    # output instead loses boundaries, and macOS profile paths contain spaces
    # ("Application Support"), so --user-data-dir values would never match.
    processes: list[tuple[int, list[str]]] = []
    for process in psutil.process_iter(["cmdline"]):
        args = process.info["cmdline"]
        if args:
            processes.append((process.pid, list(args)))
    return processes
