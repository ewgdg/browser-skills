from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import urlparse
import re

from .errors import SkillError
from .extract import clean_response
from .surf import SurfRunner

CHATGPT_HOME = "https://chatgpt.com/"
CHATGPT_HOSTS = {"chatgpt.com", "www.chatgpt.com"}

PROMPT_SELECTORS = [
    "#prompt-textarea",
    '[data-testid="composer-textarea"]',
    'textarea[name="prompt-textarea"]',
    ".ProseMirror",
    '[contenteditable="true"][data-virtualkeyboard="true"]',
    '[contenteditable="true"]',
]
SEND_SELECTORS = [
    'button[data-testid="send-button"]',
    'button[data-testid*="composer-send"]',
    'form button[type="submit"]',
]
ASSISTANT_SELECTORS = (
    '[data-message-author-role="assistant"], [data-turn="assistant"], '
    '[data-testid*="assistant-message"], [data-testid*="assistant-turn"], [data-testid*="assistant-response"]'
)
ASSISTANT_CONTENT_SELECTORS = [".markdown", "[data-message-content]", ".prose", '[class*="markdown"]', '[dir="auto"]']
CONVERSATION_TURN_SELECTOR = '[data-testid^="conversation-turn"], [data-testid*="conversation-turn"]'
STOP_SELECTOR = '[data-testid="stop-button"], [data-testid*="stop"], button[aria-label*="Stop"], button[aria-label*="stop"]'
FINISHED_SELECTOR = (
    'button[data-testid="copy-turn-action-button"], button[data-testid="good-response-turn-action-button"], '
    'button[data-testid*="turn-action"], button[aria-label*="Copy"], button[aria-label*="copy"], '
    'button[aria-label*="Read aloud"], button[aria-label*="read aloud"]'
)


@dataclass(frozen=True)
class ReusableAskOptions:
    session_policy: str
    session_url: str | None = None
    window_id: int | None = None
    keep_open: bool = False
    model_query: str | None = None
    start_new: bool = False
    timeout: int = 2700
    thinking_label: str | None = None


def ask_reusable_session(
    prompt: str,
    options: ReusableAskOptions,
    *,
    surf: SurfRunner | None = None,
) -> dict[str, Any]:
    """Ask ChatGPT through controlled browser automation.

    Ephemeral requests use a temporary browser window that is closed in `finally`.
    Persistent continuity is explicit: callers pass a ChatGPT `/c/<id>` URL or id.
    """
    runner = surf or SurfRunner()
    started_at = time.time()
    target: BrowserTarget | None = None

    try:
        target = _resolve_target(runner, options)
        _wait_load_best_effort(runner, target)
        _assert_chatgpt_ready(runner, target)
        selection = _select_model_choice(runner, target, options.model_query, options.thinking_label) if (options.model_query or options.thinking_label) else {"model": "current", "thinking": None}

        baseline = _read_snapshot(runner, target)
        _inject_prompt(runner, target, prompt)
        _send_prompt(runner, target)
        response = _wait_for_response(runner, target, baseline, timeout_seconds=options.timeout)
        warnings: list[str] = []
        try:
            url = _current_url(runner, target)
        except SkillError as exc:
            # Do not discard a completed answer just because ChatGPT/Surf hangs while reading
            # the final conversation URL. This happens after send/response on heavy pages.
            warnings.append(f"session_url_unavailable:{exc.type}")
            url = options.session_url or _tab_url_best_effort(runner, target) or CHATGPT_HOME

        session_id = _session_id_from_url(url)

        return {
            "response": response.text,
            "model": selection.get("model") or "current",
            "thinking": selection.get("thinking"),
            "messageId": response.message_id,
            "tookMs": int((time.time() - started_at) * 1000),
            "warnings": warnings,
            "session": {
                "policy": options.session_policy,
                "id": session_id,
                "url": url,
                "reused": target.reused,
                "tab_id": target.tab_id,
                "window_id": target.window_id,
                "saved": False,
            },
        }
    finally:
        if target and target.close_after:
            _close_target_best_effort(runner, target)


@dataclass(frozen=True)
class BrowserTarget:
    tab_id: int
    window_id: int | None
    reused: bool
    close_after: bool = False
    scope: str = "tab"

    @property
    def command_id(self) -> int:
        if self.scope == "window":
            if self.window_id is None:
                raise SkillError("parse_error", "browser target missing window id")
            return self.window_id
        return self.tab_id


@dataclass(frozen=True)
class AssistantResponse:
    text: str
    message_id: str | None = None


