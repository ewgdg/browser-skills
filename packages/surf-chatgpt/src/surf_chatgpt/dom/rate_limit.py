from __future__ import annotations

import json


RATE_LIMIT_SURFACE_SELECTORS = (
    '[role="alert"]',
    '[role="dialog"]',
    '[data-testid*="toast"]',
    '[data-testid*="error"]',
    '[data-testid*="rate-limit"]',
    '[data-testid*="rate_limit"]',
    "[data-sonner-toast]",
)


def rate_limit_helpers_source() -> str:
    """Build helpers that identify explicit visible rate-limit UI only."""
    return f"""
  const rateLimitSurfaceSelectors = {json.dumps(RATE_LIMIT_SURFACE_SELECTORS)};

  function hasVisibleRateLimit(root = document) {{
    const surfaces = Array.from(new Set(rateLimitSurfaceSelectors.flatMap(
      (selector) => Array.from(root.querySelectorAll(selector))
    )));
    return surfaces.some((surface) => {{
      if (!isVisible(surface) || surface.closest('[data-message-author-role]')) {{
        return false;
      }}
      const text = String(
        surface.getAttribute?.('aria-label') ||
        surface.textContent ||
        surface.innerText ||
        ''
      ).replace(/\\s+/g, ' ').trim();
      return /\\btoo many requests\\b|\\brate[- ]limit(?:ed|ing)?\\b/i.test(text);
    }});
  }}
""".strip()
