from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
import time
from typing import Any, Protocol

from surf_agent.errors import SurfAgentError
from surf_agent import Thread


class SearchPageKind(StrEnum):
    RESULTS = "results"
    EXHAUSTED = "exhausted"
    HUMAN_INTERVENTION = "human_intervention"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ObservedOrganicResult:
    title: str
    url: str
    snippet: str | None
    displayed_date: str | None


@dataclass(frozen=True)
class SearchPageObservation:
    kind: SearchPageKind
    results: tuple[ObservedOrganicResult, ...] = ()
    next_url: str | None = None


class BrowserPagePort(Protocol):
    def is_open(self, thread: str) -> bool: ...

    def open(self, thread: str, url: str) -> None: ...

    def observe(self, thread: str) -> SearchPageObservation: ...

    def close(self, thread: str) -> None: ...


class BrowserUnavailable(RuntimeError):
    pass


class PageObservationError(ValueError):
    pass


class SurfAgentPort(Protocol):
    def is_open(self) -> bool: ...

    def open(self, url: str) -> str: ...

    def evaluate(self, code: str) -> Any: ...

    def close(self) -> None: ...


AgentFactory = Callable[[str], SurfAgentPort]

# Navigation returns at DOMContentLoaded, but Google streams result cards in
# afterwards (observed 0.3-0.6s later), so an unrecognized page is re-observed
# until it settles instead of being reported as an interface change at once.
SETTLE_TIMEOUT_SECONDS = 10.0
POLL_INTERVAL_SECONDS = 0.25


class SurfBrowserPagePort:
    def __init__(
        self,
        *,
        agent_factory: AgentFactory | None = None,
        settle_timeout_seconds: float = SETTLE_TIMEOUT_SECONDS,
        poll_interval_seconds: float = POLL_INTERVAL_SECONDS,
    ) -> None:
        self._agent_factory = agent_factory or _create_surf_agent
        self._agents: dict[str, SurfAgentPort] = {}
        self._settle_timeout_seconds = settle_timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds

    def is_open(self, thread: str) -> bool:
        try:
            return self._agent(thread).is_open()
        except SurfAgentError as error:
            raise BrowserUnavailable from error

    def open(self, thread: str, url: str) -> None:
        try:
            self._agent(thread).open(url)
        except SurfAgentError as error:
            raise BrowserUnavailable from error

    def observe(self, thread: str) -> SearchPageObservation:
        deadline = time.monotonic() + self._settle_timeout_seconds
        observation = self._observe_once(thread)
        while observation.kind is SearchPageKind.UNKNOWN and time.monotonic() < deadline:
            time.sleep(self._poll_interval_seconds)
            observation = self._observe_once(thread)
        return observation

    def _observe_once(self, thread: str) -> SearchPageObservation:
        try:
            raw = self._agent(thread).evaluate(GOOGLE_PAGE_OBSERVATION_SCRIPT)
        except SurfAgentError as error:
            raise BrowserUnavailable from error
        try:
            return _parse_observation(raw)
        except (TypeError, ValueError, KeyError) as error:
            raise PageObservationError("browser returned an invalid Search page observation") from error

    def close(self, thread: str) -> None:
        agent = self._agents.get(thread)
        if agent is None:
            return
        try:
            agent.close()
        except SurfAgentError as error:
            raise BrowserUnavailable from error

    def _agent(self, thread: str) -> SurfAgentPort:
        if thread not in self._agents:
            self._agents[thread] = self._agent_factory(thread)
        return self._agents[thread]


def selected_surf_profile_path() -> Path:
    from surf_agent import Browser

    return Browser().profile().profile_dir


def _create_surf_agent(thread: str) -> SurfAgentPort:
    return Thread(thread)


def _parse_observation(value: Any) -> SearchPageObservation:
    if not isinstance(value, dict):
        raise TypeError("observation must be an object")
    kind = SearchPageKind(value["kind"])
    raw_results = value.get("results", [])
    if not isinstance(raw_results, list):
        raise TypeError("results must be a list")
    results: list[ObservedOrganicResult] = []
    for raw_result in raw_results:
        if not isinstance(raw_result, dict):
            raise TypeError("result must be an object")
        title = raw_result["title"]
        url = raw_result["url"]
        snippet = raw_result.get("snippet")
        displayed_date = raw_result.get("displayed_date")
        if not isinstance(title, str) or not title or not isinstance(url, str) or not url:
            raise TypeError("result title and URL must be non-empty strings")
        if snippet is not None and not isinstance(snippet, str):
            raise TypeError("snippet must be a string or null")
        if displayed_date is not None and not isinstance(displayed_date, str):
            raise TypeError("displayed date must be a string or null")
        results.append(ObservedOrganicResult(title, url, snippet, displayed_date))
    next_url = value.get("next_url")
    if next_url is not None and not isinstance(next_url, str):
        raise TypeError("next URL must be a string or null")
    if kind is SearchPageKind.RESULTS and not results:
        raise ValueError("results pages must contain at least one result")
    if kind is not SearchPageKind.RESULTS and (results or next_url is not None):
        raise ValueError("non-result pages must not contain results or pagination")
    return SearchPageObservation(kind=kind, results=tuple(results), next_url=next_url)


