from __future__ import annotations

import pytest
from patchright.sync_api import Browser, Page, sync_playwright
from surf_agent.pacing import NATURAL_PACING_PROFILE

from surf_chatgpt.dom.submission import (
    observe_session_assignment_source,
    prepare_submission_source,
    send_submission_source,
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


def _composer_fixture(*, authenticated: bool = True, extra: str = "") -> str:
    account = (
        '<button aria-label="Open profile menu">Account</button>'
        if authenticated
        else '<a href="/auth/login">Log in</a>'
    )
    return f"""
      <main>
        {account}
        {extra}
        <form>
          <textarea id="prompt-textarea" style="width:20px;height:20px"></textarea>
          <button data-testid="send-button" type="button">Send</button>
        </form>
      </main>
    """


def _set_url_fixture(page: Page, url: str, html: str) -> None:
    page.route(
        "**/*",
        lambda route: route.fulfill(status=200, body=html, content_type="text/html"),
    )
    page.goto(url)


def _picker_fixture(*, affirm_checked_state: bool = True) -> str:
    checked_update = (
        "this.setAttribute('aria-checked', 'true');" if affirm_checked_state else ""
    )
    return _composer_fixture(
        extra=f"""
          <button data-testid="model-switcher-dropdown-button" aria-haspopup="menu">
            Current mode
          </button>
          <div id="top-menu" role="menu" hidden>
            <button role="menuitemradio" aria-checked="false" data-thinking>Instant</button>
            <button role="menuitemradio" aria-checked="false" data-thinking>Pro</button>
            <button id="model-submenu" role="menuitem" aria-haspopup="menu">Models</button>
          </div>
          <div id="model-menu" role="menu" hidden>
            <button role="menuitemradio" aria-checked="false" data-model>GPT-5.6 Sol</button>
            <button role="menuitemradio" aria-checked="false" data-model>GPT-5.6 Terra</button>
          </div>
          <script>
            const topMenu = document.querySelector('#top-menu');
            const modelMenu = document.querySelector('#model-menu');
            document.querySelector('[data-testid="model-switcher-dropdown-button"]')
              .addEventListener('click', () => {{
                topMenu.hidden = false;
                modelMenu.hidden = true;
              }});
            document.querySelector('#model-submenu').addEventListener('click', () => {{
              modelMenu.hidden = false;
            }});
            for (const item of document.querySelectorAll('[data-model], [data-thinking]')) {{
              item.addEventListener('click', function () {{
                {checked_update}
                topMenu.hidden = true;
                modelMenu.hidden = true;
              }});
            }}
          </script>
        """
    )


def _send_fixture(
    *,
    authenticated: bool = True,
    disabled: bool = False,
    include_send: bool = True,
    extra: str = "",
) -> str:
    account = (
        '<button aria-label="Open profile menu">Account</button>'
        if authenticated
        else '<a href="/auth/login">Log in</a>'
    )
    disabled_attribute = "disabled" if disabled else ""
    send_button = (
        f'<button data-testid="send-button" type="button" {disabled_attribute}>Send</button>'
        if include_send
        else ""
    )
    return f"""
      <main>
        {account}
        {extra}
        <form>
          <textarea id="prompt-textarea" style="width:20px;height:20px"></textarea>
          {send_button}
        </form>
      </main>
    """


def _install_send_observer(page: Page, *, enable_on_input: bool = False) -> None:
    page.evaluate(
        """(enableOnInput) => {
          window.submissionEvents = [];
          window.sendEventCounts = {};
          const composer = document.querySelector('#prompt-textarea');
          const sendButton = document.querySelector('[data-testid="send-button"]');
          composer.addEventListener('input', () => {
            window.submissionEvents.push('input');
            if (enableOnInput && sendButton) sendButton.disabled = false;
          });
          if (!sendButton) return;
          for (const eventType of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
            sendButton.addEventListener(eventType, () => {
              window.submissionEvents.push(eventType);
              window.sendEventCounts[eventType] = (window.sendEventCounts[eventType] || 0) + 1;
              if (eventType === 'click') window.promptAtSend = composer.value;
            });
          }
        }""",
        enable_on_input,
    )


def _prepare(page: Page, *, allow_logged_out: bool = False) -> dict[str, object]:
    return page.evaluate(
        prepare_submission_source(
            model_query=None,
            thinking_query=None,
            allow_logged_out=allow_logged_out,
        )
    )


def test_prepare_affirms_visible_authenticated_composer_and_send(page: Page) -> None:
    page.set_content(_composer_fixture())

    assert _prepare(page) == {"state": "ready", "selection": {}}


@pytest.mark.parametrize(
    "hidden_gate",
    [
        '<form id="challenge-form" hidden>Secret challenge</form>',
        '<iframe src="https://challenges.cloudflare.com/widget" aria-hidden="true"></iframe>',
        '<div inert><div class="g-recaptcha">Secret challenge</div></div>',
    ],
)
def test_prepare_ignores_hidden_challenge_markers(page: Page, hidden_gate: str) -> None:
    page.set_content(_composer_fixture(extra=hidden_gate))

    assert _prepare(page) == {"state": "ready", "selection": {}}


def test_prepare_reports_visible_challenge_without_dom_details(page: Page) -> None:
    page.set_content(
        _composer_fixture(
            extra='<form id="challenge-form" style="width:20px;height:20px">Canary challenge</form>'
        )
    )

    assert _prepare(page) == {"state": "challenge"}


def test_prepare_reports_visible_login_page(page: Page) -> None:
    _set_url_fixture(
        page,
        "https://chatgpt.com/auth/login",
        '<main><form action="/auth/login"><button>Log in</button></form></main>',
    )

    assert _prepare(page) == {"state": "login_required"}


def test_prepare_requires_authentication_by_default(page: Page) -> None:
    page.set_content(_composer_fixture(authenticated=False))

    assert _prepare(page) == {"state": "login_required"}


def test_prepare_allows_logged_out_composer_when_explicit(page: Page) -> None:
    page.set_content(_composer_fixture(authenticated=False))

    assert _prepare(page, allow_logged_out=True) == {
        "state": "ready",
        "selection": {},
    }


def test_prepare_rejects_missing_visible_composer_or_send(page: Page) -> None:
    page.set_content(
        '<main><button aria-label="Open profile menu">Account</button></main>'
    )

    assert _prepare(page) == {"state": "ui_changed"}


def test_prepare_selects_model_only_from_nested_model_rows(page: Page) -> None:
    page.set_content(_picker_fixture())

    result = page.evaluate(
        prepare_submission_source(
            model_query="5.6 sol",
            thinking_query=None,
            allow_logged_out=False,
        )
    )

    assert result == {"state": "ready", "selection": {"model": "GPT-5.6 Sol"}}


def test_prepare_selects_thinking_only_from_top_level_modes(page: Page) -> None:
    page.set_content(_picker_fixture())

    result = page.evaluate(
        prepare_submission_source(
            model_query=None,
            thinking_query="pro",
            allow_logged_out=False,
        )
    )

    assert result == {"state": "ready", "selection": {"thinking": "Pro"}}


def test_prepare_selects_both_requested_picker_dimensions(page: Page) -> None:
    page.set_content(_picker_fixture())

    result = page.evaluate(
        prepare_submission_source(
            model_query="terra",
            thinking_query="instant",
            allow_logged_out=False,
        )
    )

    assert result == {
        "state": "ready",
        "selection": {"model": "GPT-5.6 Terra", "thinking": "Instant"},
    }


def test_prepare_does_not_treat_top_level_thinking_mode_as_model(page: Page) -> None:
    page.set_content(_picker_fixture())

    result = page.evaluate(
        prepare_submission_source(
            model_query="pro",
            thinking_query=None,
            allow_logged_out=False,
        )
    )

    assert result == {"state": "model_unavailable"}


def test_prepare_requires_picker_checked_state_to_affirm_selection(page: Page) -> None:
    page.set_content(_picker_fixture(affirm_checked_state=False))

    result = page.evaluate(
        prepare_submission_source(
            model_query=None,
            thinking_query="pro",
            allow_logged_out=False,
        )
    )

    assert result == {"state": "model_unavailable"}


def test_prepare_reports_changed_ui_when_requested_picker_is_missing(
    page: Page,
) -> None:
    page.set_content(_composer_fixture())

    result = page.evaluate(
        prepare_submission_source(
            model_query="sol",
            thinking_query=None,
            allow_logged_out=False,
        )
    )

    assert result == {"state": "ui_changed"}


@pytest.mark.parametrize("dimension", ["model", "thinking"])
def test_prepare_safely_encodes_unavailable_query_without_leaking_it(
    page: Page, dimension: str
) -> None:
    canary_query = 'CANARY-query-79 "; window.exfiltrated = true; //'
    page.set_content(_picker_fixture())

    result = page.evaluate(
        prepare_submission_source(
            model_query=canary_query if dimension == "model" else None,
            thinking_query=canary_query if dimension == "thinking" else None,
            allow_logged_out=False,
        )
    )

    assert result == {"state": "model_unavailable"}
    assert "CANARY" not in str(result)
    assert page.evaluate("window.exfiltrated") is None


def test_send_injects_positional_prompt_and_issues_one_click_sequence(
    page: Page,
) -> None:
    canary_prompt = 'CANARY-secret-42 "; window.exfiltrated = true; // \\ newline\nend'
    page.set_content(_send_fixture())
    _install_send_observer(page)

    result = page.evaluate(
        send_submission_source(
            canary_prompt,
            allow_logged_out=False,
            pace="none",
        )
    )

    assert result == {"state": "submitted"}
    assert "CANARY-secret-42" not in str(result)
    assert page.evaluate("window.promptAtSend") == canary_prompt
    assert page.evaluate("window.exfiltrated") is None
    assert page.evaluate("window.sendEventCounts") == {
        "pointerdown": 1,
        "mousedown": 1,
        "pointerup": 1,
        "mouseup": 1,
        "click": 1,
    }


def test_send_natural_pacing_waits_after_injection_before_click(page: Page) -> None:
    page.set_content(_send_fixture(disabled=True))
    _install_send_observer(page, enable_on_input=True)
    page.evaluate(
        """() => { window.setTimeout = (callback, delay) => {
          window.sampledPaceDelay = delay;
          window.submissionEvents.push('pace');
          callback();
        }; }"""
    )

    result = page.evaluate(
        send_submission_source(
            "paced prompt",
            allow_logged_out=False,
            pace="natural",
        )
    )

    assert result == {"state": "submitted"}
    sampled_seconds = page.evaluate("window.sampledPaceDelay") / 1000
    assert NATURAL_PACING_PROFILE.minimum_seconds <= sampled_seconds
    assert sampled_seconds <= NATURAL_PACING_PROFILE.maximum_seconds
    events = page.evaluate("window.submissionEvents")
    assert events.index("input") < events.index("pace") < events.index("click")


def test_send_none_pacing_never_waits(page: Page) -> None:
    page.set_content(_send_fixture())
    _install_send_observer(page)
    page.evaluate(
        """() => { window.setTimeout = () => {
          window.submissionEvents.push('unexpected-pace');
          throw new Error('none pacing must not wait');
        }; }"""
    )

    result = page.evaluate(
        send_submission_source(
            "immediate prompt",
            allow_logged_out=False,
            pace="none",
        )
    )

    assert result == {"state": "submitted"}
    assert "unexpected-pace" not in page.evaluate("window.submissionEvents")


@pytest.mark.parametrize(
    ("extra", "expected_state"),
    [
        (
            '<form id="challenge-form" style="width:20px;height:20px">Challenge</form>',
            "challenge",
        ),
        ('<a href="/auth/login">Log in</a>', "login_required"),
    ],
)
def test_send_reaffirms_visible_gate_before_injection(
    page: Page, extra: str, expected_state: str
) -> None:
    page.set_content(_send_fixture(extra=extra))
    _install_send_observer(page)

    result = page.evaluate(
        send_submission_source(
            "must not be injected",
            allow_logged_out=False,
            pace="none",
        )
    )

    assert result == {"state": expected_state}
    assert page.locator("#prompt-textarea").input_value() == ""
    assert page.evaluate("window.sendEventCounts") == {}


def test_send_allows_logged_out_composer_when_explicit(page: Page) -> None:
    page.set_content(_send_fixture(authenticated=False))

    result = page.evaluate(
        send_submission_source(
            "anonymous prompt",
            allow_logged_out=True,
            pace="none",
        )
    )

    assert result == {"state": "submitted"}


@pytest.mark.parametrize(
    "fixture",
    [
        _send_fixture(disabled=True),
        _send_fixture(include_send=False),
    ],
    ids=["disabled-send", "missing-send"],
)
def test_send_does_not_click_disabled_or_missing_send(page: Page, fixture: str) -> None:
    page.set_content(fixture)
    _install_send_observer(page)

    result = page.evaluate(
        send_submission_source(
            "unsent prompt",
            allow_logged_out=False,
            pace="none",
        )
    )

    assert result == {"state": "ui_changed"}
    assert page.evaluate("window.sendEventCounts") == {}


def test_assignment_returns_id_only_for_exact_canonical_session_url(page: Page) -> None:
    _set_url_fixture(
        page,
        "https://chatgpt.com/c/Session_abc-123",
        '<main data-message-author-role="assistant">CANARY terminal answer</main>',
    )

    result = page.evaluate(observe_session_assignment_source())

    assert result == {"state": "session", "session_id": "Session_abc-123"}
    assert "CANARY" not in str(result)
    assert "chatgpt.com" not in str(result)


@pytest.mark.parametrize(
    "url",
    [
        "https://chatgpt.com/c/abc123?tracking=CANARY",
        "https://chatgpt.com/c/abc123#CANARY",
        "https://chatgpt.com/share/abc123",
        "https://www.chatgpt.com/c/abc123",
    ],
)
def test_assignment_rejects_noncanonical_routes(page: Page, url: str) -> None:
    _set_url_fixture(page, url, _composer_fixture())

    result = page.evaluate(observe_session_assignment_source())

    assert result == {"state": "ui_changed"}
    assert "CANARY" not in str(result)


def test_assignment_reports_not_ready_before_chatgpt_assigns_route(page: Page) -> None:
    _set_url_fixture(page, "https://chatgpt.com/", _composer_fixture())

    result = page.evaluate(observe_session_assignment_source())

    assert result == {"state": "not_ready"}


def test_terminal_response_dom_does_not_manufacture_assignment(page: Page) -> None:
    _set_url_fixture(
        page,
        "https://chatgpt.com/",
        """
          <main>
            <article data-message-author-role="assistant">CANARY final response</article>
            <button data-testid="copy-turn-action-button">Copy</button>
          </main>
        """,
    )

    result = page.evaluate(observe_session_assignment_source())

    assert result == {"state": "not_ready"}
    assert "CANARY" not in str(result)


def test_assignment_reports_visible_post_send_login_gate(page: Page) -> None:
    _set_url_fixture(
        page,
        "https://chatgpt.com/auth/login",
        '<form action="/auth/login"><button>Log in</button></form>',
    )

    result = page.evaluate(observe_session_assignment_source())

    assert result == {"state": "login_required"}


def test_assignment_reports_visible_post_send_challenge(page: Page) -> None:
    _set_url_fixture(
        page,
        "https://chatgpt.com/",
        '<form id="challenge-form" style="width:20px;height:20px">Challenge</form>',
    )

    result = page.evaluate(observe_session_assignment_source())

    assert result == {"state": "challenge"}


def test_assignment_preserves_known_session_id_when_a_challenge_is_visible(
    page: Page,
) -> None:
    _set_url_fixture(
        page,
        "https://chatgpt.com/c/abc123",
        '<form id="challenge-form" style="width:20px;height:20px">Challenge</form>',
    )

    result = page.evaluate(observe_session_assignment_source())

    assert result == {"state": "challenge", "session_id": "abc123"}


def test_assignment_ignores_hidden_post_send_challenge(page: Page) -> None:
    _set_url_fixture(
        page,
        "https://chatgpt.com/",
        '<form id="challenge-form" hidden>Challenge</form>',
    )

    result = page.evaluate(observe_session_assignment_source())

    assert result == {"state": "not_ready"}
