from __future__ import annotations

import pytest
from patchright.sync_api import Browser, Page, sync_playwright

from surf_chatgpt.dom.attempt import (
    classify_latest_attempt_source,
    extract_latest_result_source,
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


def _set_session_fixture(page: Page, body: str) -> None:
    page.route(
        "**/*",
        lambda route: route.fulfill(
            status=200,
            body=f"<main>{body}</main>",
            content_type="text/html",
        ),
    )
    page.goto("https://chatgpt.com/c/abc123")


def test_classifier_affirms_latest_assistant_generation_without_content(
    page: Page,
) -> None:
    _set_session_fixture(
        page,
        """
        <section data-testid="conversation-turn-1" data-turn="user">
          <div data-message-author-role="user" data-message-id="user-1">
            CANARY-private-prompt
          </div>
        </section>
        <section data-testid="conversation-turn-2" data-turn="assistant">
          <div data-message-author-role="assistant" data-message-id="assistant-1">
            CANARY-private-generating-response
          </div>
        </section>
        <button data-testid="stop-button">Stop generating</button>
        <aside>CANARY-private-sidebar</aside>
        """,
    )
    page.evaluate("document.title = 'CANARY-private-title'")

    metadata = page.evaluate(classify_latest_attempt_source())

    assert metadata == {"state": "generating"}
    assert "CANARY" not in str(metadata)


def test_classifier_affirms_latest_assistant_completion_without_content(
    page: Page,
) -> None:
    _set_session_fixture(
        page,
        """
        <section data-testid="conversation-turn-1" data-turn="user">
          <div data-message-author-role="user" data-message-id="user-1">Question</div>
        </section>
        <section data-testid="conversation-turn-2" data-turn="assistant">
          <div data-message-author-role="assistant" data-message-id="assistant-1">
            CANARY-private-completed-response
          </div>
          <button data-testid="copy-turn-action-button" aria-label="Copy response">Copy</button>
        </section>
        """,
    )

    metadata = page.evaluate(classify_latest_attempt_source())

    assert metadata == {"state": "completed"}
    assert "CANARY" not in str(metadata)


def test_classifier_affirms_explicitly_stopped_latest_response_without_content(
    page: Page,
) -> None:
    _set_session_fixture(
        page,
        """
        <section data-testid="conversation-turn-1" data-turn="user">
          <div data-message-author-role="user" data-message-id="user-1">Question</div>
        </section>
        <section data-testid="conversation-turn-2" data-turn="assistant">
          <div data-message-author-role="assistant" data-message-id="assistant-1">
            CANARY-private-partial-response
          </div>
          <button data-testid="copy-turn-action-button" aria-label="Copy response">Copy</button>
          <button data-testid="continue-button" aria-label="Continue generating">Continue</button>
        </section>
        """,
    )

    metadata = page.evaluate(classify_latest_attempt_source())

    assert metadata == {"state": "stopped"}
    assert "CANARY" not in str(metadata)


def test_classifier_affirms_latest_response_failure_without_stale_content(
    page: Page,
) -> None:
    _set_session_fixture(
        page,
        """
        <section data-testid="conversation-turn-1" data-turn="assistant">
          <div data-message-author-role="assistant" data-message-id="assistant-old">
            CANARY-stale-response-must-not-become-a-result
          </div>
          <button data-testid="copy-turn-action-button" aria-label="Copy response">Copy</button>
        </section>
        <section data-testid="conversation-turn-2" data-turn="assistant">
          <div data-testid="conversation-turn-error" role="alert">Generation failed</div>
          <button data-testid="regenerate-button">Retry</button>
        </section>
        """,
    )

    metadata = page.evaluate(classify_latest_attempt_source())

    assert metadata == {"state": "failed"}
    assert "CANARY" not in str(metadata)


def test_classifier_affirms_explicit_rate_limited_latest_attempt(page: Page) -> None:
    _set_session_fixture(
        page,
        """
        <section data-testid="conversation-turn-1" data-turn="user">
          <div data-message-author-role="user" data-message-id="user-1">Question</div>
        </section>
        <section data-testid="conversation-turn-2" data-turn="assistant">
          <div data-testid="conversation-turn-error" role="alert">
            Too many requests. Please try again later.
          </div>
          <button data-testid="regenerate-button">Retry</button>
        </section>
        """,
    )

    metadata = page.evaluate(classify_latest_attempt_source())

    assert metadata == {"state": "rate_limited"}


def test_classifier_affirms_visible_rate_limit_before_a_turn_exists(page: Page) -> None:
    _set_session_fixture(
        page,
        """
        <div data-testid="request-error" role="alert">
          Too many requests. Please try again later.
        </div>
        """,
    )

    assert page.evaluate(classify_latest_attempt_source()) == {
        "state": "rate_limited"
    }


def test_classifier_does_not_infer_rate_limit_from_authored_content(page: Page) -> None:
    _set_session_fixture(
        page,
        """
        <section data-testid="conversation-turn-1" data-turn="user">
          <div data-message-author-role="user" data-message-id="user-1">
            Explain the phrase too many requests.
          </div>
        </section>
        <section data-testid="conversation-turn-2" data-turn="assistant">
          <div data-message-author-role="assistant" data-message-id="assistant-1">
            Too many requests is an HTTP rate-limit message.
          </div>
          <button data-testid="copy-turn-action-button" aria-label="Copy response">Copy</button>
        </section>
        """,
    )

    assert page.evaluate(classify_latest_attempt_source()) == {"state": "completed"}


def test_explicit_result_extraction_returns_only_the_completed_latest_response(
    page: Page,
) -> None:
    _set_session_fixture(
        page,
        """
        <section data-testid="conversation-turn-1" data-turn="assistant">
          <div data-message-author-role="assistant" data-message-id="assistant-old">
            CANARY-stale-response
          </div>
          <button data-testid="copy-turn-action-button" aria-label="Copy response">Copy</button>
        </section>
        <section data-testid="conversation-turn-2" data-turn="assistant">
          <div data-message-author-role="assistant" data-message-id="assistant-latest">
            Final answer
          </div>
          <button data-testid="copy-turn-action-button" aria-label="Copy response">Copy</button>
        </section>
        """,
    )

    result = page.evaluate(extract_latest_result_source())

    assert result == {"state": "completed", "text": "Final answer"}
    assert "CANARY" not in str(result)


@pytest.mark.parametrize(
    ("latest_body", "expected"),
    [
        (
            """
            <div data-message-author-role="assistant" data-message-id="assistant-latest">
              Partial answer
            </div>
            <button data-testid="continue-button" aria-label="Continue generating">Continue</button>
            """,
            {"state": "stopped", "text": "Partial answer"},
        ),
        (
            """
            <div data-testid="conversation-turn-error" role="alert">Generation failed</div>
            <button data-testid="regenerate-button">Retry</button>
            """,
            {"state": "failed"},
        ),
        (
            """
            <div data-message-author-role="assistant" data-message-id="assistant-latest"></div>
            <button data-testid="copy-turn-action-button" aria-label="Copy response">Copy</button>
            """,
            {"state": "completed", "text": ""},
        ),
        (
            """
            <div data-message-author-role="assistant" data-message-id="assistant-latest">
              <p>I cannot help with that.</p><ul><li>Safe alternative</li></ul>
            </div>
            <button data-testid="copy-turn-action-button" aria-label="Copy response">Copy</button>
            """,
            {
                "state": "completed",
                "text": "I cannot help with that.\n\nSafe alternative",
            },
        ),
    ],
)
def test_explicit_result_extraction_handles_terminal_result_shapes(
    page: Page,
    latest_body: str,
    expected: dict[str, str],
) -> None:
    _set_session_fixture(
        page,
        f"""
        <section data-testid="conversation-turn-1" data-turn="assistant">
          <div data-message-author-role="assistant" data-message-id="assistant-old">
            CANARY-stale-response
          </div>
          <button data-testid="copy-turn-action-button" aria-label="Copy response">Copy</button>
        </section>
        <section data-testid="conversation-turn-2" data-turn="assistant">
          {latest_body}
        </section>
        """,
    )

    result = page.evaluate(extract_latest_result_source())

    assert result == expected
    assert "CANARY" not in str(result)


def test_classifier_ignores_stale_stop_control_inside_an_old_turn(page: Page) -> None:
    _set_session_fixture(
        page,
        """
        <section data-testid="conversation-turn-1" data-turn="assistant">
          <div data-message-author-role="assistant" data-message-id="assistant-old">Old</div>
          <button data-testid="stop-button">Stale stop</button>
        </section>
        <section data-testid="conversation-turn-2" data-turn="assistant">
          <div data-message-author-role="assistant" data-message-id="assistant-latest">Latest</div>
          <button data-testid="copy-turn-action-button" aria-label="Copy response">Copy</button>
        </section>
        """,
    )

    assert page.evaluate(classify_latest_attempt_source()) == {"state": "completed"}


def test_classifier_affirms_generation_before_the_assistant_turn_is_created(
    page: Page,
) -> None:
    _set_session_fixture(
        page,
        """
        <section data-testid="conversation-turn-1" data-turn="assistant">
          <div data-message-author-role="assistant" data-message-id="assistant-old">Old</div>
          <button data-testid="copy-turn-action-button" aria-label="Copy response">Copy</button>
        </section>
        <section data-testid="conversation-turn-2" data-turn="user">
          <div data-message-author-role="user" data-message-id="user-latest">New question</div>
        </section>
        <button data-testid="stop-button">Stop generating</button>
        """,
    )

    assert page.evaluate(classify_latest_attempt_source()) == {"state": "generating"}


@pytest.mark.parametrize(
    "body",
    [
        """
        <section data-testid="conversation-turn-1" data-turn="assistant">
          <div data-message-author-role="assistant" data-message-id="assistant-1">Text</div>
          <button data-testid="stop-button">Stop</button>
          <button data-testid="copy-turn-action-button" aria-label="Copy response">Copy</button>
        </section>
        """,
        """
        <section data-testid="conversation-turn-1" data-turn="assistant">
          <div data-message-author-role="assistant" data-message-id="assistant-1">One</div>
          <div data-message-author-role="assistant" data-message-id="assistant-2">Two</div>
          <button data-testid="copy-turn-action-button" aria-label="Copy response">Copy</button>
        </section>
        """,
        """
        <section data-testid="conversation-turn-1" data-turn="assistant">
          <div data-message-author-role="assistant" data-message-id="assistant-1">Old</div>
          <button data-testid="copy-turn-action-button" aria-label="Copy response">Copy</button>
        </section>
        <section data-testid="conversation-turn-2" data-turn="user">
          <div data-message-author-role="user" data-message-id="user-2">New question</div>
        </section>
        """,
        """
        <aside>completed stopped failed generating CANARY-state-like-text</aside>
        <section data-testid="conversation-turn-1" data-turn="assistant" aria-busy="true">
          <div data-testid="conversation-turn-loading">Loading</div>
        </section>
        """,
        """
        <form id="challenge-form" style="width:20px;height:20px">Verify</form>
        <section data-testid="conversation-turn-1" data-turn="assistant">
          <div data-message-author-role="assistant" data-message-id="assistant-1">Old</div>
          <button data-testid="copy-turn-action-button" aria-label="Copy response">Copy</button>
        </section>
        """,
        """
        <section data-testid="conversation-turn-1" data-turn="assistant">
          <div data-message-author-role="assistant" data-message-id="assistant-1">One</div>
          <button data-testid="copy-turn-action-button" aria-label="Copy response">Copy</button>
        </section>
        <section data-testid="conversation-turn-1" data-turn="assistant">
          <div data-message-author-role="assistant" data-message-id="assistant-2">Two</div>
          <button data-testid="copy-turn-action-button" aria-label="Copy response">Copy</button>
        </section>
        """,
    ],
)
def test_classifier_fails_closed_for_unaffirmed_or_ambiguous_latest_attempts(
    page: Page,
    body: str,
) -> None:
    _set_session_fixture(page, body)

    metadata = page.evaluate(classify_latest_attempt_source())

    assert metadata == {"state": "unrecognized"}
    assert "CANARY" not in str(metadata)
