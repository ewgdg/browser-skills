from __future__ import annotations

import json
import os
import re
import tempfile
from typing import Any, Iterable

from . import SOURCE_LABEL
from .errors import SkillError
from .surf import SurfRunner

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
    def __init__(self, tab_id: int, window_id: int):
        self.tab_id = tab_id
        self.window_id = window_id


def _open_temp_chatgpt_window(runner: SurfRunner) -> _Target:
    # No URL: surf opens its neutral surf-agent page first, letting window rules keep it unfocused.
    data = runner.run_json(["window.new"], timeout=30)
    tab_id = _extract_int(data, "tabId", "tab_id", "id", "_resolvedTabId")
    window_id = _extract_int(data, "windowId", "window_id")
    if tab_id is None:
        raise SkillError("parse_error", "surf window.new JSON missing tab id")
    if window_id is None:
        raise SkillError("parse_error", "surf window.new JSON missing window id")
    target = _Target(tab_id=tab_id, window_id=window_id)
    runner.run_json_on_window(target.window_id, ["navigate", CHATGPT_HOME], timeout=30)
    return target


def _close_temp_target(runner: SurfRunner, target: _Target) -> None:
    try:
        if target.window_id is not None:
            runner.run_json(["window.close", str(target.window_id)], timeout=10)
        else:
            runner.run_json(["tab.close", str(target.tab_id)], timeout=10)
    except SkillError:
        # Search result/error matters more than cleanup diagnostics; do not dump browser state.
        pass


def _wait_load_best_effort(runner: SurfRunner, target: _Target) -> None:
    try:
        runner.run_json_on_window(target.window_id, ["wait.load", "--timeout", "30000"], timeout=35)
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
    path = _write_temp_js(code)
    try:
        data = runner.run_json_on_window(target.window_id, ["js", "--file", path], timeout=timeout)
        return _unwrap_js_result(data)
    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def _write_temp_js(code: str) -> str:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".js", prefix="surf-chatgpt-search-", dir="/tmp", delete=False) as handle:
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
