from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import HTTPServer
from pathlib import Path

from platformdirs import PlatformDirs
from typing import Any

from ...constants import DEFAULT_CAMOUFOX_APP_ID
from ..bridge_common import (
    CLOSED_TARGET_MESSAGE,
    NATIVE_ARIA_REF_PATTERN,
    SNAPSHOT_BOXES,
    SNAPSHOT_DEPTH,
    STALE_REF_MESSAGE,
    STARTUP_PAGE_URLS,
    BridgeRequestHandler,
    PageSlot,
)

SNAPSHOT_ARIA_TIMEOUT_MS = 5_000
SNAPSHOT_BODY_TIMEOUT_MS = 5_000


class CamoufoxRuntime:
    def __init__(self, *, profile_dir: Path, headless: bool = False, app_id: str = DEFAULT_CAMOUFOX_APP_ID) -> None:
        self.profile_dir = profile_dir
        self.headless = headless
        self.app_id = app_id
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
            raise RuntimeError("Camoufox is not installed. Run `uv tool install \"surf-agent[camoufox] @ git+https://github.com/ewgdg/browser-skills.git#subdirectory=packages/surf-agent\"`, then manually run `python -m camoufox fetch`.") from exc
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        launch_args = [f"--class={self.app_id}", "--name", self.app_id] if self.app_id else []
        self.manager = Camoufox(persistent_context=True, user_data_dir=str(self.profile_dir), headless=self.headless, args=launch_args)
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
            return self._format_opened(slot.page)
        if name == "open":
            url = str(args["url"])

            def open_page(slot: PageSlot) -> str:
                slot.page.goto(url, wait_until="domcontentloaded")
                return self._format_opened(slot.page)

            return self._with_live_page(thread, open_page)
        slot = self._page(thread)
        if name == "back":
            slot.page.go_back(wait_until="domcontentloaded")
            return self._format_opened(slot.page)
        if name == "text":
            return self._body_text(slot.page)
        if name == "snapshot":
            return self._snapshot(slot)
        if name == "click":
            locator = self._target_locator(slot, str(args["uid"]))
            locator.click()
            return "clicked\n"
        if name == "fill":
            locator = self._target_locator(slot, str(args["uid"]))
            locator.fill(str(args.get("text") or ""))
            return "filled\n"
        if name == "type":
            slot.page.keyboard.type(str(args.get("text") or ""))
            return "typed\n"
        if name == "press":
            slot.page.keyboard.press(str(args.get("key") or "Enter"))
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
            return "scrolled\n"
        if name == "wait":
            target = args.get("target")
            if isinstance(target, (int, float)):
                slot.page.wait_for_timeout(float(target))
            else:
                slot.page.get_by_text(str(target)).first.wait_for(timeout=10_000)
            return "waited\n"
        if name == "screenshot":
            path = str(args["path"])
            full_page = args.get("fullPage") is True
            slot.page.screenshot(path=path, full_page=full_page)
            return f"screenshot: {path}\n"
        if name == "eval":
            result = slot.page.evaluate(str(args.get("code") or ""))
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
            text = page.locator("body").inner_text(timeout=SNAPSHOT_BODY_TIMEOUT_MS)
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
        return "\n".join(parts).rstrip() + "\n"

    def _aria_snapshot(self, target: Any) -> str:
        # Match Playwright CLI snapshots where Camoufox exposes compatible options.
        options = {
            "mode": "ai",
            "timeout": SNAPSHOT_ARIA_TIMEOUT_MS,
            "depth": SNAPSHOT_DEPTH,
            "boxes": SNAPSHOT_BOXES,
        }
        try:
            return str(target.aria_snapshot(**options))
        except TypeError:
            try:
                return str(target.aria_snapshot(mode="ai", timeout=SNAPSHOT_ARIA_TIMEOUT_MS))
            except TypeError:
                return str(target.aria_snapshot(timeout=SNAPSHOT_ARIA_TIMEOUT_MS))

    def _target_locator(self, slot: PageSlot, target: str) -> Any:
        normalized = target[1:] if target.startswith("@") else target
        is_native_ref = NATIVE_ARIA_REF_PATTERN.fullmatch(normalized) is not None
        if target.startswith("@") and not is_native_ref:
            raise RuntimeError(STALE_REF_MESSAGE.format(ref=normalized))
        if is_native_ref:
            return self._ref_locator(slot, normalized)
        return self._selector_locator(slot, target)

    def _ref_locator(self, slot: PageSlot, ref: str) -> Any:
        try:
            locator = slot.page.locator(f"aria-ref={ref}")
            if locator.count() != 1:
                raise RuntimeError
            return locator
        except Exception as exc:
            raise RuntimeError(STALE_REF_MESSAGE.format(ref=ref)) from exc

    def _locator_candidates(self, locator: Any, *, limit: int) -> list[Any]:
        candidates: list[Any] = []
        try:
            count = min(locator.count(), limit)
        except Exception:
            return candidates
        for index in range(count):
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


class RequestHandler(BridgeRequestHandler):
    runtime: Any


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=int(os.environ.get("SURF_AGENT_CAMOUFOX_PORT", "9345")))
    parser.add_argument(
        "--profile-dir",
        default=os.environ.get("SURF_AGENT_CAMOUFOX_PROFILE_DIR") or os.environ.get("SURF_AGENT_FIREFOX_PROFILE_DIR") or "",
    )
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--app-id", default=os.environ.get("SURF_AGENT_CAMOUFOX_APP_ID") or os.environ.get("SURF_AGENT_CAMOUFOX_CLASS") or DEFAULT_CAMOUFOX_APP_ID)
    args = parser.parse_args(argv)
    home = os.environ.get("SURF_AGENT_HOME")
    default_profile_dir = (Path(home).expanduser() if home else Path(PlatformDirs("surf-agent", appauthor=False).user_data_dir)) / "profiles" / "firefox"
    profile_dir = Path(args.profile_dir).expanduser() if args.profile_dir else default_profile_dir
    RequestHandler.runtime = CamoufoxRuntime(profile_dir=profile_dir, headless=args.headless, app_id=args.app_id)
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
