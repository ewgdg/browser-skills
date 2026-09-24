from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ScreenshotOptions:
    path: str
    full_page: bool = False


@dataclass(frozen=True)
class WaitConditions:
    """Page conditions that must all hold; ``timeout_ms=None`` selects the backend default."""

    text: str | None = None
    gone: str | None = None
    url: str | None = None
    timeout_ms: int | None = None


@dataclass(frozen=True)
class AgentPage:
    page_id: int
    url: str | None = None
    title: str | None = None
    backend: str = "axi"


class BrowserBackend(Protocol):
    name: str

    def open(self, url: str) -> str: ...

    def new(self) -> str: ...

    def is_open(self) -> bool: ...

    def list_threads(self) -> list[dict[str, Any]]: ...

    def snapshot(self) -> str: ...

    def text(self, target: str | None = None) -> str: ...

    def click(self, target: str) -> str: ...

    def fill(self, target: str, text: str) -> str: ...

    def type_text(self, text: str) -> str: ...

    def press(self, key: str) -> str: ...

    def scroll(self, direction: str) -> str: ...

    def wait_ms(self, milliseconds: int) -> str: ...

    def wait_for(self, conditions: WaitConditions) -> str: ...

    def back(self) -> str: ...

    def screenshot(self, options: ScreenshotOptions) -> str: ...

    def evaluate(self, code: str) -> str: ...

    def evaluate_value(self, code: str) -> Any: ...

    def close(self) -> int: ...

    def focus(self) -> int: ...

    def close_matching(self, pattern: str) -> int: ...

    def bridge_stop(self) -> int: ...

    def capture_snapshot(self) -> Any: ...
