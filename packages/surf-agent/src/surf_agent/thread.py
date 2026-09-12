"""Importable browser interaction handles for Surf."""

from __future__ import annotations

from typing import Any

from .cli import SnapshotCapture, SurfAgent, choose_snapshot_diff, safe_thread_name
from .constants import DEFAULT_THREAD


class Thread:
    """A named, agent-owned browser interaction context.

    A thread maps to Surf's currently dedicated browser window.  It is not a
    tab selector: callers own this context and should use one handle for the
    lifetime of an interaction.
    """

    def __init__(self, name: str, *, agent: Any) -> None:
        self.name = safe_thread_name(name)
        self._agent = agent
        self._baseline: SnapshotCapture | None = None

    @classmethod
    def acquire(cls, name: str = DEFAULT_THREAD, **agent_options: Any) -> "Thread":
        """Acquire a named browser interaction context."""
        normalized_name = safe_thread_name(name)
        return cls(normalized_name, agent=SurfAgent(thread=normalized_name, **agent_options))

    def open(self, url: str) -> str:
        """Navigate this thread's page to *url* and return backend output."""
        self._baseline = None
        return self._agent.browser_backend.open(url)

    def snapshot(self) -> str:
        """Capture and return the full current accessibility snapshot value."""
        current = self._capture()
        self._baseline = current
        return current.text

    def emit(self, *, full: bool = False) -> str:
        """Capture a snapshot, returning a useful diff or an explicit full value.

        The latest capture becomes the baseline for the next emission.  Before
        a baseline exists, automatic emission returns the full value.
        """
        current = self._capture()
        if full or self._baseline is None:
            output = current.text
        else:
            output = choose_snapshot_diff(self._baseline, current).output
        self._baseline = current
        return output

    def close(self) -> None:
        """Close this thread's managed browser page."""
        self._agent.browser_backend.close()
        self._baseline = None

    def _capture(self) -> SnapshotCapture:
        capture = self._agent.browser_backend.capture_snapshot()
        if not isinstance(capture, SnapshotCapture):
            raise TypeError("browser backend returned an invalid snapshot capture")
        return capture


__all__ = ["Thread"]
