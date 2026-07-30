from __future__ import annotations

import pytest
from patchright.sync_api import Browser, Page, sync_playwright

from surf_chatgpt.dom.recent import discover_recent_sessions_source


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


def _set_history_fixture(
    page: Page,
    body: str,
    *,
    url: str = "https://chatgpt.com/",
) -> None:
    page.route(
        "**/*",
        lambda route: route.fulfill(
            status=200,
            body=body,
            content_type="text/html",
        ),
    )
    page.goto(url)


def test_discovery_returns_visible_canonical_chats_in_displayed_order(
    page: Page,
) -> None:
    _set_history_fixture(
        page,
        """
        <nav aria-label="Chat history">
          <section aria-labelledby="pinned-heading">
            <h2 id="pinned-heading">Pinned</h2>
            <a href="/c/pinned">Pinned conversation</a>
          </section>
          <section aria-labelledby="chats-heading">
            <h2 id="chats-heading">Chats</h2>
            <ol>
              <li><a href="/c/first">First visible title</a></li>
              <li><a href="https://chatgpt.com/c/second">Second visible title</a></li>
            </ol>
          </section>
        </nav>
        """,
    )

    result = page.evaluate(discover_recent_sessions_source())

    assert result == {
        "state": "sessions",
        "sessions": [
            {"id": "first", "title": "First visible title"},
            {"id": "second", "title": "Second visible title"},
        ],
    }


def test_discovery_supports_a_rendered_label_followed_by_its_chat_list(
    page: Page,
) -> None:
    _set_history_fixture(
        page,
        """
        <nav aria-label="Chat history">
          <div data-testid="pinned-group">
            <div><span>Pinned</span></div>
            <ol><li><a href="/c/pinned">Pinned title</a></li></ol>
          </div>
          <div data-testid="chats-group">
            <div><span>Chats</span></div>
            <ol aria-label="Chats">
              <li><a href="/c/first">First visible title</a></li>
              <li><a href="/c/second">Second visible title</a></li>
            </ol>
          </div>
          <div data-testid="projects-group">
            <div><span>Projects</span></div>
            <a href="/c/project">Project title</a>
          </div>
        </nav>
        """,
    )

    result = page.evaluate(discover_recent_sessions_source())

    assert result == {
        "state": "sessions",
        "sessions": [
            {"id": "first", "title": "First visible title"},
            {"id": "second", "title": "Second visible title"},
        ],
    }


def test_discovery_waits_for_initial_document_hydration(page: Page) -> None:
    _set_history_fixture(page, '<main id="application"></main>')
    page.evaluate(
        """() => {
          Object.defineProperty(document, 'readyState', {
            value: 'interactive',
            configurable: true,
          });
          setTimeout(() => {
            document.querySelector('#application').innerHTML = `
              <nav aria-label="Chat history">
                <section aria-labelledby="chats-heading">
                  <h2 id="chats-heading">Chats</h2>
                  <ol><li><a href="/c/hydrated">Hydrated title</a></li></ol>
                </section>
              </nav>
            `;
          }, 50);
        }"""
    )

    result = page.evaluate(discover_recent_sessions_source())

    assert result == {
        "state": "sessions",
        "sessions": [{"id": "hydrated", "title": "Hydrated title"}],
    }


def test_discovery_supports_one_exclusive_ungrouped_chat_history_nav(
    page: Page,
) -> None:
    _set_history_fixture(
        page,
        """
        <nav aria-label="Chat history">
          <ol>
            <li><a href="/c/first">First visible title</a></li>
            <li><a href="/c/second">Second visible title</a></li>
          </ol>
        </nav>
        <a href="/settings">Settings</a>
        """,
    )

    result = page.evaluate(discover_recent_sessions_source())

    assert result == {
        "state": "sessions",
        "sessions": [
            {"id": "first", "title": "First visible title"},
            {"id": "second", "title": "Second visible title"},
        ],
    }


def test_discovery_rejects_an_ungrouped_history_with_canonical_links_outside_it(
    page: Page,
) -> None:
    _set_history_fixture(
        page,
        """
        <nav aria-label="Chat history">
          <a href="/c/inside">Inside title</a>
        </nav>
        <section><a href="/c/outside">Outside title</a></section>
        """,
    )

    result = page.evaluate(discover_recent_sessions_source())

    assert result == {"state": "ui_changed"}


