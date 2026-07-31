from __future__ import annotations

import json

from surf_agent.pacing import NATURAL_PACING_PROFILE

from surf_chatgpt.dom.readiness import (
    CHALLENGE_SURFACE_SELECTORS,
    COMPOSER_SELECTORS,
)
from surf_chatgpt.dom.rate_limit import rate_limit_helpers_source
from surf_chatgpt.dom.selection import picker_selection_source
from surf_chatgpt.session_address import CHATGPT_SESSION_ID_PATTERN


SEND_SELECTORS = (
    'button[data-testid="send-button"]',
    'button[data-testid*="composer-send"]',
    'form button[type="submit"]',
)
COMPOSER_RECONCILIATION_TIMEOUT_MILLISECONDS = 2_000
_PROVISIONAL_SESSION_ID_PATTERN = r"WEB:[A-Za-z0-9._~-]+"


def prepare_submission_source(
    *,
    model_query: str | None,
    thinking_query: str | None,
    allow_logged_out: bool,
) -> str:
    """Build the metadata-only pre-send readiness and picker program."""
    return f"""async () => {{
  const allowLoggedOut = {json.dumps(allow_logged_out)};
  {_shared_dom_helpers_source()}
  {picker_selection_source(model_query=model_query, thinking_query=thinking_query)}
  const readiness = readinessState(allowLoggedOut);
  if (readiness) return {{state: readiness}};
  const picker = await selectRequestedDimensions();
  if (picker.state) return {{state: picker.state}};
  const finalReadiness = readinessState(allowLoggedOut);
  if (finalReadiness) return {{state: finalReadiness}};
  return {{state: 'ready', selection: picker.selection}};
}}"""


def send_submission_source(
    prompt: str,
    *,
    allow_logged_out: bool,
    pace: str = "natural",
) -> str:
    """Build the guarded prompt-injection and single-send program."""
    pace_value = str(pace)
    if pace_value not in {"natural", "none"}:
        raise ValueError(f"unsupported pacing profile: {pace_value}")
    minimum_delay_ms = NATURAL_PACING_PROFILE.minimum_seconds * 1000
    maximum_delay_ms = NATURAL_PACING_PROFILE.maximum_seconds * 1000
    return f"""async () => {{
  const prompt = {json.dumps(prompt)};
  const allowLoggedOut = {json.dumps(allow_logged_out)};
  const pace = {json.dumps(pace_value)};
  const naturalPacingBounds = {{
    minimumMilliseconds: {json.dumps(minimum_delay_ms)},
    maximumMilliseconds: {json.dumps(maximum_delay_ms)}
  }};
  const composerReconciliationTimeoutMilliseconds = {
      json.dumps(COMPOSER_RECONCILIATION_TIMEOUT_MILLISECONDS)
  };
  {_shared_dom_helpers_source()}

  function isDisabled(node) {{
    return node.hasAttribute('disabled') || node.getAttribute('aria-disabled') === 'true' ||
      node.getAttribute('data-disabled') === 'true';
  }}

  function settleComposerMutation() {{
    return new Promise((resolve) => requestAnimationFrame(() => resolve()));
  }}

  function postFillGateState() {{
    if (hasVisibleRateLimit()) return 'rate_limited';
    if (hasVisibleChallenge()) return 'challenge';
    const onLoginRoute = /^\\/auth\\/login\\/?$/.test(location.pathname);
    if (!allowLoggedOut && (onLoginRoute || hasVisibleLoginSurface())) {{
      return 'login_required';
    }}
    return null;
  }}

  async function waitForReconciledDispatch() {{
    const deadline = performance.now() + composerReconciliationTimeoutMilliseconds;
    while (true) {{
      const gate = postFillGateState();
      if (gate) return gate;
      const composer = firstVisible(composerSelectors);
      const send = firstVisible(sendSelectors);
      const authenticated = allowLoggedOut || hasVisibleAccountControl();
      if (authenticated && composer && composerText(composer) === prompt &&
          send && !isDisabled(send)) {{
        return 'ready';
      }}
      if (performance.now() >= deadline) return 'ui_changed';
      await settleComposerMutation();
    }}
  }}

  async function applyPacing() {{
    if (pace === 'none') return;
    const range = naturalPacingBounds.maximumMilliseconds -
      naturalPacingBounds.minimumMilliseconds;
    const duration = naturalPacingBounds.minimumMilliseconds + Math.random() * range;
    await new Promise((resolve) => setTimeout(resolve, duration));
  }}

  const reconciliation = await waitForReconciledDispatch();
  if (reconciliation !== 'ready') {{
    return {{state: reconciliation}};
  }}
  await applyPacing();
  const finalGate = postFillGateState();
  if (finalGate) {{
    return {{state: finalGate}};
  }}
  const finalComposer = firstVisible(composerSelectors);
  const finalSend = firstVisible(sendSelectors);
  if ((!allowLoggedOut && !hasVisibleAccountControl()) ||
      !finalComposer || composerText(finalComposer) !== prompt ||
      !finalSend || isDisabled(finalSend)) {{
    return {{state: 'ui_changed'}};
  }}
  return {{state: 'submitted'}};
}}"""