def _resolve_target(runner: SurfRunner, options: ReusableAskOptions) -> BrowserTarget:
    if options.session_policy == "ephemeral":
        return _open_ephemeral_chatgpt_target(runner)
    if options.session_policy == "current":
        return _active_chatgpt_tab(runner)
    if options.session_policy == "window":
        if options.window_id is None:
            raise SkillError("invalid_args", "--window-id is required for window session mode")
        return _existing_window_chatgpt_target(runner, options.window_id)
    if options.start_new or options.session_policy == "new":
        return _open_chatgpt_url(runner, CHATGPT_HOME, reused=False, close_after=not options.keep_open)
    if options.session_url:
        return _open_chatgpt_url(runner, options.session_url, reused=True, close_after=not options.keep_open)
    raise SkillError("invalid_args", "browser session mode requires --new, --session ID_OR_URL, --window-id, or --current")


def _open_ephemeral_chatgpt_target(runner: SurfRunner) -> BrowserTarget:
    target = _open_background_window(runner, reused=False, close_after=True)
    _navigate_window(runner, target, CHATGPT_HOME)
    return target


def _open_chatgpt_url(runner: SurfRunner, url: str, *, reused: bool, close_after: bool) -> BrowserTarget:
    target = _open_background_window(runner, reused=reused, close_after=close_after)
    _navigate_window(runner, target, url)
    return target


def _existing_window_chatgpt_target(runner: SurfRunner, window_id: int) -> BrowserTarget:
    matches = [tab for tab in _list_tabs(runner) if _coerce_int(tab.get("windowId") or tab.get("window_id")) == window_id]
    if not matches:
        raise SkillError("invalid_args", f"no surf window found for --window-id {window_id}")
    if len(matches) > 1:
        raise SkillError("invalid_args", f"--window-id {window_id} has multiple tabs; expected one ChatGPT tab")
    tab = matches[0]
    url = str(tab.get("url", ""))
    if not _is_chatgpt_url(url):
        raise SkillError("invalid_args", f"--window-id {window_id} is not a ChatGPT window")
    tab_id = _coerce_int(tab.get("id") or tab.get("tabId") or tab.get("tab_id"))
    if tab_id is None:
        raise SkillError("parse_error", "ChatGPT window tab is missing id")
    return BrowserTarget(tab_id=tab_id, window_id=window_id, reused=True, close_after=False, scope="window")


def _open_background_window(runner: SurfRunner, *, reused: bool, close_after: bool) -> BrowserTarget:
    # No URL: surf opens its neutral surf-agent page first, letting window rules keep it unfocused.
    data = runner.run_json(["window.new"], timeout=30)
    tab_id = _extract_int(data, "tabId", "tab_id", "id", "_resolvedTabId")
    window_id = _extract_int(data, "windowId", "window_id")
    if tab_id is None:
        raise SkillError("parse_error", "surf window.new JSON missing tab id")
    if window_id is None:
        raise SkillError("parse_error", "surf window.new JSON missing window id")
    return BrowserTarget(tab_id=tab_id, window_id=window_id, reused=reused, close_after=close_after, scope="window")


def _navigate_window(runner: SurfRunner, target: BrowserTarget, url: str) -> None:
    if target.window_id is None:
        raise SkillError("parse_error", "browser target missing window id")
    runner.run_json_on_window(target.window_id, ["navigate", url], timeout=30)


def _close_target_best_effort(runner: SurfRunner, target: BrowserTarget) -> None:
    try:
        if target.window_id is not None:
            runner.run_json(["window.close", str(target.window_id)], timeout=10)
        else:
            runner.run_json(["tab.close", str(target.tab_id)], timeout=10)
    except SkillError:
        # Preserve primary answer/error; cleanup failure is not useful to caller.
        pass


def _active_chatgpt_tab(runner: SurfRunner) -> BrowserTarget:
    tabs = _list_tabs(runner)
    matches = [tab for tab in tabs if bool(tab.get("active")) and _is_chatgpt_url(str(tab.get("url", "")))]
    if not matches:
        raise SkillError("invalid_args", "no active ChatGPT tab found")
    if len(matches) > 1:
        raise SkillError("invalid_args", "multiple active ChatGPT tabs found")
    tab = matches[0]
    tab_id = _coerce_int(tab.get("id") or tab.get("tabId") or tab.get("tab_id"))
    if tab_id is None:
        raise SkillError("parse_error", "active ChatGPT tab is missing id")
    return BrowserTarget(
        tab_id=tab_id,
        window_id=_coerce_int(tab.get("windowId") or tab.get("window_id")),
        reused=True,
    )


def _find_tab_by_id(runner: SurfRunner, tab_id: int) -> dict[str, Any] | None:
    for tab in _list_tabs(runner):
        candidate = _coerce_int(tab.get("id") or tab.get("tabId") or tab.get("tab_id"))
        if candidate == tab_id:
            return tab
    return None


def _list_tabs(runner: SurfRunner) -> list[dict[str, Any]]:
    return _as_tab_list(runner.run_json(["tab.list"], timeout=10))