# Organic results are recognized by likelihood rather than one exact selector:
# each fingerprint below adds weight, so a Google markup change that removes a
# few of them still leaves a result above the threshold, while media, news and
# answer cards (which carry none of the strong ones) stay below it.
GOOGLE_PAGE_OBSERVATION_SCRIPT = r"""
(() => {
  const RESULT_WEIGHTS = {
    h3Title: 3,           // organic titles are <h3>; other cards use role=heading
    ariaHeadingTitle: 1,
    citeInCard: 3,        // the displayed-URL breadcrumb
    hostShownInCard: 2,   // card text shows the destination's host name
    metadataBlock: 2,     // [data-snf] wraps an organic title
    snippetBlock: 2,      // [data-sncf] holds an organic snippet
    resultSlot: 1,        // [data-rpos] numbers every card, organic or not
    mainResults: 1,       // inside #rso / #search
    snippetProse: 1,      // descriptive text beside the title
    moduleCard: -3,       // a card with several titles is a news box or carousel
    adCard: -3,           // paid cards must earn their place with more result fingerprints
    excludedRegion: -100, // answer citations are never results
  };
  const RESULT_THRESHOLD = 6;
  const MIN_SNIPPET_LENGTH = 40;
  const CARD_CLIMB_LIMIT = 8;
  const MODULE_TITLE_COUNT = 3;
  // Ads are dampened, not excluded: a paid card that carries a full result's
  // fingerprints is still a qualified result.
  const AD_REGIONS = '#tads, #tadsb, #bottomads, [data-text-ad]';
  const EXCLUDED_REGIONS = [
    '[data-subtree="aimc"]',                            // AI Overview
    '[data-q]', '.related-question-pair',               // People also ask
    '[data-sncf]',                                      // links cited inside a snippet
  ].join(', ');

  const normalize = value => (value || '').replace(/\s+/g, ' ').trim();
  const hostname = location.hostname.toLowerCase();
  const challenge = hostname === 'consent.google.com'
    || location.pathname.startsWith('/sorry')
    || Boolean(document.querySelector('#captcha-form, input[name="captcha"], iframe[src*="recaptcha"], form[action*="/sorry/"]'));
  if (challenge) return {kind: 'human_intervention', results: [], next_url: null};

  const isGoogleHost = host => /(^|\.)google\.[a-z.]+$/.test(host);
  const isGoogleSearch = (hostname === 'google.com' || hostname === 'www.google.com')
    && location.pathname === '/search';
  const searchShell = document.querySelector('textarea[name="q"], input[name="q"]');
  if (!isGoogleSearch || !searchShell) return {kind: 'unknown', results: [], next_url: null};

  const isVisible = element => element.checkVisibility
    ? element.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})
    : element.getClientRects().length > 0;
  // Every ad click passes through Google's billing redirect, carried in href
  // or in an attribute such as data-rw, so it marks ads the regions miss.
  // Only a parsed Google redirect counts, so pages that merely mention it are
  // not dampened.
  const isAdClickUrl = value => {
    try {
      const url = new URL(value);
      return (isGoogleHost(url.hostname) || /(^|\.)googleadservices\.com$/.test(url.hostname))
        && /^\/(pagead\/)?aclk$/.test(url.pathname);
    } catch {
      return false;
    }
  };
  const isAd = link => Boolean(link.closest(AD_REGIONS))
    || [...link.attributes].some(attribute => isAdClickUrl(attribute.value));
  const destinationHost = link => {
    try {
      const url = new URL(link.href);
      return /^https?:$/.test(url.protocol) && !isGoogleHost(url.hostname) ? url.hostname : null;
    } catch {
      return null;
    }
  };

  const candidates = [];
  for (const link of document.querySelectorAll('a[href]')) {
    const host = destinationHost(link);
    const heading = link.querySelector('h3, [role="heading"]');
    if (!host || !heading || !normalize(heading.innerText) || !isVisible(link)) continue;
    candidates.push({link, heading, host});
  }

  // A card is Google's result slot when marked, otherwise the widest ancestor
  // that holds no other candidate title.
  const cardOf = candidate => {
    const slot = candidate.link.closest('[data-rpos]');
    if (slot) return slot;
    let card = candidate.link;
    for (let depth = 0; depth < CARD_CLIMB_LIMIT; depth++) {
      const parent = card.parentElement;
      if (!parent || parent === document.body
          || candidates.some(other => other !== candidate && parent.contains(other.link))) break;
      card = parent;
    }
    return card;
  };
  const proseBeside = (card, links) => {
    let text = normalize(card.innerText);
    for (const link of links) text = text.replace(normalize(link.innerText), ' ');
    return normalize(text);
  };

  const score = (candidate, card) => {
    const {link, heading, host} = candidate;
    if (link.closest(EXCLUDED_REGIONS)) return RESULT_WEIGHTS.excludedRegion;
    const cardText = normalize(card.innerText).toLowerCase();
    const signals = {
      h3Title: heading.tagName === 'H3',
      ariaHeadingTitle: heading.tagName !== 'H3',
      citeInCard: Boolean(card.querySelector('cite')),
      hostShownInCard: cardText.includes(host.replace(/^www\./, '')),
      metadataBlock: Boolean(link.closest('[data-snf]')),
      snippetBlock: Boolean(card.querySelector('[data-sncf]')),
      resultSlot: card.matches('[data-rpos]'),
      mainResults: Boolean(link.closest('#rso, #search')),
      snippetProse: proseBeside(card, card.querySelectorAll('a')).length >= MIN_SNIPPET_LENGTH,
      moduleCard: candidates.filter(other => card.contains(other.link)).length >= MODULE_TITLE_COUNT,
      adCard: isAd(link),
    };
    return Object.entries(signals)
      .reduce((total, [signal, present]) => total + (present ? RESULT_WEIGHTS[signal] : 0), 0);
  };

  // Keep the single most likely title per card; ties keep document order.
  const bestByCard = new Map();
  for (const candidate of candidates) {
    const card = cardOf(candidate);
    const likelihood = score(candidate, card);
    if (likelihood < RESULT_THRESHOLD) continue;
    const best = bestByCard.get(card);
    if (!best || likelihood > best.likelihood) bestByCard.set(card, {...candidate, likelihood});
  }

  const snippetOf = (card, link) => {
    const snippetNode = card.querySelector('[data-sncf="1"]');
    if (!snippetNode) {
      const prose = proseBeside(card, [link]);
      return {snippet: prose.length >= MIN_SNIPPET_LENGTH ? prose : null, displayedDate: null};
    }
    let snippet = normalize(snippetNode.innerText) || null;
    const dateMarker = [...snippetNode.querySelectorAll('span')].find(node => {
      const ownText = [...node.childNodes]
        .filter(child => child.nodeType === Node.TEXT_NODE)
        .map(child => child.textContent)
        .join('');
      return node.querySelector(':scope > span') && normalize(ownText) === '—';
    });
    const displayedDate = normalize(dateMarker?.querySelector(':scope > span')?.innerText) || null;
    if (displayedDate && snippet?.startsWith(`${displayedDate} —`)) {
      snippet = normalize(snippet.slice(`${displayedDate} —`.length)) || null;
    }
    if (snippet) snippet = normalize(snippet.replace(/\s*Read more\s*$/i, '')) || null;
    return {snippet, displayedDate};
  };

  const results = [...bestByCard.entries()].map(([card, best]) => {
    const {snippet, displayedDate} = snippetOf(card, best.link);
    return {
      title: normalize(best.heading.innerText),
      url: best.link.href,
      snippet,
      displayed_date: displayedDate,
    };
  });

  // The next page is recognized by its address, independent of pager markup
  // and interface language: same query, start advanced by one page.
  const currentParameters = new URLSearchParams(location.search);
  const currentStart = Number(currentParameters.get('start') || 0);
  const pageSize = Number(currentParameters.get('num') || 10);
  const isNextPage = link => {
    try {
      const url = new URL(link.href);
      return isGoogleHost(url.hostname) && url.pathname === '/search'
        && url.searchParams.get('q') === currentParameters.get('q')
        && Number(url.searchParams.get('start')) === currentStart + pageSize;
    } catch {
      return false;
    }
  };
  const next = document.querySelector('a#pnnext, a[aria-label="Next page"]')
    || [...document.querySelectorAll('a[href]')].find(isNextPage);
  const nextUrl = next?.href || null;
  // The top Search tabs also use role=navigation; only the bottom pager owns this table.
  const pagination = [...document.querySelectorAll('div[role="navigation"]')]
    .find(node => node.querySelector('table[role="presentation"]'));
  // Google streams result cards after DOMContentLoaded; the pager arrives with
  // the last of them, and a last page without one is final once loading ends.
  const rendered = nextUrl || pagination || document.readyState === 'complete';
  if (results.length && rendered) {
    return {kind: 'results', results, next_url: nextUrl};
  }
  if (results.length) {
    return {kind: 'unknown', results: [], next_url: null};
  }

  // Generic top/bottom text also hosts corrections and modules; require the no-results card.
  const noResults = document.querySelector(
    '#botstuff .mnr-c .JPMJ2c > p[role="heading"]'
  );
  if (noResults) return {kind: 'exhausted', results: [], next_url: null};
  return {kind: 'unknown', results: [], next_url: null};
})()
""".strip()
