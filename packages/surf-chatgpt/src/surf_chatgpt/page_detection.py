from __future__ import annotations

import json


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


def blocking_challenge_detector_js() -> str:
    """Return the shared browser-side detector used by all ChatGPT page flows."""
    selectors = json.dumps(CHALLENGE_SURFACE_SELECTORS)
    return f"""
  const detectBlockingChallenge = () => {{
    const isVisible = (node) => {{
      if (!node) return false;
      const rect = node.getBoundingClientRect?.();
      const style = window.getComputedStyle?.(node);
      return Boolean(
        rect &&
        rect.width > 0 &&
        rect.height > 0 &&
        style?.display !== 'none' &&
        style?.visibility !== 'hidden' &&
        Number(style?.opacity ?? 1) > 0 &&
        !node.parentElement?.closest('[hidden], [aria-hidden="true"], [inert]')
      );
    }};
    for (const selector of {selectors}) {{
      const surface = Array.from(document.querySelectorAll(selector)).find(isVisible);
      if (surface) return {{ present: true, signal: selector }};
    }}
    return {{ present: false, signal: null }};
  }};
""".strip()