def test_discovery_bounds_unique_candidates_and_excludes_non_chat_links(
    page: Page,
) -> None:
    _set_history_fixture(
        page,
        """
        <nav aria-label="Chat history">
          <section><h2>Pinned</h2><a href="/c/pinned">Pinned</a></section>
          <section><h2>Projects</h2><a href="/c/project">Project chat</a></section>
          <section aria-labelledby="chats-heading">
            <h2 id="chats-heading">Chats</h2>
            <a href="/c/one"><span>One</span><span hidden>CANARY-hidden-title</span></a>
            <a href="/c/one">Duplicate one</a>
            <a href="/c/two">Two</a>
            <a href="/c/three">Three</a>
            <a href="/c/four">Four</a>
            <a href="/c/five">Five</a>
            <a href="/c/six">Six</a>
            <a href="/c/seven">Seven</a>
            <a href="/c/eight">Eight</a>
            <a href="/c/nine">Nine</a>
            <a href="/c/ten">Ten</a>
            <a href="/c/eleven">Eleven</a>
            <a href="/c/query?share=1">Query</a>
            <a href="/c/fragment#latest">Fragment</a>
            <a href="https://example.com/c/external">External</a>
            <a href="http://[">Malformed</a>
            <a href="/c/hidden" hidden>Hidden</a>
            <a href="/c/no-title"><span hidden>No visible title</span></a>
            <a href="/g/gpt-id/c/not-canonical">Custom GPT</a>
          </section>
          <section><h2>Archived</h2><a href="/c/archived">Archived</a></section>
          <a href="/c/outside">Outside every section</a>
        </nav>
        """,
    )

    result = page.evaluate(discover_recent_sessions_source())

    assert result == {
        "state": "sessions",
        "sessions": [
            {"id": "one", "title": "One"},
            {"id": "two", "title": "Two"},
            {"id": "three", "title": "Three"},
            {"id": "four", "title": "Four"},
            {"id": "five", "title": "Five"},
            {"id": "six", "title": "Six"},
            {"id": "seven", "title": "Seven"},
            {"id": "eight", "title": "Eight"},
            {"id": "nine", "title": "Nine"},
            {"id": "ten", "title": "Ten"},
        ],
    }
    assert "CANARY" not in str(result)


@pytest.mark.parametrize(
    "body",
    [
        "<main>No chat history</main>",
        """
        <nav aria-label="Chat history">
          <section><h2>Chats</h2></section>
        </nav>
        <nav aria-label="Chat history">
          <section><h2>Chats</h2></section>
        </nav>
        """,
        """
        <nav aria-label="Chat history">
          <section><h2>Chats</h2></section>
          <section><h2>Chats</h2></section>
        </nav>
        """,
        """
        <nav aria-label="Chat history">
          <h2>Chats</h2>
          <a href="/c/unscoped">Unscoped conversation</a>
        </nav>
        """,
    ],
)
def test_discovery_fails_closed_for_missing_or_ambiguous_chats_ui(
    page: Page,
    body: str,
) -> None:
    _set_history_fixture(page, body)

    result = page.evaluate(discover_recent_sessions_source())

    assert result == {"state": "ui_changed"}


def test_discovery_affirms_an_empty_chats_section(page: Page) -> None:
    _set_history_fixture(
        page,
        """
        <nav aria-label="Chat history">
          <section aria-labelledby="chats-heading">
            <h2 id="chats-heading">Chats</h2>
            <ol aria-label="Chats"></ol>
          </section>
        </nav>
        """,
    )

    result = page.evaluate(discover_recent_sessions_source())

    assert result == {"state": "sessions", "sessions": []}


@pytest.mark.parametrize(
    ("url", "body", "expected_state"),
    [
        (
            "https://chatgpt.com/auth/login",
            '<main><form action="/auth/login">Sign in</form></main>',
            "login_required",
        ),
        (
            "https://chatgpt.com/",
            '<main><form id="challenge-form">Verify you are human</form></main>',
            "challenge",
        ),
    ],
)
def test_discovery_reports_human_gates_without_reading_titles(
    page: Page,
    url: str,
    body: str,
    expected_state: str,
) -> None:
    _set_history_fixture(page, body, url=url)

    result = page.evaluate(discover_recent_sessions_source())

    assert result == {"state": expected_state}


def test_discovery_ignores_hidden_challenge_markers(page: Page) -> None:
    _set_history_fixture(
        page,
        '<main><form id="challenge-form" hidden>CANARY-private-gate</form></main>',
    )

    result = page.evaluate(discover_recent_sessions_source())

    assert result == {"state": "ui_changed"}
    assert "CANARY" not in str(result)
