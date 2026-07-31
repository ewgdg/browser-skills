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


def test_selection_inspection_affirms_picker_state_without_sending_and_closes_after_output() -> (
    None
):
    browser = MemoryBrowserPort()
    browser.evaluations = iter(
        [
            {
                "state": "ready",
                "selection": {"model": "GPT-5.6 Sol", "thinking": "Pro"},
            }
        ]
    )
    lifecycle = BrowserSessionLifecycle(
        browser,
        selection_thread_factory=lambda: "selection-diagnostic",
    )
    output = io.StringIO()

    exit_code = cli.main(
        [
            "selection",
            "inspect",
            "--model",
            "5.6 sol",
            "--thinking",
            "pro",
        ],
        stdin=io.StringIO(),
        stdout=output,
        lifecycle=lifecycle,
    )

    assert exit_code == 0
    assert json.loads(output.getvalue()) == {
        "ok": True,
        "selection": {"model": "GPT-5.6 Sol", "thinking": "Pro"},
    }
    assert browser.drafts == {}
    assert browser.sent_prompts == []
    assert "selection-diagnostic" not in browser.pages


def test_selection_inspection_retries_the_exact_preserved_thread_without_navigation() -> (
    None
):
    browser = MemoryBrowserPort()
    thread = "surf-chatgpt-selection-safe123"
    preserved_url = "https://chatgpt.com/?model=auto"
    browser.pages[thread] = preserved_url
    browser.evaluations = iter([{"state": "ready", "selection": {"thinking": "Pro"}}])
    lifecycle = BrowserSessionLifecycle(browser)
    output = io.StringIO()

    exit_code = cli.main(
        [
            "selection",
            "inspect",
            "--thinking",
            "pro",
            "--thread",
            thread,
            "--retain",
        ],
        stdin=io.StringIO(),
        stdout=output,
        lifecycle=lifecycle,
    )

    assert exit_code == 0
    assert json.loads(output.getvalue()) == {
        "ok": True,
        "selection": {"thinking": "Pro"},
        "thread": thread,
    }
    assert browser.pages == {thread: preserved_url}
    assert browser.drafts == {}
    assert browser.sent_prompts == []