def _as_tab_list(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("tabs", "items", "result"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        nested = data.get("result")
        if isinstance(nested, dict):
            return _as_tab_list(nested)
    return []


def _wait_load_best_effort(runner: SurfRunner, target: BrowserTarget) -> None:
    try:
        _run_json_on_target(runner, target, ["wait.load", "--timeout", "30000"], timeout=35)
    except SkillError as exc:
        if exc.type not in {"timeout", "ui_changed"}:
            raise


def _assert_chatgpt_ready(runner: SurfRunner, target: BrowserTarget) -> None:
    status = _run_js_file(runner, target, _status_js(), timeout=15)
    if not isinstance(status, dict):
        raise SkillError("parse_error", "ChatGPT status script returned unexpected data")
    if status.get("challenge"):
        raise SkillError("captcha_or_cloudflare", "ChatGPT challenge detected")
    if status.get("loginRequired"):
        raise SkillError("login_required", "ChatGPT login required")
    if not status.get("hasPrompt"):
        raise SkillError("ui_changed", "ChatGPT prompt composer not found")


def _select_model_choice(runner: SurfRunner, target: BrowserTarget, model_query: str | None, thinking_label: str | None) -> dict[str, Any]:
    selected_thinking = None
    if thinking_label and not model_query:
        result = _run_js_file(runner, target, _select_thinking_level_js(thinking_label), timeout=15)
        if not isinstance(result, dict):
            raise SkillError("parse_error", "ChatGPT thinking selection script returned unexpected data")
        if not result.get("ok"):
            _raise_model_selection_error(result, None, thinking_label)
        return {"model": "current", "thinking": result.get("selectedThinking") or thinking_label}

    result = _run_js_file(runner, target, _select_model_choice_js(model_query, thinking_label), timeout=30)
    if not isinstance(result, dict):
        raise SkillError("parse_error", "ChatGPT model selection script returned unexpected data")
    if result.get("ok"):
        selected_thinking = result.get("selectedThinking") or thinking_label
        return {
            "model": result.get("selectedModel") or model_query or "current",
            "thinking": selected_thinking,
        }
    _raise_model_selection_error(result, model_query, thinking_label)
    raise AssertionError("unreachable")


def _raise_model_selection_error(result: dict[str, Any], model_query: str | None, thinking_label: str | None) -> None:
    available = result.get("available")
    suffix = f" Available: {', '.join(available)}" if isinstance(available, list) and available else ""
    reason = result.get("reason") or "model unavailable"
    if reason in {"model_button_missing", "menu_missing", "model_selector_missing"}:
        raise SkillError("ui_changed", f"ChatGPT model menu unavailable: {reason}")
    if reason == "thinking_missing":
        raise SkillError("model_unavailable", f"ChatGPT thinking level {thinking_label!r} unavailable.{suffix}")
    raise SkillError("model_unavailable", f"ChatGPT model {model_query!r} unavailable.{suffix}")


def _inject_prompt(runner: SurfRunner, target: BrowserTarget, prompt: str) -> None:
    result = _run_js_file(runner, target, _inject_prompt_js(prompt), timeout=30)
    if not isinstance(result, dict) or not result.get("ok"):
        raise SkillError("ui_changed", "failed to inject prompt into ChatGPT composer")
    if int(result.get("textLength") or 0) <= 0:
        raise SkillError("ui_changed", "ChatGPT composer did not retain injected prompt")


def _send_prompt(runner: SurfRunner, target: BrowserTarget) -> None:
    deadline = time.time() + 8
    last_status: Any = None
    while time.time() < deadline:
        result = _run_js_file(runner, target, _send_prompt_js(), timeout=10)
        last_status = result
        status = result.get("status") if isinstance(result, dict) else None
        if status == "clicked":
            return
        if status == "missing":
            break
        time.sleep(0.2)
    raise SkillError("ui_changed", f"ChatGPT send button not ready: {last_status}")


def _read_snapshot(runner: SurfRunner, target: BrowserTarget) -> dict[str, Any]:
    result = _run_js_file(runner, target, _snapshot_js(), timeout=15)
    if not isinstance(result, dict):
        raise SkillError("parse_error", "ChatGPT snapshot script returned unexpected data")
    return _normalize_snapshot(result)


def _wait_for_response(
    runner: SurfRunner,
    target: BrowserTarget,
    baseline: dict[str, Any],
    *,
    timeout_seconds: int,
) -> AssistantResponse:
    deadline = time.time() + timeout_seconds
    baseline_latest = baseline.get("latest") or {}
    previous_text = baseline_latest.get("text") or ""
    stable_since = time.time()
    stable_cycles = 0

    snapshot_timeouts = 0

    while time.time() < deadline:
        try:
            snapshot = _read_snapshot(runner, target)
            snapshot_timeouts = 0
        except SkillError as exc:
            if exc.type != "timeout":
                raise
            snapshot_timeouts += 1
            if snapshot_timeouts >= 3:
                raise SkillError("timeout", "ChatGPT response polling timed out after repeated surf JS timeouts") from exc
            time.sleep(0.5)
            continue

        latest = snapshot.get("latest") or {}
        current_text = latest.get("text") or ""
        has_new_content = _is_new_assistant_content(snapshot, baseline)

        if not has_new_content:
            time.sleep(0.5)
            continue

        if current_text != previous_text:
            previous_text = current_text
            stable_since = time.time()
            stable_cycles = 0
        elif current_text:
            stable_cycles += 1

        stable_ms = (time.time() - stable_since) * 1000
        complete = current_text and not snapshot.get("stopVisible") and (
            latest.get("hasFinishedActions") or stable_cycles >= 2 or stable_ms >= 1500
        )
        if complete:
            return AssistantResponse(text=current_text, message_id=latest.get("messageId"))

        time.sleep(0.5)

    raise SkillError("timeout", "ChatGPT response timed out")


def _normalize_snapshot(raw: dict[str, Any]) -> dict[str, Any]:
    candidates = raw.get("candidates")
    if not isinstance(candidates, list):
        candidates = []
    normalized: list[dict[str, Any]] = []
    for idx, candidate in enumerate(candidates):
        if not isinstance(candidate, dict) or not candidate.get("isAssistant"):
            continue
        text = clean_response(str(candidate.get("text") or ""))
        normalized.append({**candidate, "text": text, "turnIndex": idx})

    latest = None
    for candidate in reversed(normalized):
        if candidate.get("text"):
            latest = candidate
            break
    if latest is None and normalized:
        latest = normalized[-1]
    return {
        "latest": latest,
        "assistantCount": len(normalized),
        "stopVisible": bool(raw.get("stopVisible")),
    }


def _is_new_assistant_content(snapshot: dict[str, Any], baseline: dict[str, Any]) -> bool:
    latest = snapshot.get("latest")
    baseline_latest = baseline.get("latest")
    if not latest:
        return False
    if not baseline_latest:
        return bool(latest.get("text"))
    if snapshot.get("assistantCount", 0) > baseline.get("assistantCount", 0):
        return True
    if latest.get("messageId") and baseline_latest.get("messageId"):
        return latest.get("messageId") != baseline_latest.get("messageId")
    return bool(latest.get("text")) and latest.get("text") != baseline_latest.get("text")


def _tab_url_best_effort(runner: SurfRunner, target: BrowserTarget) -> str | None:
    try:
        tab = _find_tab_by_id(runner, target.tab_id)
    except SkillError:
        return None
    if tab and _is_chatgpt_url(str(tab.get("url", ""))):
        return str(tab["url"])
    return None


def _current_url(runner: SurfRunner, target: BrowserTarget) -> str:
    last_url: str | None = None
    # New conversations usually rewrite `/` to `/c/<id>` after first answer; give it a brief
    # chance so named sessions persist the continuity URL, not just the ChatGPT home URL.
    tab_url = _tab_url_best_effort(runner, target)
    if tab_url and _is_conversation_url(tab_url):
        return tab_url
    if tab_url:
        last_url = tab_url

    deadline = time.time() + 2
    while time.time() < deadline:
        result = _run_js_file(runner, target, "return location.href;", timeout=2)
        if isinstance(result, str) and _is_chatgpt_url(result):
            last_url = result
            if _is_conversation_url(result):
                return result
        time.sleep(0.3)
    if last_url:
        return last_url
    tab = _find_tab_by_id(runner, target.tab_id)
    if tab and _is_chatgpt_url(str(tab.get("url", ""))):
        return str(tab["url"])
    raise SkillError("parse_error", "could not read ChatGPT conversation URL")


def _run_json_on_target(runner: SurfRunner, target: BrowserTarget, args: list[str], *, timeout: int) -> Any:
    if target.scope == "window":
        return runner.run_json_on_window(target.command_id, args, timeout=timeout)
    return runner.run_json_on_tab(target.command_id, args, timeout=timeout)


def _run_js_file(runner: SurfRunner, target: BrowserTarget, code: str, *, timeout: int) -> Any:
    path = _write_temp_js(code)
    try:
        data = _run_json_on_target(runner, target, ["js", "--file", path], timeout=timeout)
        return _unwrap_js_result(data)
    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def _write_temp_js(code: str) -> str:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".js", prefix="surf-chatgpt-", dir="/tmp", delete=False) as handle:
        handle.write(code)
        handle.write("\n")
        return handle.name


def _unwrap_js_result(data: Any) -> Any:
    current = data
    for _ in range(4):
        if isinstance(current, dict):
            if "result" in current:
                current = current["result"]
                continue
            if "value" in current and set(current.keys()).issubset({"value", "type", "description"}):
                current = current["value"]
                continue
        break
    return current


def _status_js() -> str:
    return f"""
return (() => {{
  const promptSelectors = {json.dumps(PROMPT_SELECTORS)};
  const text = document.body?.innerText || '';
  const lowered = text.toLowerCase();
  const hasPrompt = promptSelectors.some((selector) => document.querySelector(selector));
  const challenge = Boolean(document.querySelector('script[src*="/challenge-platform/"]')) || lowered.includes('cloudflare') || lowered.includes('verify you are human') || lowered.includes('captcha');
  const loginRequired = location.href.includes('/auth/login') || (!hasPrompt && (lowered.includes('log in') || lowered.includes('sign up')));
  return {{ url: location.href, title: document.title, hasPrompt, challenge, loginRequired }};
}})();
""".strip()


def _select_thinking_level_js(thinking_label: str) -> str:
    return rf"""
return (async () => {{
  const desiredThinking = {json.dumps(thinking_label)};
  const desiredThinkingNorm = desiredThinking.toLowerCase();
  function sleep(ms) {{ return new Promise((resolve) => setTimeout(resolve, ms)); }}
  function textOf(node) {{ return (node?.textContent || node?.innerText || node?.getAttribute?.('aria-label') || '').trim(); }}
  function isVisible(node) {{
    const rect = node?.getBoundingClientRect?.();
    const style = node ? window.getComputedStyle(node) : null;
    return Boolean(rect && rect.width > 0 && rect.height > 0 && style?.visibility !== 'hidden' && style?.display !== 'none');
  }}
  function dispatchClickSequence(target) {{
    for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {{
      const common = {{ bubbles: true, cancelable: true, view: window }};
      const event = type.startsWith('pointer') && 'PointerEvent' in window
        ? new PointerEvent(type, {{ ...common, pointerId: 1, pointerType: 'mouse' }})
        : new MouseEvent(type, common);
      target.dispatchEvent(event);
    }}
  }}
  function modelButtonScore(node) {{
    if (!isVisible(node)) return -1;
    const label = textOf(node).replace(/\s+/g, ' ').trim();
    const aria = (node.getAttribute?.('aria-label') || '').toLowerCase();
    const testid = (node.getAttribute?.('data-testid') || '').toLowerCase();
    const haystack = (label + ' ' + aria + ' ' + testid).toLowerCase();
    let score = 0;
    if (testid.includes('model-switcher')) score += 100;
    if (/\b(instant|medium|high)\b/i.test(label)) score += 80;
    if (/model|gpt|intelligence/i.test(haystack)) score += 60;
    if (node.closest('main, form')) score += 20;
    if (/share|archive|delete|rename|pin chat|group chat/i.test(label)) score -= 200;
    return /model|gpt|intelligence|\b(instant|medium|high)\b/i.test(haystack) ? score : -1;
  }}
  function findModelButton() {{
    const selectors = ['[data-testid="model-switcher-dropdown-button"]', 'button[aria-haspopup="menu"]', 'button[aria-expanded]', '[role="button"]'];
    const seen = new Set();
    const candidates = [];
    for (const selector of selectors) {{
      for (const node of Array.from(document.querySelectorAll(selector))) {{
        if (seen.has(node)) continue;
        seen.add(node);
        const score = modelButtonScore(node);
        if (score >= 0) candidates.push({{ node, score, label: textOf(node) }});
      }}
    }}
    candidates.sort((a, b) => b.score - a.score);
    return candidates[0] || null;
  }}
  function visibleItems() {{
    const nodes = Array.from(document.querySelectorAll('[role="menu"] [role="menuitemradio"], [role="menu"] [role="menuitem"], [role="menu"] button, [data-radix-menu-content] [role="menuitemradio"], [data-radix-menu-content] [role="menuitem"], [data-radix-menu-content] button'));
    return nodes.filter(isVisible).map((node) => ({{ node, label: textOf(node).replace(/\s+/g, ' ').trim(), disabled: node.hasAttribute('disabled') || node.getAttribute('aria-disabled') === 'true' }})).filter((item) => item.label);
  }}
  document.dispatchEvent(new KeyboardEvent('keydown', {{ key: 'Escape', code: 'Escape', bubbles: true, cancelable: true }}));
  await sleep(60);
  const button = findModelButton();
  if (!button) return {{ ok: false, reason: 'model_button_missing', available: [] }};
  dispatchClickSequence(button.node);
  await sleep(180);
  const items = visibleItems();
  const match = items.find((item) => item.label.toLowerCase() === desiredThinkingNorm && !item.disabled);
  if (!match) return {{ ok: false, reason: 'thinking_missing', desired: desiredThinking, button: button.label, available: items.map((item) => item.label).slice(0, 30) }};
  dispatchClickSequence(match.node);
  await sleep(120);
  return {{ ok: true, selectedThinking: desiredThinking }};
}})();
""".strip()


def _select_model_choice_js(model_query: str | None, thinking_label: str | None) -> str:
    return rf"""
return (async () => {{
  const desiredModelQuery = {json.dumps(model_query)};
  const desiredThinking = {json.dumps(thinking_label)};
  const desiredThinkingNorm = desiredThinking ? desiredThinking.toLowerCase() : null;
  function sleep(ms) {{ return new Promise((resolve) => setTimeout(resolve, ms)); }}
  function compact(value) {{ return String(value || '').toLowerCase().replace(/[^a-z0-9]+/g, ''); }}
  function textOf(node) {{ return (node?.textContent || node?.innerText || node?.getAttribute?.('aria-label') || '').trim(); }}
  function isVisible(node) {{
    const rect = node?.getBoundingClientRect?.();
    const style = node ? window.getComputedStyle(node) : null;
    return Boolean(rect && rect.width > 0 && rect.height > 0 && style?.visibility !== 'hidden' && style?.display !== 'none');
  }}
  function isDisabled(node) {{
    const text = textOf(node).toLowerCase();
    return node?.hasAttribute?.('disabled') || node?.getAttribute?.('aria-disabled') === 'true' || /upgrade|unavailable|limit reached/.test(text);
  }}
  function dispatchClickSequence(target) {{
    const types = ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'];
    for (const type of types) {{
      const common = {{ bubbles: true, cancelable: true, view: window }};
      const event = type.startsWith('pointer') && 'PointerEvent' in window
        ? new PointerEvent(type, {{ ...common, pointerId: 1, pointerType: 'mouse' }})
        : new MouseEvent(type, common);
      target.dispatchEvent(event);
    }}
  }}
  function visibleItems() {{
    const nodes = Array.from(document.querySelectorAll('[role="menu"] [role="menuitemradio"], [role="menu"] [role="menuitem"], [role="menu"] button, [data-radix-menu-content] [role="menuitemradio"], [data-radix-menu-content] [role="menuitem"], [data-radix-menu-content] button, [cmdk-item]'));
    const seen = new Set();
    return nodes
      .filter((node) => {{ if (seen.has(node)) return false; seen.add(node); return isVisible(node); }})
      .map((node, index) => ({{ node, index, label: textOf(node).replace(/\s+/g, ' ').trim(), disabled: isDisabled(node), hasPopup: node.getAttribute?.('aria-haspopup') === 'menu' || node.getAttribute?.('data-state') === 'closed' }}))
      .filter((item) => item.label);
  }}
  function modelButtonScore(node) {{
    if (!isVisible(node)) return -1;
    const label = textOf(node).replace(/\s+/g, ' ').trim();
    const aria = (node.getAttribute?.('aria-label') || '').toLowerCase();
    const testid = (node.getAttribute?.('data-testid') || '').toLowerCase();
    const haystack = label + ' ' + aria + ' ' + testid;
    const lower = haystack.toLowerCase();
    let score = 0;
    if (testid.includes('model-switcher')) score += 100;
    if (/\b(instant|medium|high)\b/i.test(label)) score += 80;
    if (/gpt[- ]?5\.?(5|4)|\b5\.[54]\b|pro|model|intelligence/i.test(haystack)) score += 70;
    if (aria.includes('model')) score += 50;
    if (node.closest('main, form')) score += 20;
    const rect = node.getBoundingClientRect();
    if (rect.top > window.innerHeight * 0.45) score += 10;
    if (/share|archive|delete|rename|pin chat|group chat/i.test(label)) score -= 200;
    return lower.includes('model') || /\b(instant|medium|high)\b/i.test(label) || /gpt[- ]?5\.?(5|4)|\b5\.[54]\b|pro|intelligence/i.test(haystack) ? score : -1;
  }}
  function findModelButton() {{
    const selectors = [
      '[data-testid="model-switcher-dropdown-button"]',
      'button[aria-haspopup="menu"]',
      'button[aria-expanded]',
      '[role="button"]'
    ];
    const seen = new Set();
    const candidates = [];
    for (const selector of selectors) {{
      for (const node of Array.from(document.querySelectorAll(selector))) {{
        if (seen.has(node)) continue;
        seen.add(node);
        const score = modelButtonScore(node);
        if (score >= 0) candidates.push({{ node, score, label: textOf(node) }});
      }}
    }}
    candidates.sort((a, b) => b.score - a.score);
    return candidates[0] || null;
  }}
  async function openModelMenu() {{
    document.dispatchEvent(new KeyboardEvent('keydown', {{ key: 'Escape', code: 'Escape', bubbles: true, cancelable: true }}));
    await sleep(60);
    const button = findModelButton();
    if (!button) return null;
    dispatchClickSequence(button.node);
    await sleep(220);
    return button;
  }}
  function versionScore(label) {{
    const c = compact(label);
    if (c.includes('55')) return 55;
    if (c.includes('54')) return 54;
    if (c.includes('5')) return 50;
    return 0;
  }}
  function modelItemScore(item, query) {{
    if (item.disabled) return -9999;
    const label = item.label;
    const l = compact(label);
    const q = compact(query);
    if (!l || !q) return -9999;
    let score = 0;
    if (l === q) score += 1000;
    if (l.includes(q)) score += 700;
    if (q.includes(l)) score += 200;
    if (q.includes('pro')) score += l.includes('pro') ? 350 : -250;
    else if (l.includes('pro')) score -= 120;
    if (q.includes('55') || q.includes('gpt55')) score += l.includes('55') ? 320 : -80;
    if (q.includes('54') || q.includes('gpt54')) score += l.includes('54') ? 320 : -80;
    if (q.includes('gpt') && l.includes('gpt')) score += 40;
    for (const token of q.match(/[a-z]+|\d+/g) || []) {{
      if (token.length >= 2 && l.includes(token)) score += 80;
    }}
    score += versionScore(label);
    if (/temporary|settings|customize|connector|project|archive|delete|share/i.test(label)) score -= 500;
    return score;
  }}
  function findModelSelector(items) {{
    const thinking = new Set(['instant', 'medium', 'high']);
    const candidates = items.filter((item) => !thinking.has(item.label.toLowerCase()));
    if (!candidates.length) return null;
    const scored = candidates.map((item) => {{
      let score = item.index;
      if (item.hasPopup) score += 80;
      if (/model|gpt[- ]?5|\b5\.[54]\b|pro|more/i.test(item.label)) score += 120;
      if (/temporary|settings|customize|connector|project|archive|delete|share/i.test(item.label)) score -= 500;
      return {{ item, score }};
    }}).sort((a, b) => b.score - a.score);
    return scored[0].item;
  }}

  let selectedThinking = null;
  let selectedModel = null;
  if (desiredThinkingNorm) {{
    const button = await openModelMenu();
    if (!button) return {{ ok: false, reason: 'model_button_missing', available: [] }};
    let items = visibleItems();
    const match = items.find((item) => item.label.toLowerCase() === desiredThinkingNorm && !item.disabled);
    if (!match) return {{ ok: false, reason: 'thinking_missing', desired: desiredThinking, button: button.label, available: items.map((item) => item.label).slice(0, 30) }};
    dispatchClickSequence(match.node);
    selectedThinking = desiredThinking;
    await sleep(120);
  }}

  if (desiredModelQuery) {{
    const button = await openModelMenu();
    if (!button) return {{ ok: false, reason: 'model_button_missing', available: [] }};
    let items = visibleItems();
    const selector = findModelSelector(items);
    if (!selector) return {{ ok: false, reason: 'model_selector_missing', desired: desiredModelQuery, button: button.label, available: items.map((item) => item.label).slice(0, 30) }};
    selector.node.dispatchEvent(new MouseEvent('mouseover', {{ bubbles: true, cancelable: true, view: window }}));
    dispatchClickSequence(selector.node);
    await sleep(250);
    items = visibleItems();
    const scored = items
      .filter((item) => item.node !== selector.node)
      .map((item) => ({{ item, score: modelItemScore(item, desiredModelQuery) }}))
      .sort((a, b) => b.score - a.score);
    const best = scored[0];
    if (!best || best.score < 120) return {{ ok: false, reason: 'model_missing', desired: desiredModelQuery, selector: selector.label, available: items.map((item) => item.label + (item.disabled ? ' (disabled)' : '')).slice(0, 40) }};
    dispatchClickSequence(best.item.node);
    selectedModel = best.item.label;
    await sleep(150);
  }}

  return {{ ok: true, selectedModel, selectedThinking }};
}})();
""".strip()


def _inject_prompt_js(prompt: str) -> str:
    return f"""
return (() => {{
  const prompt = {json.dumps(prompt)};
  const selectors = {json.dumps(PROMPT_SELECTORS)};
  function dispatchInput(node) {{
    node.dispatchEvent(new InputEvent('beforeinput', {{ bubbles: true, cancelable: true, inputType: 'insertFromPaste', data: prompt }}));
    node.dispatchEvent(new InputEvent('input', {{ bubbles: true, inputType: 'insertFromPaste', data: prompt }}));
  }}
  for (const selector of selectors) {{
    const node = document.querySelector(selector);
    if (!node) continue;
    node.focus?.();
    if ('value' in node) {{
      node.value = prompt;
      dispatchInput(node);
    }} else {{
      const selection = document.getSelection?.();
      const range = document.createRange();
      range.selectNodeContents(node);
      range.collapse(false);
      selection?.removeAllRanges();
      selection?.addRange(range);
      let inserted = false;
      try {{ inserted = document.execCommand?.('insertText', false, prompt) || false; }} catch {{}}
      if (!inserted || !(node.innerText || node.textContent || '').trim()) {{
        node.textContent = prompt;
        dispatchInput(node);
      }} else {{
        dispatchInput(node);
      }}
    }}
    const text = node.innerText || node.value || node.textContent || '';
    return {{ ok: text.trim().length > 0, textLength: text.length }};
  }}
  return {{ ok: false, textLength: 0, reason: 'composer_missing' }};
}})();
""".strip()


def _send_prompt_js() -> str:
    return f"""
return (() => {{
  const selectors = {json.dumps(SEND_SELECTORS)};
  function dispatchClickSequence(target) {{
    const types = ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'];
    for (const type of types) {{
      const common = {{ bubbles: true, cancelable: true, view: window }};
      const event = type.startsWith('pointer') && 'PointerEvent' in window
        ? new PointerEvent(type, {{ ...common, pointerId: 1, pointerType: 'mouse' }})
        : new MouseEvent(type, common);
      target.dispatchEvent(event);
    }}
  }}
  for (const selector of selectors) {{
    const button = document.querySelector(selector);
    if (!button) continue;
    const disabled = button.hasAttribute('disabled') || button.getAttribute('aria-disabled') === 'true' || button.getAttribute('data-disabled') === 'true';
    if (disabled) return {{ status: 'disabled' }};
    dispatchClickSequence(button);
    return {{ status: 'clicked' }};
  }}
  return {{ status: 'missing' }};
}})();
""".strip()


def _snapshot_js() -> str:
    return f"""
return (() => {{
  const scope = document.querySelector('main') || document;
  const conversationSelector = {json.dumps(CONVERSATION_TURN_SELECTOR)};
  const assistantSelector = {json.dumps(ASSISTANT_SELECTORS)};
  const contentSelectors = {json.dumps(ASSISTANT_CONTENT_SELECTORS)};
  const stopSelector = {json.dumps(STOP_SELECTOR)};
  const finishedSelector = {json.dumps(FINISHED_SELECTOR)};

  function toCandidate(turnNode, messageRoot = null) {{
    const resolvedMessageRoot = messageRoot || (turnNode.matches?.(assistantSelector) ? turnNode : turnNode.querySelector(assistantSelector));
    const searchRoot = resolvedMessageRoot || turnNode;
    let contentRoot = null;
    for (const selector of contentSelectors) {{
      const match = selector === '[dir="auto"]'
        ? (searchRoot.matches?.(selector) ? searchRoot : null)
        : (searchRoot.matches?.(selector) ? searchRoot : searchRoot.querySelector(selector));
      if (match) {{ contentRoot = match; break; }}
    }}
    const role = resolvedMessageRoot?.getAttribute('data-message-author-role') || turnNode.getAttribute('data-message-author-role') || null;
    const turn = resolvedMessageRoot?.getAttribute('data-turn') || turnNode.getAttribute('data-turn') || null;
    const isAssistant = role === 'assistant' || turn === 'assistant' || resolvedMessageRoot !== null;
    const textNode = contentRoot || turnNode;
    const text = textNode.textContent || textNode.innerText || '';
    const messageId = resolvedMessageRoot?.getAttribute('data-message-id') || turnNode.getAttribute('data-message-id') || null;
    const hasFinishedActions = Boolean(turnNode.querySelector(finishedSelector));
    return {{ role, turn, isAssistant, text, messageId, hasFinishedActions }};
  }}

  let turnNodes = Array.from(scope.querySelectorAll(conversationSelector)).slice(-8);
  let candidates = turnNodes.map((turnNode) => toCandidate(turnNode));
  if (candidates.length === 0) {{
    candidates = Array.from(scope.querySelectorAll(assistantSelector)).slice(-8).map((messageRoot) => toCandidate(messageRoot, messageRoot));
  }}
  return {{ candidates, stopVisible: Boolean(scope.querySelector(stopSelector)) }};
}})();
""".strip()


def _extract_int(data: Any, *keys: str) -> int | None:
    for value in _walk_values(data, keys):
        coerced = _coerce_int(value)
        if coerced is not None:
            return coerced
    if isinstance(data, str):
        if any(key.lower().startswith("window") for key in keys):
            match = re.search(r"\bWindow\s+(\d+)\b", data, flags=re.I)
            if match:
                return int(match.group(1))
        if any("tab" in key.lower() or key in {"id", "_resolvedTabId"} for key in keys):
            match = re.search(r"\btab\s+(\d+)\b", data, flags=re.I)
            if match:
                return int(match.group(1))
    return None


def _walk_values(data: Any, keys: Iterable[str]) -> Iterable[Any]:
    if isinstance(data, dict):
        for key in keys:
            if key in data:
                yield data[key]
        for value in data.values():
            yield from _walk_values(value, keys)
    elif isinstance(data, list):
        for item in data:
            yield from _walk_values(item, keys)


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _is_chatgpt_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and parsed.hostname in CHATGPT_HOSTS


def _is_conversation_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.hostname in CHATGPT_HOSTS and parsed.path.startswith("/c/")


def _session_id_from_url(url: str) -> str | None:
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.hostname not in CHATGPT_HOSTS:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[0] == "c":
        return parts[1]
    return None
