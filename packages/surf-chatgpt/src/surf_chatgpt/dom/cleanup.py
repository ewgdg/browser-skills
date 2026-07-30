from __future__ import annotations

import json

from surf_agent.owned_pages import CHATGPT_SESSION_ID_PATTERN

from .attempt import (
    GATE_SELECTORS,
    STOP_GENERATING_SELECTORS,
    TURN_SELECTOR,
    attempt_helpers_source,
)
from .readiness import COMPOSER_SELECTORS


def classify_retained_page_source() -> str:
    """Build the metadata-only classifier used by cleanup transactions."""
    return rf"""() => {{
  {attempt_helpers_source()}
  if (visibleMatches(document, {json.dumps(GATE_SELECTORS)}).length > 0) {{
    return {{state: 'human_intervention'}};
  }}
  const classification = classifyLatestAttempt();
  if (classification.state !== 'unrecognized') {{
    return {{state: classification.state}};
  }}
  const isSession = /^\/c\/{CHATGPT_SESSION_ID_PATTERN}$/.test(location.pathname);
  if (!isSession &&
      visibleMatches(document, {json.dumps(COMPOSER_SELECTORS)}).length > 0) {{
    return {{state: 'human_intervention'}};
  }}
  return {{state: classification.state}};
}}"""


def request_stop_source() -> str:
    """Build the one-shot action that requests stop for an affirmed generation."""
    return f"""() => {{
  {attempt_helpers_source()}
  if (classifyLatestAttempt().state !== 'generating') {{
    return {{state: 'unrecognized'}};
  }}
  const turns = Array.from(document.querySelectorAll({json.dumps(TURN_SELECTOR)}));
  const latestTurn = turns.at(-1);
  const stopControls = visibleMatches(
    document,
    {json.dumps(STOP_GENERATING_SELECTORS)}
  ).filter((control) => {{
    const owningTurn = control.closest({json.dumps(TURN_SELECTOR)});
    return owningTurn === null || owningTurn === latestTurn;
  }});
  if (stopControls.length !== 1) return {{state: 'unrecognized'}};
  stopControls[0].click();
  return {{state: 'stop_requested'}};
}}"""
