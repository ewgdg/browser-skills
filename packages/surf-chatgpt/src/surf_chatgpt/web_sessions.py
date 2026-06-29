from __future__ import annotations

import json
import os
import uuid
from typing import Any

from . import SOURCE_LABEL
from .errors import SkillError
from .surf import SurfRunner
from .temp_js import unlink_temp_file, write_temp_js

CHATGPT_HOME = "https://chatgpt.com/"


def search_web_sessions(query: str, *, limit: int = 10, surf: SurfRunner | None = None) -> dict[str, Any]:
    clean_query = query.strip()
    if not clean_query:
        raise SkillError("invalid_args", "session search query cannot be empty")
    if limit <= 0:
        raise SkillError("invalid_args", "--limit must be positive")

    runner = surf or SurfRunner()
    target = _open_temp_chatgpt_window(runner)
    try:
        _wait_load_best_effort(runner, target)
        result = _run_js_file(runner, target, _search_sessions_js(clean_query, limit), timeout=35)
        if not isinstance(result, dict):
            raise SkillError("parse_error", "ChatGPT session search returned unexpected data")
        return _result_to_payload(clean_query, result, limit)
    finally:
        _close_temp_target(runner, target)


class _Target:
    def __init__(self, thread: str):
        self.thread = thread


def _open_temp_chatgpt_window(runner: SurfRunner) -> _Target:
    thread = f"surf-chatgpt-search-{os.getpid()}-{uuid.uuid4().hex[:12]}"
    runner.new(thread, timeout=30)
    runner.open(thread, CHATGPT_HOME, timeout=30)
    return _Target(thread)


def _close_temp_target(runner: SurfRunner, target: _Target) -> None:
    try:
        runner.close(target.thread, timeout=10)
    except SkillError:
        # Search result/error matters more than cleanup diagnostics; do not dump browser state.
        pass


def _wait_load_best_effort(runner: SurfRunner, target: _Target) -> None:
    try:
        runner.wait(target.thread, "1000", timeout=35)
    except SkillError as exc:
        if exc.type not in {"timeout", "ui_changed"}:
            raise


def _result_to_payload(query: str, result: dict[str, Any], limit: int) -> dict[str, Any]:
    status = result.get("status")
    if status == "login_required":
        raise SkillError("login_required", "ChatGPT login required")
    if status == "captcha_or_cloudflare":
        raise SkillError("captcha_or_cloudflare", "ChatGPT challenge detected")
    if status != "ok":
        reason = str(result.get("reason") or status or "search UI unavailable")
        raise SkillError("ui_changed", f"ChatGPT session search UI unavailable: {reason}")

    sessions = _normalize_sessions(result.get("sessions"), limit)
    payload: dict[str, Any] = {
        "ok": True,
        "source": SOURCE_LABEL,
        "query": query,
        "sessions": sessions,
    }
    if not sessions:
        payload["warning"] = "no matching ChatGPT sessions found"
    return payload


def _normalize_sessions(raw: Any, limit: int) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    sessions: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        session_id = str(item.get("id") or "").strip()
        url = str(item.get("url") or "").strip()
        title = " ".join(str(item.get("title") or "Untitled").split()) or "Untitled"
        if not session_id or not url or session_id in seen:
            continue
        if "/c/" not in url:
            continue
        seen.add(session_id)
        sessions.append({"id": session_id, "url": url, "title": title})
        if len(sessions) >= limit:
            break
    return sessions


def _run_js_file(runner: SurfRunner, target: _Target, code: str, *, timeout: int) -> Any:
    path = write_temp_js(_surf_agent_function_source(code), prefix="surf-chatgpt-search-")
    try:
        return runner.eval_file(target.thread, path, timeout=timeout)
    finally:
        unlink_temp_file(path)


def _surf_agent_function_source(body: str) -> str:
    return "async () => {\n" + body + "\n}"


