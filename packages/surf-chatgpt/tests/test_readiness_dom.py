from __future__ import annotations

import pytest
from patchright.sync_api import sync_playwright

from surf_chatgpt.dom.readiness import current_session_classifier_source


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            yield browser
        finally:
            browser.close()


@pytest.mark.parametrize("hidden_attribute", ["hidden", 'aria-hidden="true"', "inert"])
def test_current_session_classifier_rejects_hidden_challenge_markers(
    browser, hidden_attribute: str
) -> None:
    page = browser.new_page()
    try:
        page.set_content(
            f'<form id="challenge-form" {hidden_attribute}>Verify you are human</form>'
        )

        metadata = page.evaluate(current_session_classifier_source())

        assert metadata == {"state": "unrecognized"}
    finally:
        page.close()
