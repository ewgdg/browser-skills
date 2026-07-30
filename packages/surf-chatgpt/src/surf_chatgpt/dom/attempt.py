from __future__ import annotations

import json

from .readiness import CHALLENGE_SURFACE_SELECTORS


# The data-turn guard excludes nested error markers whose test IDs share the
# conversation-turn prefix.
TURN_SELECTOR = '[data-testid^="conversation-turn-"][data-turn]'
ASSISTANT_MESSAGE_SELECTOR = '[data-message-author-role="assistant"][data-message-id]'
STOP_GENERATING_SELECTORS = (
    'button[data-testid="stop-button"]',
    'button[aria-label="Stop generating"]',
)
COMPLETION_MARKER_SELECTORS = (
    '[data-testid="copy-turn-action-button"]',
    'button[aria-label="Copy response"]',
)
CONTINUE_GENERATING_SELECTORS = (
    'button[data-testid="continue-button"]',
    'button[aria-label="Continue generating"]',
)
FAILURE_MARKER_SELECTORS = (
    '[data-testid="conversation-turn-error"][role="alert"]',
    '[data-testid="response-error"][role="alert"]',
)
RETRY_MARKER_SELECTORS = (
    'button[data-testid="regenerate-button"]',
    'button[data-testid="retry-button"]',
)
GATE_SELECTORS = (
    *CHALLENGE_SURFACE_SELECTORS,
    'form[action*="/auth/login"]',
    '[data-testid*="login"]',
)


def classify_latest_attempt_source() -> str:
    """Build the metadata-only latest-response-attempt classifier."""
    return f"""() => {{
  {_attempt_helpers_source()}
  const classification = classifyLatestAttempt();
  return {{state: classification.state}};
}}"""


def extract_latest_result_source() -> str:
    """Build explicit latest-result extraction for session result only."""
    return f"""() => {{
  {_attempt_helpers_source()}
  const classification = classifyLatestAttempt();
  if (!['completed', 'stopped'].includes(classification.state)) {{
    return {{state: classification.state}};
  }}
  return {{
    state: classification.state,
    text: String(classification.message?.innerText || '').trim()
  }};
}}"""


def _attempt_helpers_source() -> str:
    return f"""
  function isVisible(node) {{
    if (!node || node.closest('[hidden], [aria-hidden="true"], [inert]')) return false;
    const rectangle = node.getBoundingClientRect?.();
    const style = window.getComputedStyle?.(node);
    return Boolean(
      rectangle && rectangle.width > 0 && rectangle.height > 0 &&
      style?.display !== 'none' && style?.visibility !== 'hidden' &&
      Number(style?.opacity ?? 1) > 0
    );
  }}

  function visibleMatches(root, selectors) {{
    return Array.from(new Set(selectors.flatMap((selector) =>
      Array.from(root.querySelectorAll(selector))
    ))).filter(isVisible);
  }}

  function classifyLatestAttempt() {{
    if (visibleMatches(document, {json.dumps(GATE_SELECTORS)}).length > 0) {{
      return {{state: 'unrecognized'}};
    }}
    const turns = Array.from(document.querySelectorAll({json.dumps(TURN_SELECTOR)}));
    const turnIds = turns.map((turn) => turn.getAttribute('data-testid'));
    if (turnIds.some((turnId) => turnId === null) ||
        new Set(turnIds).size !== turnIds.length) {{
      return {{state: 'unrecognized'}};
    }}
    const latestTurn = turns.at(-1);
    if (!latestTurn) return {{state: 'unrecognized'}};
    const stopControls = visibleMatches(
      document,
      {json.dumps(STOP_GENERATING_SELECTORS)}
    ).filter((control) => {{
      const owningTurn = control.closest({json.dumps(TURN_SELECTOR)});
      return owningTurn === null || owningTurn === latestTurn;
    }});
    if (latestTurn.getAttribute('data-turn') === 'user') {{
      return stopControls.length === 1
        ? {{state: 'generating', turn: latestTurn, message: null}}
        : {{state: 'unrecognized'}};
    }}
    const assistantTurns = turns.filter((turn) =>
      turn.getAttribute('data-turn') === 'assistant' ||
      turn.querySelector({json.dumps(ASSISTANT_MESSAGE_SELECTOR)}) !== null
    );
    if (assistantTurns.length === 0 || latestTurn !== assistantTurns.at(-1)) {{
      return {{state: 'unrecognized'}};
    }}
    const latestMessages = latestTurn.querySelectorAll(
      {json.dumps(ASSISTANT_MESSAGE_SELECTOR)}
    );
    if (latestMessages.length > 1) return {{state: 'unrecognized'}};
    const completionMarkers = visibleMatches(
      latestTurn,
      {json.dumps(COMPLETION_MARKER_SELECTORS)}
    );
    const continueControls = visibleMatches(
      latestTurn,
      {json.dumps(CONTINUE_GENERATING_SELECTORS)}
    );
    const failureMarkers = visibleMatches(
      latestTurn,
      {json.dumps(FAILURE_MARKER_SELECTORS)}
    );
    const retryControls = visibleMatches(
      latestTurn,
      {json.dumps(RETRY_MARKER_SELECTORS)}
    );
    if (
      stopControls.length === 1 && completionMarkers.length === 0 &&
      continueControls.length === 0 && failureMarkers.length === 0 &&
      retryControls.length === 0 && latestMessages.length === 1
    ) {{
      return {{state: 'generating', turn: latestTurn, message: latestMessages[0]}};
    }}
    if (
      stopControls.length === 0 && completionMarkers.length <= 1 &&
      continueControls.length === 1 && failureMarkers.length === 0 &&
      retryControls.length === 0 && latestMessages.length === 1
    ) {{
      return {{state: 'stopped', turn: latestTurn, message: latestMessages[0]}};
    }}
    if (
      stopControls.length === 0 && completionMarkers.length === 0 &&
      continueControls.length === 0 && failureMarkers.length === 1 &&
      retryControls.length === 1
    ) {{
      return {{state: 'failed', turn: latestTurn, message: null}};
    }}
    if (
      stopControls.length === 0 && completionMarkers.length === 1 &&
      continueControls.length === 0 && failureMarkers.length === 0 &&
      retryControls.length === 0 && latestMessages.length === 1
    ) {{
      return {{state: 'completed', turn: latestTurn, message: latestMessages[0]}};
    }}
    return {{state: 'unrecognized'}};
  }}
""".strip()
