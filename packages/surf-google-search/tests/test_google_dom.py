from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from patchright.sync_api import Browser, sync_playwright

from surf_agent.cli import find_chrome_bin
from surf_google_search.browser_port import GOOGLE_PAGE_OBSERVATION_SCRIPT


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    executable = find_chrome_bin()
    assert executable is not None, "Google DOM contract tests require a Chrome-family browser"
    with sync_playwright() as playwright:
        # Headless fixture pages do not need the production profile's Chromium sandbox.
        launched = playwright.chromium.launch(
            executable_path=executable,
            headless=True,
            chromium_sandbox=False,
        )
        yield launched
        launched.close()


def observe_fixture(browser: Browser, body: str) -> dict[str, object]:
    page = browser.new_page()
    page.route(
        "https://www.google.com/search**",
        lambda route: route.fulfill(
            status=200,
            content_type="text/html; charset=utf-8",
            body=f"<textarea name='q'></textarea>{body}",
        ),
    )
    page.goto("https://www.google.com/search?q=fixture&start=0&num=10")
    try:
        return json.loads(page.evaluate(GOOGLE_PAGE_OBSERVATION_SCRIPT))
    finally:
        page.close()


ORGANIC_RESULT = """
<div data-rpos="1">
  <div data-snf><a href="https://example.com/result"><h3>Fixture result</h3></a></div>
</div>
"""


def test_captcha_structure_requires_human_intervention(browser: Browser) -> None:
    observation = observe_fixture(browser, "<form id='captcha-form'></form>")

    assert observation == {
        "kind": "human_intervention",
        "results": [],
        "next_url": None,
    }


def test_unrelated_topstuff_content_does_not_affirm_no_results(browser: Browser) -> None:
    observation = observe_fixture(
        browser,
        "<div id='topstuff'>Showing results for a corrected spelling.</div>",
    )

    assert observation == {"kind": "unknown", "results": [], "next_url": None}


def test_explicit_no_results_structure_is_exhausted(browser: Browser) -> None:
    observation = observe_fixture(
        browser,
        """
        <div id="botstuff">
          <div class="mnr-c"><div class="JPMJ2c">
            <p role="heading">The query did not match any documents.</p>
          </div></div>
        </div>
        """,
    )

    assert observation == {"kind": "exhausted", "results": [], "next_url": None}


def test_primary_organic_extraction_excludes_ads_and_answer_sources(browser: Browser) -> None:
    observation = observe_fixture(
        browser,
        """
        <div data-rpos="1">
          <div data-snf><a href="https://example.com/organic"><h3> Organic   title </h3></a></div>
          <div data-sncf="1"><span><span>Jun 23, 2026</span> —</span> Useful description. Read more</div>
        </div>
        <div data-rpos="2" data-text-ad>
          <div data-snf><a href="https://ads.example/ad"><h3>Sponsored</h3></a></div>
        </div>
        <div data-rpos="3">
          <div data-snf data-sncf><a href="https://ai.example/source"><h3>AI source</h3></a></div>
        </div>
        <a href="https://answer.example/source"><h3>Featured answer source</h3></a>
        <div role="navigation"><table role="presentation"><tr><td>
          <a id="pnnext" href="https://www.google.com/search?q=fixture&amp;start=10">Next</a>
        </td></tr></table></div>
        """,
    )

    assert observation == {
        "kind": "results",
        "results": [
            {
                "title": "Organic title",
                "url": "https://example.com/organic",
                "snippet": "Useful description.",
                "displayed_date": "Jun 23, 2026",
            }
        ],
        "next_url": "https://www.google.com/search?q=fixture&start=10",
    }


def test_terminal_pagination_affirms_exhaustion_after_results(browser: Browser) -> None:
    observation = observe_fixture(
        browser,
        ORGANIC_RESULT
        + """
          <div role="navigation"><table role="presentation"><tr><td>
            <a id="pnprev" href="https://www.google.com/search?q=fixture&amp;start=0">Previous</a>
          </td></tr></table></div>
        """,
    )

    assert observation["kind"] == "results"
    assert observation["next_url"] is None
    assert len(observation["results"]) == 1


def test_multiple_primary_headings_in_one_slot_fail_closed(browser: Browser) -> None:
    observation = observe_fixture(
        browser,
        """
        <div data-rpos="1">
          <div data-snf><a href="https://example.com/one"><h3>One</h3></a></div>
          <div data-snf><a href="https://example.com/two"><h3>Two</h3></a></div>
        </div>
        <div role="navigation"><table role="presentation"><tr><td>
          <a id="pnnext" href="https://www.google.com/search?q=fixture&amp;start=10">Next</a>
        </td></tr></table></div>
        """,
    )

    assert observation == {"kind": "unknown", "results": [], "next_url": None}


def test_page_preserves_more_than_ten_organic_results(browser: Browser) -> None:
    results = "".join(
        f'<div data-rpos="{position}"><div data-snf><a href="https://example.com/{position}"><h3>Result {position}</h3></a></div></div>'
        for position in range(1, 12)
    )
    observation = observe_fixture(
        browser,
        results
        + """
          <div role="navigation"><table role="presentation"><tr><td>
            <a id="pnnext" href="https://www.google.com/search?q=fixture&amp;start=10">Next</a>
          </td></tr></table></div>
        """,
    )

    assert observation["kind"] == "results"
    assert len(observation["results"]) == 11
    assert observation["next_url"] == "https://www.google.com/search?q=fixture&start=10"


def test_results_without_recognized_pagination_fail_closed(browser: Browser) -> None:
    observation = observe_fixture(browser, ORGANIC_RESULT)

    assert observation == {"kind": "unknown", "results": [], "next_url": None}
