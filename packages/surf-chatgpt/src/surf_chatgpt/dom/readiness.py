from __future__ import annotations

import json

from surf_agent.owned_pages import (
    CHATGPT_SESSION_ID_PATTERN,
    OwnedPageClassifier,
    OwnedPageInspectionState,
)


CHALLENGE_SURFACE_SELECTORS = (
    'iframe[src*="challenges.cloudflare.com"]',
    'iframe[src*="hcaptcha.com"]',
    'iframe[src*="recaptcha"]',
    "form#challenge-form",
    'form[action*="/cdn-cgi/challenge-platform/"]',
    '[id^="cf-chl-"]',
    "#challenge-stage",
    "#challenge-running",
    "#challenge-body-text",
    ".cf-turnstile",
    ".h-captcha",
    ".g-recaptcha",
)
COMPOSER_SELECTORS = (
    "textarea#prompt-textarea",
    '#prompt-textarea[contenteditable="true"]',
    '[data-testid="composer-textarea"] textarea',
    '[data-testid="composer-textarea"][contenteditable="true"]',
    'textarea[name="prompt-textarea"]',
    '.ProseMirror[contenteditable="true"]',
    '[contenteditable="true"][data-virtualkeyboard="true"]',
    '[contenteditable="true"]',
)
_STATE_VALUES = {state: json.dumps(state.value) for state in OwnedPageInspectionState}


CURRENT_SESSION_CLASSIFIER = OwnedPageClassifier(
    rf"""() => {{
  const isVisible = (node) => {{
    if (!node) return false;
    const rect = node.getBoundingClientRect?.();
    const style = window.getComputedStyle?.(node);
    return Boolean(
      rect && rect.width > 0 && rect.height > 0 &&
      style?.display !== 'none' && style?.visibility !== 'hidden' &&
      Number(style?.opacity ?? 1) > 0 &&
      !node.closest('[hidden], [aria-hidden="true"], [inert]')
    );
  }};
  if (/^\/c\/{CHATGPT_SESSION_ID_PATTERN}$/.test(location.pathname)) {{
    return {{state: {_STATE_VALUES[OwnedPageInspectionState.SESSION]}}};
  }}
  const challengeSelectors = {json.dumps(CHALLENGE_SURFACE_SELECTORS)};
  if (challengeSelectors.some((selector) =>
    Array.from(document.querySelectorAll(selector)).some(isVisible)
  )) {{
    return {{state: {_STATE_VALUES[OwnedPageInspectionState.HUMAN_GATE]}}};
  }}
  if (location.pathname === '/auth/login' || location.pathname === '/auth/login/') {{
    return {{state: {_STATE_VALUES[OwnedPageInspectionState.PRE_SESSION]}}};
  }}
  const promptSelectors = {json.dumps(COMPOSER_SELECTORS)};
  if (promptSelectors.some((selector) =>
    Array.from(document.querySelectorAll(selector)).some(isVisible)
  )) {{
    return {{state: {_STATE_VALUES[OwnedPageInspectionState.PRE_SESSION]}}};
  }}
  return {{state: {_STATE_VALUES[OwnedPageInspectionState.UNRECOGNIZED]}}};
}}"""
)
