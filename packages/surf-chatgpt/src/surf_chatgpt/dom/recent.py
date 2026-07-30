from __future__ import annotations

import json

from .readiness import CHALLENGE_SURFACE_SELECTORS


LOGIN_SURFACE_SELECTORS = (
    'form[action*="/auth/login"]',
    '[data-testid*="login"]',
)
RECENT_SESSION_LIMIT = 10


def discover_recent_sessions_source() -> str:
    """Build explicit recent-session title extraction for session recent only."""
    source = r"""() => {
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
  const chatsLabels = Array.from(
    history.querySelectorAll('h1, h2, h3, h4, h5, h6, [role="heading"], div, span, p')
  ).filter((label) =>
    isVisible(label) && !label.closest('a, button') && directText(label) === 'Chats'
  );
  if (chatsLabels.length !== 1) return {state: 'ui_changed'};
  const label = chatsLabels[0];
  const semanticSection = label.closest('section, [role="region"]');
  let section = semanticSection && semanticSection !== history
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
  if (!section) return {state: 'ui_changed'};
  const seen = new Set();
  const sessions = [];
  for (const link of section.querySelectorAll('a[href]')) {
    if (!isVisible(link)) continue;
    const url = new URL(link.href, location.origin);
    const match = url.origin === 'https://chatgpt.com' && !url.search && !url.hash
      ? url.pathname.match(/^\/c\/([A-Za-z0-9_-]+)$/)
      : null;
    const id = match?.[1];
    const title = String(link.innerText || '').trim();
    if (!id || !title || seen.has(id)) continue;
    seen.add(id);
    sessions.push({id, title});
    if (sessions.length === __RECENT_SESSION_LIMIT__) break;
  }
  return {state: 'sessions', sessions};
}"""
    return (
        source.replace(
            "__CHALLENGE_SELECTORS__",
            json.dumps(CHALLENGE_SURFACE_SELECTORS),
        )
        .replace("__LOGIN_SELECTORS__", json.dumps(LOGIN_SURFACE_SELECTORS))
        .replace(
            "__RECENT_SESSION_LIMIT__",
            str(RECENT_SESSION_LIMIT),
        )
    )
