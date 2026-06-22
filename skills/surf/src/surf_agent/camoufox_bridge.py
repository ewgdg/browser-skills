from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ACTIONABLE_SELECTOR = "a,button,input,textarea,select,[role=button],[role=link],[contenteditable=true]"
REF_PATTERN = re.compile(r"^(?:cf|e)\d+$")
STALE_REF_MESSAGE = "Ref {ref!r} not found in the current page snapshot. Capture a new snapshot."
CLOSED_TARGET_MESSAGE = "Target page, context or browser has been closed"
STARTUP_PAGE_URLS = {"", "about:blank", "about:home", "about:newtab", "chrome://newtab/"}


@dataclass(frozen=True)
class TargetFingerprint:
    tag: str = ""
    role: str = ""
    name: str = ""
    text: str = ""
    bbox: dict[str, float] | None = None


@dataclass(frozen=True)
class RefTarget:
    ref: str
    selector: str
    index: int
    css_path: str | None
    fingerprint: TargetFingerprint


@dataclass
class PageSlot:
    page: Any
    page_token: int
    ref_map: dict[str, RefTarget] = field(default_factory=dict)


class CamoufoxRuntime:
    def __init__(self, *, profile_dir: Path, headless: bool = False) -> None:
        self.profile_dir = profile_dir
        self.headless = headless
        self.manager: Any | None = None
        self.browser_or_context: Any | None = None
        self.pages: dict[str, PageSlot] = {}
        self._next_page_token = 1

    def start(self) -> None:
        if self.browser_or_context is not None:
            return
        try:
            from camoufox.sync_api import Camoufox
        except ImportError as exc:
            raise RuntimeError("Camoufox is not installed. Run `uv sync --extra camoufox`, then `uv run python -m camoufox fetch`.") from exc
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.manager = Camoufox(persistent_context=True, user_data_dir=str(self.profile_dir), headless=self.headless)
        self.browser_or_context = self.manager.__enter__()

    def stop(self) -> str:
        if self.manager is not None:
            self.manager.__exit__(None, None, None)
        self.manager = None
        self.browser_or_context = None
        self.pages.clear()
        return "stopped\n"

    def call(self, name: str, args: dict[str, Any]) -> str:
        if name == "stop":
            return self.stop()
        thread = str(args.get("thread") or "default")
        if name == "state":
            slot = self.pages.get(thread)
            if not slot:
                return json.dumps({"backend": "camoufox", "open": False, "thread": thread}) + "\n"
            return json.dumps({"backend": "camoufox", "open": True, "thread": thread, **self._metadata(slot)}) + "\n"
        if name == "list":
            rows = [{"thread": key, **self._metadata(slot)} for key, slot in sorted(self.pages.items())]
            return json.dumps({"backend": "camoufox", "pages": rows}, sort_keys=True) + "\n"
        if name == "close":
            old = self.pages.pop(thread, None)
            if old:
                old.page.close()
            return "closed\n"
        if name == "scroll" and str(args.get("direction") or "down") not in {"up", "down", "top", "bottom"}:
            raise RuntimeError("scroll requires direction: up, down, top, or bottom")
        self.start()
        if name == "new":
            slot = self._new_page(thread)
            url = str(args.get("url") or "about:blank")
            if url != "about:blank":
                slot.page.goto(url, wait_until="domcontentloaded")
            slot.ref_map.clear()
            return self._format_opened(slot.page)
        if name == "open":
            url = str(args["url"])

            def open_page(slot: PageSlot) -> str:
                slot.page.goto(url, wait_until="domcontentloaded")
                slot.ref_map.clear()
                return self._format_opened(slot.page)

            return self._with_live_page(thread, open_page)
        slot = self._page(thread)
        if name == "back":
            slot.page.go_back(wait_until="domcontentloaded")
            slot.ref_map.clear()
            return self._format_opened(slot.page)
        if name == "text":
            return self._body_text(slot.page)
        if name == "snapshot":
            return self._snapshot(slot)
        if name == "click":
            locator = self._target_locator(slot, str(args["uid"]))
            locator.click()
            slot.ref_map.clear()
            return "clicked\n"
        if name == "fill":
            locator = self._target_locator(slot, str(args["uid"]))
            locator.fill(str(args.get("text") or ""))
            slot.ref_map.clear()
            return "filled\n"
        if name == "type":
            slot.page.keyboard.type(str(args.get("text") or ""))
            slot.ref_map.clear()
            return "typed\n"
        if name == "press":
            slot.page.keyboard.press(str(args.get("key") or "Enter"))
            slot.ref_map.clear()
            return "pressed\n"
        if name == "scroll":
            direction = str(args.get("direction") or "down")
            if direction not in {"up", "down", "top", "bottom"}:
                raise RuntimeError("scroll requires direction: up, down, top, or bottom")
            delta = -700 if direction in {"up", "top"} else 700
            if direction == "top":
                slot.page.evaluate("() => window.scrollTo(0, 0)")
            elif direction == "bottom":
                slot.page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
            else:
                slot.page.mouse.wheel(0, delta)
            slot.ref_map.clear()
            return "scrolled\n"
        if name == "wait":
            target = args.get("target")
            if isinstance(target, (int, float)):
                slot.page.wait_for_timeout(float(target))
            else:
                slot.page.get_by_text(str(target)).first.wait_for(timeout=10_000)
            slot.ref_map.clear()
            return "waited\n"
        if name == "screenshot":
            path = str(args["path"])
            slot.page.screenshot(path=path, full_page=True)
            return f"screenshot: {path}\n"
        if name == "eval":
            result = slot.page.evaluate(str(args.get("code") or ""))
            slot.ref_map.clear()
            return json.dumps(result, ensure_ascii=False) + "\n"
        if name == "focus":
            slot.page.bring_to_front()
            return "focused\n"
        raise RuntimeError(f"unsupported Camoufox command: {name}")

    def _context(self) -> Any:
        if self.browser_or_context is None:
            raise RuntimeError("Camoufox runtime is not started")
        # persistent_context=True returns BrowserContext. Non-persistent would return Browser.
        if hasattr(self.browser_or_context, "new_page") and hasattr(self.browser_or_context, "pages"):
            return self.browser_or_context
        return self.browser_or_context.new_context()

    def _with_live_page(self, thread: str, action: Any) -> str:
        slot = self._page(thread)
        try:
            return action(slot)
        except Exception as exc:
            if not self._is_closed_target_error(exc):
                raise
            slot = self._new_page(thread)
            return action(slot)

    def _page(self, thread: str) -> PageSlot:
        slot = self.pages.get(thread)
        if slot and self._page_is_open(slot.page):
            return slot
        return self._new_page(thread)

    def _new_page(self, thread: str) -> PageSlot:
        old = self.pages.pop(thread, None)
        if old and self._page_is_open(old.page):
            old.page.close()
        try:
            page = self._adopt_unowned_page() or self._context().new_page()
        except Exception as exc:
            if not self._is_closed_target_error(exc):
                raise
            # Manual window close can close the whole persistent context; recreate it.
            self._restart_closed_context()
            page = self._adopt_unowned_page() or self._context().new_page()
        slot = PageSlot(page=page, page_token=self._next_page_token)
        self._next_page_token += 1
        self.pages[thread] = slot
        return slot

    def _adopt_unowned_page(self) -> Any | None:
        owned_pages = {id(slot.page) for slot in self.pages.values()}
        try:
            context_pages = list(self._context().pages)
        except Exception as exc:
            if self._is_closed_target_error(exc):
                return None
            raise
        candidates = [page for page in context_pages if id(page) not in owned_pages and self._page_is_open(page)]
        if not candidates:
            return None
        startup_pages = [page for page in candidates if self._page_url(page) in STARTUP_PAGE_URLS]
        adopted = startup_pages[0] if startup_pages else candidates[0]
        for page in candidates:
            if page is not adopted:
                page.close()
        return adopted

    def _page_url(self, page: Any) -> str:
        return str(getattr(page, "url", "") or "")

    def _restart_closed_context(self) -> None:
        try:
            self.stop()
        except Exception:
            self.manager = None
            self.browser_or_context = None
            self.pages.clear()
        self.start()

    def _page_is_open(self, page: Any) -> bool:
        try:
            return not page.is_closed()
        except Exception as exc:
            if self._is_closed_target_error(exc):
                return False
            raise

    def _is_closed_target_error(self, exc: Exception) -> bool:
        return CLOSED_TARGET_MESSAGE in str(exc)

    def _body_text(self, page: Any) -> str:
        try:
            text = page.locator("body").inner_text(timeout=5_000)
        except Exception:
            text = page.content()
        return text + ("" if text.endswith("\n") else "\n")

    def _snapshot(self, slot: PageSlot) -> str:
        page = slot.page
        parts = ["snapshot:"]
        try:
            aria = self._aria_snapshot(page)
        except Exception:
            try:
                aria = self._aria_snapshot(page.locator("body"))
            except Exception:
                aria = self._body_text(page).strip()
        if aria:
            parts.append(str(aria).rstrip())
        ref_lines = self._index_actionable_refs(slot)
        if ref_lines:
            parts.extend(ref_lines)
        return "\n".join(parts).rstrip() + "\n"

    def _aria_snapshot(self, target: Any) -> str:
        try:
            return str(target.aria_snapshot(mode="ai", timeout=5_000))
        except TypeError:
            return str(target.aria_snapshot(timeout=5_000))

    def _index_actionable_refs(self, slot: PageSlot) -> list[str]:
        locator = slot.page.locator(ACTIONABLE_SELECTOR)
        slot.ref_map.clear()
        lines: list[str] = []
        try:
            count = min(locator.count(), 200)
        except Exception:
            return lines
        for index in range(count):
            item = locator.nth(index)
            try:
                if not item.is_visible(timeout=250):
                    continue
                ref = f"cf{len(slot.ref_map)}"
                fingerprint = self._fingerprint_locator(item)
                target = RefTarget(
                    ref=ref,
                    selector=ACTIONABLE_SELECTOR,
                    index=index,
                    css_path=self._css_path(item),
                    fingerprint=fingerprint,
                )
                slot.ref_map[ref] = target
                lines.append(f"- {self._format_snapshot_node(fingerprint)} [ref={ref}]")
            except Exception:
                continue
        return lines

    def _format_snapshot_node(self, fingerprint: TargetFingerprint) -> str:
        label = fingerprint.role or fingerprint.tag or "element"
        name = fingerprint.name or fingerprint.text
        name = " ".join(name.split())[:160]
        return f'{label} "{name}"' if name else label

    def _fingerprint_locator(self, locator: Any) -> TargetFingerprint:
        tag = self._safe(lambda: locator.evaluate("el => el.tagName.toLowerCase()"), "")
        role = self._safe(lambda: locator.get_attribute("role"), "")
        text = self._safe(lambda: locator.inner_text(timeout=250), "")
        value = self._safe(lambda: locator.input_value(timeout=250), "")
        name = ""
        for attr in ("aria-label", "placeholder", "title", "alt"):
            name = self._safe(lambda attr=attr: locator.get_attribute(attr), "")
            if name:
                break
        if not name:
            name = text or value or self._safe(lambda: locator.get_attribute("href"), "")
        bbox = self._bbox(locator)
        return TargetFingerprint(tag=normalize_text(tag), role=normalize_text(role), name=normalize_text(name), text=normalize_text(text or value), bbox=bbox)

    def _bbox(self, locator: Any) -> dict[str, float] | None:
        try:
            box = locator.bounding_box()
        except Exception:
            return None
        if not isinstance(box, dict):
            return None
        result: dict[str, float] = {}
        for key in ("x", "y", "width", "height"):
            value = box.get(key)
            if isinstance(value, (int, float)):
                result[key] = float(value)
        return result or None

    def _css_path(self, locator: Any) -> str | None:
        script = """
        el => {
          if (!el || !el.tagName) return null;
          const esc = globalThis.CSS && CSS.escape ? CSS.escape : value => String(value).replace(/[^a-zA-Z0-9_-]/g, '\\$&');
          const parts = [];
          for (let node = el; node && node.nodeType === Node.ELEMENT_NODE; node = node.parentElement) {
            let part = node.tagName.toLowerCase();
            if (node.id) {
              parts.unshift(`${part}#${esc(node.id)}`);
              break;
            }
            const parent = node.parentElement;
            if (parent) {
              const siblings = Array.from(parent.children).filter(child => child.tagName === node.tagName);
              if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
            }
            parts.unshift(part);
          }
          return parts.join(' > ');
        }
        """
        value = self._safe(lambda: locator.evaluate(script), "")
        return value or None

    def _target_locator(self, slot: PageSlot, target: str) -> Any:
        normalized = target[1:] if target.startswith("@") else target
        if normalized in slot.ref_map or target.startswith("@") or REF_PATTERN.match(normalized):
            return self._ref_locator(slot, normalized)
        return self._selector_locator(slot, target)

    def _ref_locator(self, slot: PageSlot, ref: str) -> Any:
        stored = slot.ref_map.get(ref)
        if not stored:
            raise RuntimeError(STALE_REF_MESSAGE.format(ref=ref))
        candidates = self._ref_candidates(slot, stored)
        for candidate in candidates:
            try:
                if candidate.is_visible(timeout=250) and self._fingerprint_matches(stored.fingerprint, self._fingerprint_locator(candidate)):
                    return candidate
            except Exception:
                continue
        raise RuntimeError(STALE_REF_MESSAGE.format(ref=ref))

    def _ref_candidates(self, slot: PageSlot, stored: RefTarget) -> list[Any]:
        candidates: list[Any] = []
        if stored.css_path:
            candidates.extend(self._locator_candidates(slot.page.locator(stored.css_path), limit=3))
        candidates.extend(self._locator_candidates(slot.page.locator(stored.selector), limit=200, preferred_index=stored.index))
        return candidates

    def _locator_candidates(self, locator: Any, *, limit: int, preferred_index: int | None = None) -> list[Any]:
        candidates: list[Any] = []
        try:
            count = min(locator.count(), limit)
        except Exception:
            return candidates
        indexes = list(range(count))
        if preferred_index is not None and 0 <= preferred_index < count:
            indexes.remove(preferred_index)
            indexes.insert(0, preferred_index)
        for index in indexes:
            try:
                candidates.append(locator.nth(index))
            except Exception:
                continue
        return candidates

    def _selector_locator(self, slot: PageSlot, selector: str) -> Any:
        try:
            locator = slot.page.locator(selector)
            if locator.count() < 1:
                raise RuntimeError
            candidate = locator.first
            if hasattr(candidate, "is_visible") and not candidate.is_visible(timeout=250):
                visible = next((item for item in self._locator_candidates(locator, limit=50) if item.is_visible(timeout=250)), None)
                if visible is not None:
                    return visible
            return candidate
        except Exception as exc:
            raise RuntimeError(f"target {selector!r} is neither a current snapshot ref nor a matching selector") from exc

    def _fingerprint_matches(self, expected: TargetFingerprint, actual: TargetFingerprint) -> bool:
        if expected.tag and actual.tag and expected.tag != actual.tag:
            return False
        expected_labels = {value for value in (expected.name, expected.text) if value}
        actual_labels = {value for value in (actual.name, actual.text) if value}
        if expected_labels:
            # Role alone is too broad: many changed buttons share role/tag.
            return bool(expected_labels & actual_labels)
        if expected.role:
            return expected.role == actual.role
        return True

    def _safe(self, fn: Any, default: str = "") -> str:
        try:
            value = fn()
            return "" if value is None else str(value).strip()
        except Exception:
            return default

    def _metadata(self, slot: PageSlot) -> dict[str, str | int]:
        page = slot.page
        return {"page_id": slot.page_token, "url": str(getattr(page, "url", "") or ""), "title": self._title(page)}

    def _title(self, page: Any) -> str:
        try:
            return str(page.title())
        except Exception:
            return ""

    def _format_opened(self, page: Any) -> str:
        return f"opened {getattr(page, 'url', '')}\n"


