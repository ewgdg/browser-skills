from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from surf_agent.backends.local_bridge import LocalBridgeClient
from surf_agent.errors import BridgeUnavailable


@dataclass(frozen=True)
class BrowserPageState:
    thread: str
    url: str


class BrowserPagePort(Protocol):
    def ensure(self, thread: str, url: str) -> None: ...

    def evaluate(self, thread: str, source: str) -> object: ...

    def fill(self, thread: str, target: str, text: str) -> None: ...

    def click(
        self,
        thread: str,
        target: str,
        *,
        on_may_have_dispatched: Callable[[], None],
    ) -> None: ...

    def rename(self, source_thread: str, destination_thread: str) -> None: ...

    def state(self, thread: str) -> BrowserPageState | None: ...

    def close(self, thread: str) -> None: ...


class BridgeBrowserPagePort:
    def __init__(self, client: LocalBridgeClient) -> None:
        self._client = client

    def ensure(self, thread: str, url: str) -> None:
        current = self.state(thread)
        if current is not None and current.url == url:
            return
        self._client.call_tool("open", {"thread": thread, "url": url})

    def evaluate(self, thread: str, source: str) -> object:
        raw = self._client.call_tool_if_running(
            "eval",
            {"thread": thread, "code": source},
        )
        if raw is None:
            raise BridgeUnavailable("The browser bridge connection ended.")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as error:
            raise ValueError("Browser evaluation returned invalid JSON.") from error

    def fill(self, thread: str, target: str, text: str) -> None:
        self._client.call_tool(
            "fill",
            {"thread": thread, "uid": target, "text": text},
        )

    def click(
        self,
        thread: str,
        target: str,
        *,
        on_may_have_dispatched: Callable[[], None],
    ) -> None:
        raw = self._client.call_tool_if_running(
            "click",
            {"thread": thread, "uid": target},
            on_request_may_have_been_dispatched=on_may_have_dispatched,
        )
        if raw is None:
            raise BridgeUnavailable("The browser bridge connection ended.")

    def rename(self, source_thread: str, destination_thread: str) -> None:
        raw = self._client.call_tool_if_running(
            "rename-thread",
            {
                "thread": source_thread,
                "destination_thread": destination_thread,
            },
        )
        if raw is None:
            raise BridgeUnavailable("The browser bridge connection ended.")

    def state(self, thread: str) -> BrowserPageState | None:
        raw = self._client.call_tool("state", {"thread": thread})
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ValueError("Browser state returned invalid JSON.") from error
        if not isinstance(value, dict) or value.get("open") is not True:
            return None
        url = value.get("url")
        if not isinstance(url, str):
            raise ValueError("Open browser state requires a URL.")
        return BrowserPageState(thread=thread, url=url)

    def close(self, thread: str) -> None:
        raw = self._client.call_tool_if_running("close", {"thread": thread})
        if raw is None:
            raise BridgeUnavailable("The browser bridge connection ended.")
