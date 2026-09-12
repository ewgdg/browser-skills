"""Importable browser interaction handles for Surf."""

from __future__ import annotations

import sys
from typing import TextIO

from .cli import SnapshotCapture, SurfAgent, choose_snapshot_diff, safe_thread_name
from .constants import DEFAULT_THREAD
from .errors import SurfAgentError


Snapshot = SnapshotCapture


def _create_agent(name: str) -> SurfAgent:
    """Construct the existing lifecycle-owning agent at the private test seam."""
    return SurfAgent(thread=name)


class Thread:
    """A named, agent-owned browser interaction context.

    A thread maps to Surf's currently dedicated browser window. It is not a
    tab selector: callers own this context and should use one handle for the
    lifetime of an interaction.
    """

    def __init__(self, name: str = DEFAULT_THREAD) -> None:
        self.name = safe_thread_name(name)
        self._agent = _create_agent(self.name)
        self._baseline: Snapshot | None = None

    def open(self, url: str) -> str:
        """Navigate this thread's page to *url* and return backend output."""
        self._baseline = None
        return self._agent.browser_backend.open(url)

    def snapshot(self) -> Snapshot:
        """Capture a complete snapshot value without changing emission state."""
        return self._capture()

    def emit(self, snapshot: Snapshot, *, full: bool = False, sink: TextIO | None = None) -> None:
        """Emit an already captured snapshot and advance baseline on success.

        Automatic emission compares against the last successfully emitted
        snapshot. Before a baseline exists it emits the complete value.
        """
        if not isinstance(snapshot, SnapshotCapture):
            raise TypeError("emit expects a snapshot returned by snapshot()")
        if full or self._baseline is None:
            output = snapshot.text
        else:
            output = choose_snapshot_diff(self._baseline, snapshot).output
        destination = sys.stdout if sink is None else sink
        destination.write(output)
        self._baseline = snapshot

    def close(self) -> None:
        """Close this thread's managed browser page."""
        status = self._agent.browser_backend.close_silently()
        if status not in (None, 0):
            raise SurfAgentError(f"close failed for thread {self.name}: backend returned status {status}")
        self._baseline = None

    def _capture(self) -> Snapshot:
        capture = self._agent.browser_backend.capture_snapshot()
        if not isinstance(capture, SnapshotCapture):
            raise TypeError("browser backend returned an invalid snapshot capture")
        return capture


__all__ = ["Snapshot", "Thread"]
