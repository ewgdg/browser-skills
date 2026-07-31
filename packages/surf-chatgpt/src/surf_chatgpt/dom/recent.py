from __future__ import annotations

import json

from .readiness import CHALLENGE_SURFACE_SELECTORS
from ..session_address import CHATGPT_SESSION_ID_PATTERN


LOGIN_SURFACE_SELECTORS = (
    'form[action*="/auth/login"]',
    '[data-testid*="login"]',
)
RECENT_SESSION_LIMIT = 10
RECENT_SESSION_HYDRATION_TIMEOUT_MILLISECONDS = 5_000
RECENT_SESSION_HYDRATION_POLL_MILLISECONDS = 100


def discover_recent_sessions_source() -> str:
    """Build explicit recent-session title extraction for session recent only."""
    source = r"""async () => {
  const isVisible = (node) => {
    if (!node || node.closest('[hidden], [aria-hidden="true"], [inert]')) return false;
    const rectangle = node.getBoundingClientRect?.();
    const style = window.getComputedStyle?.(node);
    return Boolean(
      rectangle && rectangle.width > 0 && rectangle.height > 0 &&
      style?.display !== 'none' && style?.visibility !== 'hidden' &&
      Number(style?.opacity ?? 1) > 0
    );
  };
  const visibleMatches = (selectors) => selectors.flatMap((selector) =>
    Array.from(document.querySelectorAll(selector)).filter(isVisible)
  );
  const classify = () => {
    if (visibleMatches(__CHALLENGE_SELECTORS__).length > 0) {
      return {state: 'challenge'};
    }
    if (
      location.pathname === '/auth/login' ||
      location.pathname === '/auth/login/' ||
      visibleMatches(__LOGIN_SELECTORS__).length > 0
    ) {
      return {state: 'login_required'};
    }
    const histories = Array.from(
      document.querySelectorAll('nav[aria-label="Chat history"]')
    ).filter(isVisible);
    if (histories.length !== 1) return {state: 'ui_changed'};
    const history = histories[0];
    const directText = (node) => Array.from(node.childNodes)
      .filter((child) => child.nodeType === Node.TEXT_NODE)
      .map((child) => String(child.textContent || ''))
      .join(' ')
      .replace(/\s+/g, ' ')
      .trim();
    const canonicalSessionId = (link) => {
      if (!isVisible(link)) return null;
      const url = new URL(link.href, location.origin);
      const match = url.origin === 'https://chatgpt.com' && !url.search && !url.hash
        ? url.pathname.match(/^\/c\/(__SESSION_ID_PATTERN__)$/)
        : null;
      return match?.[1] || null;
    };
    const chatsLabels = Array.from(
      history.querySelectorAll('h1, h2, h3, h4, h5, h6, [role="heading"], div, span, p')
    ).filter((label) =>
      isVisible(label) && !label.closest('a, button') && directText(label) === 'Chats'
    );
    if (chatsLabels.length > 1) return {state: 'ui_changed'};
    let section = null;
    if (chatsLabels.length === 1) {
      const label = chatsLabels[0];
      const semanticSection = label.closest('section, [role="region"]');
      section = semanticSection && semanticSection !== history
        ? semanticSection
        : null;
      if (!section) {
        let branch = label;
        while (branch.parentElement && branch.parentElement !== history) {
          const sibling = branch.nextElementSibling;
          if (sibling && isVisible(sibling)) {
            const identifiesList = sibling.matches(
              'ol, ul, [role="list"], [aria-label="Chats"]'
            ) || sibling.querySelector('a[href*="/c/"]') !== null;
            if (identifiesList) {
              section = sibling;
              break;
            }
          }
          branch = branch.parentElement;
        }
      }
    } else {
      const groupedLabels = Array.from(
        history.querySelectorAll('h1, h2, h3, h4, h5, h6, [role="heading"], div, span, p')
      ).filter((label) =>
        isVisible(label) && !label.closest('a, button') &&
        ['Pinned', 'Projects'].includes(directText(label))
      );
      const allCanonicalLinks = Array.from(document.querySelectorAll('a[href]'))
        .filter((link) => canonicalSessionId(link) !== null);
      const historyCanonicalLinks = allCanonicalLinks
        .filter((link) => history.contains(link));
      if (
        groupedLabels.length === 0 &&
        historyCanonicalLinks.length > 0 &&
        historyCanonicalLinks.length === allCanonicalLinks.length
      ) {
        section = history;
      }
    }
    if (!section) return {state: 'ui_changed'};
    const seen = new Set();
    const sessions = [];
    for (const link of section.querySelectorAll('a[href]')) {
      const id = canonicalSessionId(link);
      const title = String(link.innerText || '').trim();
      if (!id || !title || seen.has(id)) continue;
      seen.add(id);
      sessions.push({id, title});
      if (sessions.length === __RECENT_SESSION_LIMIT__) break;
    }
    return {state: 'sessions', sessions};
  };
  const startedBeforeDocumentCompletion = document.readyState !== 'complete';
  let result = classify();
  if (!startedBeforeDocumentCompletion || result.state !== 'ui_changed') return result;
  const deadline = performance.now() + __HYDRATION_TIMEOUT_MILLISECONDS__;
  while (performance.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, __HYDRATION_POLL_MILLISECONDS__));
    result = classify();
    if (result.state !== 'ui_changed') return result;
  }
  return result;
}"""
    return (
        source.replace(
            "__CHALLENGE_SELECTORS__",
            json.dumps(CHALLENGE_SURFACE_SELECTORS),
        )
        .replace("__LOGIN_SELECTORS__", json.dumps(LOGIN_SURFACE_SELECTORS))
        .replace("__SESSION_ID_PATTERN__", CHATGPT_SESSION_ID_PATTERN)
        .replace(
            "__RECENT_SESSION_LIMIT__",
            str(RECENT_SESSION_LIMIT),
        )
        .replace(
            "__HYDRATION_TIMEOUT_MILLISECONDS__",
            str(RECENT_SESSION_HYDRATION_TIMEOUT_MILLISECONDS),
        )
        .replace(
            "__HYDRATION_POLL_MILLISECONDS__",
            str(RECENT_SESSION_HYDRATION_POLL_MILLISECONDS),
        )
    )