def _search_sessions_js(query: str, limit: int) -> str:
    return f"""
return (async () => {{
  const query = {json.dumps(query)};
  const limit = {int(limit)};
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const compact = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
  const visible = (node) => {{
    if (!node) return false;
    const rect = node.getBoundingClientRect?.();
    const style = window.getComputedStyle?.(node);
    return Boolean(rect && rect.width > 0 && rect.height > 0 && style?.visibility !== 'hidden' && style?.display !== 'none');
  }};
  const textOf = (node) => compact(node?.innerText || node?.textContent || node?.getAttribute?.('aria-label') || node?.getAttribute?.('placeholder'));
  const click = (target) => {{
    if (!target) return false;
    for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {{
      const common = {{ bubbles: true, cancelable: true, view: window }};
      const event = type.startsWith('pointer') && 'PointerEvent' in window
        ? new PointerEvent(type, {{ ...common, pointerId: 1, pointerType: 'mouse' }})
        : new MouseEvent(type, common);
      target.dispatchEvent(event);
    }}
    return true;
  }};

  const pageText = compact(document.body?.innerText || '').toLowerCase();
  if (document.querySelector('script[src*="/challenge-platform/"]') || pageText.includes('cloudflare') || pageText.includes('verify you are human') || pageText.includes('captcha')) {{
    return {{ status: 'captcha_or_cloudflare' }};
  }}
  const hasComposer = Boolean(document.querySelector('#prompt-textarea, [data-testid="composer-textarea"], textarea[name="prompt-textarea"], .ProseMirror, [contenteditable="true"]'));
  if (location.href.includes('/auth/login') || (!hasComposer && (pageText.includes('log in') || pageText.includes('sign up')))) {{
    return {{ status: 'login_required' }};
  }}

  const searchControl = Array.from(document.querySelectorAll('button, a, [role="button"]'))
    .filter(visible)
    .find((node) => /search( chats?)?/i.test(textOf(node)) || /search/i.test(node.getAttribute?.('aria-label') || ''));
  if (!searchControl) return {{ status: 'ui_missing', reason: 'search_control_missing' }};
  click(searchControl);
  await sleep(500);

  const inputSelectors = 'input[placeholder*="Search" i], textarea[placeholder*="Search" i], input[aria-label*="Search" i], textarea[aria-label*="Search" i], [contenteditable="true"][aria-label*="Search" i]';
  let input = Array.from(document.querySelectorAll(inputSelectors)).filter(visible)[0];
  if (!input) return {{ status: 'ui_missing', reason: 'search_input_missing' }};

  input.focus?.();
  if ('value' in input) {{
    const proto = Object.getPrototypeOf(input);
    const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
    setter ? setter.call(input, query) : (input.value = query);
  }} else {{
    input.textContent = query;
  }}
  input.dispatchEvent(new InputEvent('input', {{ bubbles: true, inputType: 'insertText', data: query }}));
  input.dispatchEvent(new Event('change', {{ bubbles: true }}));
  input.dispatchEvent(new KeyboardEvent('keyup', {{ bubbles: true, key: query.slice(-1) || 'a' }}));
  await sleep(1400);

  const container = input.closest('[role="dialog"], [data-radix-dialog-content], [data-testid*="search" i]')
    || Array.from(document.querySelectorAll('[role="dialog"], [data-radix-dialog-content], [data-testid*="search" i]')).filter(visible)[0];
  if (!container) return {{ status: 'ui_missing', reason: 'search_container_missing' }};

  const seen = new Set();
  const sessions = [];
  for (const anchor of Array.from(container.querySelectorAll('a[href*="/c/"]'))) {{
    const href = anchor.getAttribute('href') || '';
    let url;
    try {{ url = new URL(href, location.origin); }} catch {{ continue; }}
    const match = url.pathname.match(/^\\/c\\/([^/?#]+)/);
    if (!match) continue;
    const id = match[1];
    if (seen.has(id)) continue;
    const lines = compact(anchor.innerText || anchor.textContent || '').split(/(?<=.) (?=[A-Z][a-z])/);
    const title = compact(lines[0] || anchor.getAttribute('aria-label') || 'Untitled');
    seen.add(id);
    sessions.push({{ id, url: `${{location.origin}}/c/${{id}}`, title }});
    if (sessions.length >= limit) break;
  }}
  return {{ status: 'ok', sessions }};
}})();
""".strip()
