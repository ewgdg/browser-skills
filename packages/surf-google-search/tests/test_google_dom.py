from __future__ import annotations

import shlex
from collections.abc import Iterator

import pytest
from patchright.sync_api import Browser, sync_playwright

from surf_agent.runtime import find_chrome_bin
from surf_google_search.browser_port import GOOGLE_PAGE_OBSERVATION_SCRIPT


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    command = find_chrome_bin()
    assert command is not None, "Google DOM contract tests require a Chrome-family browser"
    executable = shlex.split(command)[0]
    with sync_playwright() as playwright:
        # Headless fixture pages do not need the production profile's Chromium sandbox.
        launched = playwright.chromium.launch(
            executable_path=executable,
            headless=True,
            chromium_sandbox=False,
        )
        yield launched
        launched.close()


STALLED_SCRIPT = "https://www.google.com/stalled.js"


def observe_fixture(browser: Browser, body: str, *, still_loading: bool = False) -> dict[str, object]:
    page = browser.new_page()
    if still_loading:
        # A parser-blocking script that never arrives keeps the document loading,
        # as Google's streamed response does before its final chunk.
        body += f"<script src='{STALLED_SCRIPT}'></script>"
        page.route(STALLED_SCRIPT, lambda route: None)
    page.route(
        "https://www.google.com/search**",
        lambda route: route.fulfill(
            status=200,
            content_type="text/html; charset=utf-8",
            body=f"<textarea name='q'></textarea>{body}",
        ),
    )
    page.goto(
        "https://www.google.com/search?q=fixture&start=0&num=10",
        wait_until="commit" if still_loading else "load",
    )
    try:
        if still_loading:
            page.wait_for_selector("a", state="attached")
        return page.evaluate(GOOGLE_PAGE_OBSERVATION_SCRIPT)
    finally:
        page.close()


def organic_card(
    position: int,
    url: str,
    title: str,
    *,
    title_tag: str = "h3",
    cite: bool = True,
    data_attributes: bool = True,
    snippet: str = "A description long enough to read as result prose.",
) -> str:
    """A result card shaped like live Google, with optional fingerprints removed."""
    host = url.split("/")[2]
    heading = (
        f"<{title_tag}>{title}</{title_tag}>" if title_tag == "h3"
        else f"<div role='heading' aria-level='3'>{title}</div>"
    )
    displayed_url = f"<cite>https://{host}</cite>" if cite else f"<span>https://{host}</span>"
    slot = f' data-rpos="{position}"' if data_attributes else ""
    metadata = " data-snf" if data_attributes else ""
    snippet_marker = ' data-sncf="1"' if data_attributes else ""
    return (
        f"<div{slot}><div{metadata}><a href='{url}'>{heading}<br>{displayed_url}</a></div>"
        f"<div{snippet_marker}>{snippet}</div></div>"
    )


PAGER = """
<div role="navigation"><table role="presentation"><tr><td>
  <a id="pnnext" href="https://www.google.com/search?q=fixture&amp;start=10">Next</a>
</td></tr></table></div>
"""
ORGANIC_RESULT = organic_card(1, "https://example.com/result", "Fixture result")


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