class RequestHandler(BaseHTTPRequestHandler):
    runtime: CamoufoxRuntime

    def do_GET(self) -> None:
        if urlparse(self.path).path != "/health":
            self._write(404, {"error": "not found"})
            return
        self._write(200, {"status": "ok"})

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/call":
            self._write(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("content-length") or "0")
            payload = json.loads(self.rfile.read(length).decode() or "{}")
            name = str(payload.get("name") or "")
            if name == "stop":
                result = self.runtime.stop()
                self._write(200, {"result": result})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            result = self.runtime.call(name, payload.get("args") or {})
        except Exception as exc:
            self._write(500, {"error": str(exc)})
            return
        self._write(200, {"result": result})

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _write(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def normalize_text(value: str) -> str:
    return " ".join(value.split())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=int(os.environ.get("SURF_AGENT_CAMOUFOX_PORT", "9345")))
    parser.add_argument("--profile-dir", default=os.environ.get("SURF_AGENT_CAMOUFOX_PROFILE_DIR", ""))
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args(argv)
    profile_dir = Path(args.profile_dir).expanduser() if args.profile_dir else Path.cwd() / "camoufox-profile"
    RequestHandler.runtime = CamoufoxRuntime(profile_dir=profile_dir, headless=args.headless)
    # Playwright/Camoufox sync objects are bound to the thread that created them.
    # Use a single-threaded HTTP server so every browser call runs on one thread.
    server = HTTPServer(("127.0.0.1", args.port), RequestHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 130
    finally:
        RequestHandler.runtime.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