def observe_session_assignment_source() -> str:
    """Build the one-shot canonical-session assignment classifier."""
    return f"""() => {{
  {_shared_dom_helpers_source()}
  const canonicalPath = location.pathname.match(
    /^\\/c\\/({CHATGPT_SESSION_ID_PATTERN})$/
  );
  const isProvisionalSession = /^\\/c\\/{_PROVISIONAL_SESSION_ID_PATTERN}$/.test(
    location.pathname
  );
  const isCanonical = location.protocol === 'https:' &&
    location.hostname === 'chatgpt.com' && location.port === '' &&
    location.search === '' && location.hash === '' && canonicalPath !== null;
  const sessionId = isCanonical ? canonicalPath[1] : null;
  function gateMetadata(state) {{
    return sessionId === null ? {{state}} : {{state, session_id: sessionId}};
  }}
  if (hasVisibleRateLimit()) return gateMetadata('rate_limited');
  if (hasVisibleChallenge()) return gateMetadata('challenge');
  if (hasVisibleLoginSurface()) return gateMetadata('login_required');
  if (sessionId !== null) return {{state: 'session', session_id: sessionId}};
  const isPendingAssignmentRoute = location.protocol === 'https:' &&
    location.hostname === 'chatgpt.com' && location.port === '' &&
    (location.pathname === '/' || isProvisionalSession || canonicalPath !== null);
  if (isPendingAssignmentRoute) return {{state: 'not_ready'}};
  return {{state: 'ui_changed'}};
}}"""


def _shared_dom_helpers_source() -> str:
    return f"""
  const challengeSelectors = {json.dumps(CHALLENGE_SURFACE_SELECTORS)};
  const composerSelectors = {json.dumps(COMPOSER_SELECTORS)};
  const sendSelectors = {json.dumps(SEND_SELECTORS)};

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

  function firstVisible(selectors) {{
    for (const selector of selectors) {{
      const match = Array.from(document.querySelectorAll(selector)).find(isVisible);
      if (match) return match;
    }}
    return null;
  }}

  function composerText(node) {{
    return 'value' in node ? node.value : (node.textContent || '');
  }}

  function textOf(node) {{
    return String(
      node?.getAttribute?.('aria-label') || node?.textContent || node?.innerText || ''
    ).replace(/\\s+/g, ' ').trim();
  }}

  {rate_limit_helpers_source()}

  function dispatchClickSequence(target) {{
    for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {{
      const common = {{bubbles: true, cancelable: true, view: window}};
      const event = type.startsWith('pointer') && 'PointerEvent' in window
        ? new PointerEvent(type, {{...common, pointerId: 1, pointerType: 'mouse'}})
        : new MouseEvent(type, common);
      target.dispatchEvent(event);
    }}
  }}

  function hasVisibleChallenge() {{
    return Boolean(firstVisible(challengeSelectors));
  }}

  function hasVisibleLoginSurface() {{
    const explicitSurface = firstVisible([
      'form[action*="/auth/login"]',
      '[data-testid*="login"]',
      'a[href*="/auth/login"]'
    ]);
    const labelledControl = Array.from(
      document.querySelectorAll('button, a, [role="button"], [role="link"]')
    ).some((node) => isVisible(node) && /^(log in|sign in)$/i.test(textOf(node)));
    return Boolean(explicitSurface || labelledControl);
  }}

  function hasVisibleAccountControl() {{
    return Array.from(
      document.querySelectorAll('button, a, [role="button"], [role="link"]')
    ).some((node) => {{
      if (!isVisible(node)) return false;
      const evidence = [
        textOf(node),
        node.getAttribute?.('aria-label'),
        node.getAttribute?.('data-testid')
      ].join(' ').toLowerCase();
      return /profile|account|settings|customize chatgpt|my plan/.test(evidence);
    }});
  }}

  function readinessState(allowLoggedOut) {{
    if (hasVisibleRateLimit()) return 'rate_limited';
    if (hasVisibleChallenge()) return 'challenge';
    const onLoginRoute = /^\\/auth\\/login\\/?$/.test(location.pathname);
    if (onLoginRoute && hasVisibleLoginSurface()) return 'login_required';
    if (!allowLoggedOut && (!hasVisibleAccountControl() || hasVisibleLoginSurface())) {{
      return 'login_required';
    }}
    const composer = firstVisible(composerSelectors);
    if (!composer || !firstVisible(sendSelectors) || composerText(composer) !== '') {{
      return 'ui_changed';
    }}
    return null;
  }}
""".strip()