def test_primary_organic_extraction_excludes_answer_sources(browser: Browser) -> None:
    observation = observe_fixture(
        browser,
        """
        <div data-rpos="1">
          <div data-snf><a href="https://example.com/organic"><h3> Organic   title </h3><cite>https://example.com</cite></a></div>
          <div data-sncf="1"><span><span>Jun 23, 2026</span> —</span> Useful description. Read more</div>
        </div>
        <div data-rpos="3">
          <div data-snf data-sncf><a href="https://ai.example/source"><h3>AI source</h3></a></div>
        </div>
        <a href="https://answer.example/source"><h3>Featured answer source</h3></a>
        <div data-rpos="4">
          <a href="https://module.example/one"><h3>Module card one</h3></a>
          <a href="https://module.example/two"><h3>Module card two</h3></a>
        </div>
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


def test_visible_top_level_rich_result_excludes_nested_answer_sources(
    browser: Browser,
) -> None:
    observation = observe_fixture(
        browser,
        """
        <div data-rpos="10">
          <div data-q="Does Elon Musk have children?">
            <a style="display:none" href="https://www.instyle.com/hidden-source">
              <h3>Hidden answer source</h3>
            </a>
            <a href="https://nchstats.com/nested-source">
              <h3>Nested answer source</h3>
            </a>
          </div>
        </div>
        <div data-rpos="16">
          <div><a href="https://www.youtube.com/watch?v=cnb-uNTo28w">
            <h3>Elon Musk Family Tree</h3><cite>https://www.youtube.com › watch</cite>
          </a></div>
        </div>
        <div role="navigation"><table role="presentation"><tr><td>
          <a id="pnnext" href="https://www.google.com/search?q=fixture&amp;start=10">Next</a>
        </td></tr></table></div>
        """,
    )

    assert observation == {
        "kind": "results",
        "results": [
            {
                "title": "Elon Musk Family Tree",
                "url": "https://www.youtube.com/watch?v=cnb-uNTo28w",
                "snippet": None,
                "displayed_date": None,
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


def test_most_likely_title_in_one_card_wins(browser: Browser) -> None:
    observation = observe_fixture(
        browser,
        """
        <div data-rpos="1">
          <div data-snf><a href="https://example.com/main"><h3>Main</h3><cite>https://example.com</cite></a></div>
          <div data-sncf="1">Main result description long enough to read as prose.</div>
          <div data-snf><a href="https://example.com/section"><h3>Section link</h3></a></div>
        </div>
        """ + PAGER,
    )

    assert observation["kind"] == "results"
    assert [result["url"] for result in observation["results"]] == ["https://example.com/main"]


def test_page_preserves_more_than_ten_organic_results(browser: Browser) -> None:
    results = "".join(
        organic_card(position, f"https://example.com/{position}", f"Result {position}")
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


def test_results_still_streaming_without_pagination_are_not_final(browser: Browser) -> None:
    observation = observe_fixture(browser, ORGANIC_RESULT, still_loading=True)

    assert observation == {"kind": "unknown", "results": [], "next_url": None}


def test_fully_loaded_last_page_without_pagination_is_final(browser: Browser) -> None:
    observation = observe_fixture(browser, ORGANIC_RESULT)

    assert observation["kind"] == "results"
    assert observation["next_url"] is None


@pytest.mark.parametrize(
    "fingerprints",
    [
        pytest.param({"title_tag": "heading", "cite": False}, id="title-tag-and-cite-changed"),
        pytest.param({"data_attributes": False}, id="data-attributes-renamed"),
        pytest.param({"data_attributes": False, "cite": False}, id="data-attributes-and-cite-changed"),
    ],
)
def test_organic_result_survives_partial_markup_changes(browser: Browser, fingerprints) -> None:
    card = organic_card(1, "https://example.com/result", "Fixture result", **fingerprints)

    observation = observe_fixture(browser, card + PAGER)

    assert observation["kind"] == "results"
    assert [result["url"] for result in observation["results"]] == ["https://example.com/result"]
    assert observation["results"][0]["title"] == "Fixture result"


def test_organic_result_with_most_fingerprints_gone_is_not_guessed(browser: Browser) -> None:
    card = organic_card(
        1, "https://example.com/result", "Fixture result",
        title_tag="heading", cite=False, data_attributes=False,
    )

    observation = observe_fixture(browser, card + PAGER)

    assert observation == {"kind": "unknown", "results": [], "next_url": None}


def test_media_and_news_cards_are_not_organic_results(browser: Browser) -> None:
    video = """
    <div data-rpos="2">
      <a href="https://www.youtube.com/watch?v=abc"><div role="heading" aria-level="3">Video title</div></a>
      <span>YouTube · Channel</span>
    </div>
    """

    observation = observe_fixture(browser, ORGANIC_RESULT + video + PAGER)

    assert [result["url"] for result in observation["results"]] == ["https://example.com/result"]


AD_REGION = "<div id='tads'>{}</div>"
AD_REDIRECT = "data-rw='https://www.google.com/aclk?sa=L'"


def test_ad_with_a_full_result_card_is_a_result(browser: Browser) -> None:
    # Ads are dampened, not excluded: a paid card with a full result's title,
    # displayed URL and snippet still qualifies.
    ad = (
        f"<div data-text-ad='1'><a href='https://quality-ad.example/guide' {AD_REDIRECT}>"
        "<h3>Complete buyer guide</h3><cite>https://quality-ad.example</cite></a>"
        "<div>A thorough description long enough to read as result prose.</div></div>"
    )

    observation = observe_fixture(browser, AD_REGION.format(ad) + f"<div id='rso'>{ORGANIC_RESULT}</div>{PAGER}")

    assert [result["url"] for result in observation["results"]] == [
        "https://quality-ad.example/guide",
        "https://example.com/result",
    ]


@pytest.mark.parametrize(
    ("region", "link_attributes"),
    [
        pytest.param(AD_REGION, "", id="ad-region"),
        pytest.param("<div>{}</div>", AD_REDIRECT, id="click-redirect"),
    ],
)
def test_ad_needs_more_result_fingerprints_than_an_organic_card(
    browser: Browser, region, link_attributes
) -> None:
    # This card shape (h3 title, host shown, snippet prose, no cite or result
    # metadata) passes as organic but falls short once it is an ad.
    def card(url: str, attributes: str = "") -> str:
        host = url.split("/")[2]
        return (
            f"<div><a href='{url}' {attributes}><h3>Offer from {host}</h3></a>"
            f"<span>{host}</span><div>A description long enough to read as result prose.</div></div>"
        )

    ad = region.format(card("https://ad.example/offer", link_attributes))
    organic = card("https://organic.example/page")

    observation = observe_fixture(browser, f"{ad}{organic}{PAGER}")

    assert [result["url"] for result in observation["results"]] == ["https://organic.example/page"]


def test_next_page_is_found_by_its_search_address(browser: Browser) -> None:
    pager = """
    <div><a href="https://www.google.com/search?q=fixture&amp;start=0">1</a>
    <a href="https://www.google.com/search?q=fixture&amp;start=10">2</a>
    <a href="https://www.google.com/search?q=fixture&amp;start=20">3</a></div>
    """

    observation = observe_fixture(browser, ORGANIC_RESULT + pager)

    assert observation["next_url"] == "https://www.google.com/search?q=fixture&start=10"


def test_news_module_headlines_are_not_organic_results(browser: Browser) -> None:
    # Top stories labels each headline with its source, which can match the host.
    headlines = "".join(
        f"<a href='https://{source}/story'><div role='heading' aria-level='3'>"
        f"Headline from {source}</div></a><span>{source}</span><span>1 hour ago</span>"
        for source in ("cbc.ca", "news.yahoo.com", "people.com")
    )
    module = f"<div data-rpos='2'><span>Top stories</span><span>Customize</span>{headlines}</div>"

    observation = observe_fixture(browser, f"<div id='rso'>{ORGANIC_RESULT}{module}</div>{PAGER}")

    assert [result["url"] for result in observation["results"]] == ["https://example.com/result"]
