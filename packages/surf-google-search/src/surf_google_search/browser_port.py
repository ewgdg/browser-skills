from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
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


class SurfBrowserPagePort:
    def __init__(self, *, agent_factory: AgentFactory | None = None) -> None:
        self._agent_factory = agent_factory or _create_surf_agent
        self._agents: dict[str, SurfAgentPort] = {}

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
        try:
            raw = self._agent(thread).evaluate(GOOGLE_PAGE_OBSERVATION_SCRIPT)
        except SurfAgentError as error:
            raise BrowserUnavailable from error
        try:
            return _parse_observation(_decode_evaluation(raw))
        except (json.JSONDecodeError, TypeError, ValueError, KeyError) as error:
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
    from surf_agent.cli import SurfAgent

    agent = SurfAgent(thread="surf-google-search-profile")
    if agent.backend == "axi":
        return Path(agent.chrome_profile_dir)
    if agent.backend == "patchright":
        return Path(agent.patchright_profile_dir)
    raise ValueError(f"unsupported Surf backend: {agent.backend}")


def _create_surf_agent(thread: str) -> SurfAgentPort:
    return Thread(thread)


def _decode_evaluation(raw: Any) -> Any:
    if not isinstance(raw, str):
        return raw
    try:
        value = json.loads(raw)
        if not isinstance(value, str):
            return value
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    except json.JSONDecodeError:
        return raw


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


GOOGLE_PAGE_OBSERVATION_SCRIPT = r"""
(() => {
  const normalize = value => (value || '').replace(/\s+/g, ' ').trim();
  const serialize = value => JSON.stringify(value);
  const hostname = location.hostname.toLowerCase();
  const challenge = hostname === 'consent.google.com'
    || location.pathname.startsWith('/sorry')
    || Boolean(document.querySelector('#captcha-form, input[name="captcha"], iframe[src*="recaptcha"], form[action*="/sorry/"]'));
  if (challenge) return serialize({kind: 'human_intervention', results: [], next_url: null});

  const isGoogleSearch = (hostname === 'google.com' || hostname === 'www.google.com')
    && location.pathname === '/search';
  const searchShell = document.querySelector('textarea[name="q"], input[name="q"]');
  if (!isGoogleSearch || !searchShell) return serialize({kind: 'unknown', results: [], next_url: null});

  const candidateGroups = new Map();
  for (const heading of document.querySelectorAll('a h3')) {
    const link = heading.closest('a');
    const container = link?.closest('[data-rpos]');
    if (!link?.href || !container) continue;
    if (link.closest('[data-text-ad], [data-sncf], [data-q], .related-question-pair')) continue;
    const visible = link.checkVisibility
      ? link.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})
      : link.getClientRects().length > 0;
    if (!visible) continue;

    const candidates = candidateGroups.get(container) || [];
    candidates.push({
      heading,
      link,
      metadata: link.closest('[data-snf]'),
    });
    candidateGroups.set(container, candidates);
  }

  const results = [];
  for (const [container, candidates] of candidateGroups) {
    const standardCandidates = candidates.filter(candidate => candidate.metadata);
    if (standardCandidates.length > 1) {
      return serialize({kind: 'unknown', results: [], next_url: null});
    }
    // A multi-link rich module is not one independently positioned result card.
    const candidate = standardCandidates[0] || (candidates.length === 1 ? candidates[0] : null);
    if (!candidate) continue;

    let snippet = null;
    let displayedDate = null;
    if (candidate.metadata) {
      const snippetNode = container.querySelector('[data-sncf="1"]');
      snippet = normalize(snippetNode?.innerText) || null;
      if (snippetNode) {
        const dateMarker = [...snippetNode.querySelectorAll('span')].find(node => {
          const ownText = [...node.childNodes]
            .filter(child => child.nodeType === Node.TEXT_NODE)
            .map(child => child.textContent)
            .join('');
          return node.querySelector(':scope > span') && normalize(ownText) === '—';
        });
        displayedDate = normalize(dateMarker?.querySelector(':scope > span')?.innerText) || null;
        if (displayedDate && snippet.startsWith(`${displayedDate} —`)) {
          snippet = normalize(snippet.slice(`${displayedDate} —`.length)) || null;
        }
        if (snippet) snippet = normalize(snippet.replace(/\s*Read more\s*$/i, '')) || null;
      }
    }

    results.push({
      title: normalize(candidate.heading.innerText),
      url: candidate.link.href,
      snippet,
      displayed_date: displayedDate,
    });
  }

  const next = document.querySelector('a#pnnext, a[aria-label="Next page"]');
  const nextUrl = next?.href || null;
  // The top Search tabs also use role=navigation; only the bottom pager owns this table.
  const pagination = [...document.querySelectorAll('div[role="navigation"]')]
    .find(node => node.querySelector('table[role="presentation"]'));
  if (results.length && (nextUrl || pagination)) {
    return serialize({kind: 'results', results, next_url: nextUrl});
  }
  if (results.length) {
    return serialize({kind: 'unknown', results: [], next_url: null});
  }

  // Generic top/bottom text also hosts corrections and modules; require the no-results card.
  const noResults = document.querySelector(
    '#botstuff .mnr-c .JPMJ2c > p[role="heading"]'
  );
  if (noResults) return serialize({kind: 'exhausted', results: [], next_url: null});
  return serialize({kind: 'unknown', results: [], next_url: null});
})()
""".strip()
