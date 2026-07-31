from __future__ import annotations

import io
import json
from collections.abc import Callable

from surf_chatgpt import cli
from surf_chatgpt.browser_lifecycle import BrowserSessionLifecycle
from surf_chatgpt.browser_port import BrowserPageState


class MemoryBrowserPort:
    def __init__(self) -> None:
        self.pages: dict[str, str] = {}
        self.drafts: dict[str, str] = {}
        self.sent_prompts: list[str] = []
        self.renames: list[tuple[str, str]] = []
        self.evaluations = iter(
            [
                {"state": "ready", "selection": {}},
                {"state": "ready", "selection": {}},
                {"state": "submitted"},
                {"state": "session", "session_id": "abc123"},
            ]
        )

    def ensure(self, thread: str, url: str) -> None:
        self.pages[thread] = url

    def evaluate(self, thread: str, source: str) -> object:
        assert thread in self.pages
        assert source
        return next(self.evaluations)

    def fill(self, thread: str, target: str, text: str) -> None:
        assert target
        self.drafts[thread] = text

    def click(
        self,
        thread: str,
        target: str,
        *,
        on_may_have_dispatched: Callable[[], None],
    ) -> None:
        assert target
        on_may_have_dispatched()
        self.sent_prompts.append(self.drafts[thread])

    def rename(self, source_thread: str, destination_thread: str) -> None:
        self.pages[destination_thread] = self.pages.pop(source_thread)
        self.drafts[destination_thread] = self.drafts.pop(source_thread)
        self.renames.append((source_thread, destination_thread))

    def state(self, thread: str) -> BrowserPageState | None:
        url = self.pages.get(thread)
        return None if url is None else BrowserPageState(thread=thread, url=url)

    def close(self, thread: str) -> None:
        self.pages.pop(thread, None)


def test_plain_ask_uses_thread_addressed_browser_operations() -> None:
    browser = MemoryBrowserPort()
    lifecycle = BrowserSessionLifecycle(
        browser,
        submission_thread_factory=lambda: "temporary",
    )
    output = io.StringIO()

    exit_code = cli.main(
        ["ask", "hello"],
        stdin=io.StringIO(),
        stdout=output,
        lifecycle=lifecycle,
    )

    assert exit_code == 0
    assert json.loads(output.getvalue()) == {
        "ok": True,
        "session": {"id": "abc123"},
    }
    assert browser.sent_prompts == ["hello"]
    assert browser.renames == [
        (
            "temporary",
            "surf-chatgpt-session-6ca13d52ca70c883e0f0bb101e425a89e8624de51db2d2392593af6a84118090",
        )
    ]
