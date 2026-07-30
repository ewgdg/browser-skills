from __future__ import annotations

import pytest
from patchright.sync_api import Browser, Page, sync_playwright

from surf_chatgpt.dom.cleanup import (
    classify_retained_page_source,
    request_stop_source,
)


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            yield browser
        finally:
            browser.close()


@pytest.fixture
def page(browser: Browser):
    page = browser.new_page()
    try:
        yield page
    finally:
        page.close()


def _set_fixture(page: Page, path: str, body: str) -> None:
    page.route(
        "**/*",
        lambda route: route.fulfill(
            status=200,
            body=f"<main>{body}</main>",
            content_type="text/html",
        ),
    )
    page.goto(f"https://chatgpt.com{path}")


def test_retained_page_classifier_affirms_generation_without_content(
    page: Page,
) -> None:
    _set_fixture(
        page,
        "/c/abc123",
        """
        <section data-testid="conversation-turn-1" data-turn="assistant">
          <div data-message-author-role="assistant" data-message-id="assistant-1">
            CANARY-private-generating-response
          </div>
        </section>
        <button data-testid="stop-button">Stop generating</button>
        <div id="prompt-textarea" contenteditable="true">Follow-up composer</div>
        <aside>CANARY-private-sidebar</aside>
        """,
    )

    metadata = page.evaluate(classify_retained_page_source())

    assert metadata == {"state": "generating"}
    assert "CANARY" not in str(metadata)


@pytest.mark.parametrize(
    ("path", "body"),
    [
        (
            "/c/abc123",
            '<form id="challenge-form" style="width:20px;height:20px">Verify</form>',
        ),
        ("/auth/login", '<form action="/auth/login">Sign in</form>'),
        ("/", '<div id="prompt-textarea" contenteditable="true">Prompt</div>'),
    ],
)
def test_retained_page_classifier_affirms_non_generating_human_surfaces(
    page: Page,
    path: str,
    body: str,
) -> None:
    _set_fixture(page, path, body)

    metadata = page.evaluate(classify_retained_page_source())

    assert metadata == {"state": "human_intervention"}


def test_stop_action_clicks_the_current_generation_control_exactly_once(
    page: Page,
) -> None:
    _set_fixture(
        page,
        "/c/abc123",
        """
        <section data-testid="conversation-turn-1" data-turn="assistant">
          <div data-message-author-role="assistant" data-message-id="assistant-old">Old</div>
          <button id="stale-stop" data-testid="stop-button">Stale stop</button>
        </section>
        <section data-testid="conversation-turn-2" data-turn="assistant">
          <div data-message-author-role="assistant" data-message-id="assistant-latest">
            CANARY-private-generating-response
          </div>
        </section>
        <button id="current-stop" data-testid="stop-button">Stop generating</button>
        """,
    )
    page.evaluate(
        """() => {
          window.stopClicks = {current: 0, stale: 0};
          document.querySelector('#current-stop').addEventListener('click', () => {
            window.stopClicks.current += 1;
          });
          document.querySelector('#stale-stop').addEventListener('click', () => {
            window.stopClicks.stale += 1;
          });
        }"""
    )

    metadata = page.evaluate(request_stop_source())

    assert metadata == {"state": "stop_requested"}
    assert page.evaluate("window.stopClicks") == {"current": 1, "stale": 0}
