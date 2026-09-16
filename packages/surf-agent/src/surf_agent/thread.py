"""Importable browser interaction handles for Surf."""

from __future__ import annotations

import sys
from typing import Any, TextIO

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

    def is_open(self) -> bool:
        """Return managed-open state without starting a backend.

        AXI reports remembered local state; local bridge backends query only a
        running bridge. Neither path creates a missing browser window.
        """
        return self._agent.browser_backend.is_open()

    def click(self, target: str) -> str:
        return self._agent.browser_backend.click(target)

    def fill(self, target: str, text: str) -> str:
        return self._agent.browser_backend.fill(target, text)

    def type_text(self, text: str) -> str:
        return self._agent.browser_backend.type_text(text)

    def press(self, key: str) -> str:
        return self._agent.browser_backend.press(key)

    def scroll(self, direction: str) -> str:
        return self._agent.browser_backend.scroll(direction)

    def wait(self, target: int | str) -> str:
        if isinstance(target, bool):
            raise TypeError("wait target must be milliseconds as int or visible text as str")
        if isinstance(target, int):
            if target < 0:
                raise ValueError("wait milliseconds must not be negative")
            return self._agent.browser_backend.wait_ms(target)
        if not isinstance(target, str) or not target:
            raise ValueError("wait text target must not be empty")
        return self._agent.browser_backend.wait_for_text(target)

    def back(self) -> str:
        self._baseline = None
        return self._agent.browser_backend.back()

    def text(self) -> str:
        return self._agent.browser_backend.text()

    def screenshot(self, path: str, *, full_page: bool = False) -> str:
        from .backends.base import ScreenshotOptions

        return self._agent.browser_backend.screenshot(ScreenshotOptions(path=path, full_page=full_page))

    def evaluate(self, code: str) -> Any:
        return self._agent.browser_backend.evaluate_value(code)

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
