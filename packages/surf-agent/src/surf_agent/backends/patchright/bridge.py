from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import os
import sys
import time
from http.server import HTTPServer
from pathlib import Path

from platformdirs import PlatformDirs
from typing import Any
from ...constants import DEFAULT_PATCHRIGHT_APP_ID
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

SNAPSHOT_ARIA_TIMEOUT_MS = 3_000
SNAPSHOT_BODY_TIMEOUT_MS = 3_000
CDP_NEW_WINDOW_TIMEOUT_S = 3.0
CDP_NEW_WINDOW_POLL_INTERVAL_S = 0.05
CONTEXT_RESTART_REQUIRED = "Patchright persistent context closed; bridge restart required"
# Linux v11 cookies require Chrome’s real OS password store/keychain, not Patchright automation defaults.
PATCHRIGHT_INCOMPATIBLE_DEFAULT_ARGS = ("--password-store=basic", "--use-mock-keychain")

try:
    from patchright.async_api import async_playwright
except ImportError:
    async_playwright = None


class PatchrightRuntime:
    def __init__(self, *, profile_dir: Path, headless: bool = False, app_id: str = DEFAULT_PATCHRIGHT_APP_ID, window_class: str | None = None, clock: Any = time.monotonic) -> None:
        self.profile_dir = profile_dir
        self.headless = headless
        self.app_id = app_id
        self.window_class = window_class or app_id
        self.manager: Any | None = None
        self.browser_or_context: Any | None = None
        self.pages: dict[str, PageSlot] = {}
        self._next_page_token = 1
        self._runner: asyncio.Runner | None = None
        self.clock = clock
        self.idle_shutdown_deadline: float | None = None
        self._idle_close_pending = False
        self.restart_requested = False

    def start(self) -> None:
        self._run(self._start_async())

    def stop(self) -> str:
        result = self._run(self._stop_async())
        self._close_runner()
        return result

    def call(self, name: str, args: dict[str, Any]) -> str:
        result = self._run(self._call_async(name, args))
        if name == "stop":
            self._close_runner()
        return result

    def _run(self, awaitable: Any) -> Any:
        if self._runner is None:
            self._runner = asyncio.Runner()
        try:
            return self._runner.run(awaitable)
        finally:
            if self.manager is None:
                # Pure helper calls used by tests never start a browser, but creating
                # an asyncio.Runner still opens loop resources. Release them once idle.
                self._close_runner()

    def _close_runner(self) -> None:
        if self._runner is None:
            return
        self._runner.close()
        self._runner = None

    async def _maybe_await(self, value: Any) -> Any:
        if inspect.isawaitable(value):
            return await value
        return value

    async def _start_async(self) -> None:
        if self.browser_or_context is not None:
            return
        if async_playwright is None:
            raise RuntimeError("Patchright is not installed. Run `uv tool install \"surf-agent[patchright] @ git+https://github.com/ewgdg/browser-skills.git#subdirectory=packages/surf-agent\"`, install Google Chrome yourself, and set SURF_AGENT_CHROME_BIN if Chrome is not on PATH.")
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        launch_args = [f"--class={self.window_class}", f"--name={self.app_id}"] if self.app_id or self.window_class else []
        self.manager = async_playwright()
        if hasattr(self.manager, "__aenter__"):
            playwright = await self.manager.__aenter__()
        else:
            playwright = self.manager.__enter__()
        # Chrome channel keeps existing Chrome profile behavior; bundled browsers break that reuse.
        self._cancel_idle_shutdown()
        self.browser_or_context = await self._maybe_await(
            playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                channel="chrome",
                headless=self.headless,
                no_viewport=True,
                # Patchright otherwise emulates light mode instead of honoring the desktop theme.
                color_scheme="null",
                chromium_sandbox=True,
                args=launch_args,
                ignore_default_args=PATCHRIGHT_INCOMPATIBLE_DEFAULT_ARGS,
            )
        )

    async def _stop_async(self) -> str:
        if self.manager is not None:
            if hasattr(self.manager, "__aexit__"):
                await self._maybe_await(self.manager.__aexit__(None, None, None))
            else:
                self.manager.__exit__(None, None, None)
        self.manager = None
        self.browser_or_context = None
        self.pages.clear()
        self._cancel_idle_shutdown()
        return "stopped\n"

    async def _call_async(self, name: str, args: dict[str, Any]) -> str:
        if name == "stop":
            return await self._stop_async()
        thread = str(args.get("thread") or "default")
        if name == "state":
            slot = self.pages.get(thread)
            if not slot:
                return json.dumps({"backend": "patchright", "open": False, "thread": thread}) + "\n"
            return json.dumps({"backend": "patchright", "open": True, "thread": thread, **(await self._metadata(slot))}) + "\n"
        if name == "list":
            rows = [{"thread": key, **(await self._metadata(slot))} for key, slot in sorted(self.pages.items())]
            return json.dumps({"backend": "patchright", "pages": rows}, sort_keys=True) + "\n"
        if name == "close":
            old = self.pages.pop(thread, None)
            if old:
                await self._maybe_await(old.page.close())
            # The HTTP handler arms this only after its success response is written.
            self._idle_close_pending = True
            return "closed\n"
        if name == "scroll" and str(args.get("direction") or "down") not in {"up", "down", "top", "bottom"}:
            raise RuntimeError("scroll requires direction: up, down, top, or bottom")
        await self._start_async()
        if name == "new":
            url = str(args.get("url") or "about:blank")
            slot = await self._new_page(thread, url=url)
            return self._format_opened(slot.page)
        if name == "open":
            url = str(args["url"])
            existing = self.pages.get(thread)
            if not existing or not self._page_is_open(existing.page):
                slot = await self._new_page(thread, url=url)
                return self._format_opened(slot.page)

            async def open_page(slot: PageSlot) -> str:
                await self._maybe_await(slot.page.goto(url, wait_until="domcontentloaded"))
                return self._format_opened(slot.page)

            try:
                return await open_page(existing)
            except Exception as exc:
                if not self._is_closed_target_error(exc):
                    raise
                slot = await self._new_page(thread, url=url)
                return self._format_opened(slot.page)
        slot = await self._page(thread)
        if name == "back":
            await self._maybe_await(slot.page.go_back(wait_until="domcontentloaded"))
            return self._format_opened(slot.page)
        if name == "text":
            return await self._body_text(slot.page)
        if name == "snapshot":
            return await self._snapshot(slot)
        if name == "click":
            locator = await self._target_locator(slot, str(args["uid"]))
            await self._maybe_await(locator.click())
            return "clicked\n"
        if name == "fill":
            locator = await self._target_locator(slot, str(args["uid"]))
            await self._maybe_await(locator.fill(str(args.get("text") or "")))
            return "filled\n"
        if name == "type":
            await self._maybe_await(slot.page.keyboard.type(str(args.get("text") or "")))
            return "typed\n"
        if name == "press":
            await self._maybe_await(slot.page.keyboard.press(str(args.get("key") or "Enter")))
            return "pressed\n"
        if name == "scroll":
            direction = str(args.get("direction") or "down")
            if direction not in {"up", "down", "top", "bottom"}:
                raise RuntimeError("scroll requires direction: up, down, top, or bottom")
            delta = -700 if direction in {"up", "top"} else 700
            if direction == "top":
                await self._maybe_await(slot.page.evaluate("() => window.scrollTo(0, 0)"))
            elif direction == "bottom":
                await self._maybe_await(slot.page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)"))
            else:
                await self._maybe_await(slot.page.mouse.wheel(0, delta))
            return "scrolled\n"
        if name == "wait":
            target = args.get("target")
            if isinstance(target, (int, float)):
                await self._maybe_await(slot.page.wait_for_timeout(float(target)))
            else:
                await self._maybe_await(slot.page.get_by_text(str(target)).first.wait_for(timeout=10_000))
            return "waited\n"
        if name == "screenshot":
            path = str(args["path"])
            full_page = args.get("fullPage") is True
            await self._maybe_await(slot.page.screenshot(path=path, full_page=full_page))
            return f"screenshot: {path}\n"
        if name == "eval":
            result = await self._maybe_await(slot.page.evaluate(str(args.get("code") or "")))
            return json.dumps(result, ensure_ascii=False) + "\n"
        if name == "focus":
            await self._maybe_await(slot.page.bring_to_front())
            return "focused\n"
        raise RuntimeError(f"unsupported Patchright command: {name}")

    def after_response(self, name: str) -> None:
        if name != "close" or not self._idle_close_pending:
            return
        self._idle_close_pending = False
        if self._visible_pages():
            return
        self.idle_shutdown_deadline = self.clock() + 2.0

    def service_actions(self) -> bool:
        deadline = self.idle_shutdown_deadline
        if deadline is None or self.clock() < deadline:
            return False
        if self._visible_pages():
            self._cancel_idle_shutdown()
            return False
        self.idle_shutdown_deadline = None
        try:
            self.stop()
        except Exception as exc:
            # The close request already succeeded; preserve the runtime on stop failure.
            print(f"surf-agent: warning: could not stop idle Patchright bridge: {exc}", file=sys.stderr)
            return False
        return True

    def _visible_pages(self) -> list[Any]:
        if self.browser_or_context is None or not hasattr(self.browser_or_context, "pages"):
            return []
        return [page for page in list(self.browser_or_context.pages) if self._page_is_open(page)]

    def _cancel_idle_shutdown(self) -> None:
        self.idle_shutdown_deadline = None
        self._idle_close_pending = False

    def _context(self) -> Any:
        if self.browser_or_context is None:
            raise RuntimeError("Patchright runtime is not started")
        # persistent_context=True returns BrowserContext. Non-persistent would return Browser.
        if hasattr(self.browser_or_context, "new_page") and hasattr(self.browser_or_context, "pages"):
            return self.browser_or_context
        return self.browser_or_context.new_context()

    async def _page(self, thread: str) -> PageSlot:
        slot = self.pages.get(thread)
        if slot and self._page_is_open(slot.page):
            return slot
        return await self._new_page(thread)

    async def _new_page(self, thread: str, url: str | None = None) -> PageSlot:
        self._cancel_idle_shutdown()
        old = self.pages.pop(thread, None)
        if old and self._page_is_open(old.page):
            await self._maybe_await(old.page.close())
        target_url = str(url or "about:blank")

        async def create_page() -> Any:
            initial_page = await self._adopt_initial_page(target_url)
            if initial_page is not None:
                return initial_page
            return await self._create_new_window_page(target_url)

        try:
            page = await create_page()
        except Exception as exc:
            if not self._is_closed_target_error(exc):
                raise
            # Manual window close can close the whole persistent context; recreate it.
            await self._restart_closed_context()
            page = await create_page()
        slot = PageSlot(page=page, page_token=self._next_page_token)
        self._next_page_token += 1
        self.pages[thread] = slot
        return slot

    async def _adopt_initial_page(self, url: str) -> Any | None:
        if self._open_owned_pages():
            return None
        context = self._context()
        open_pages = [page for page in list(context.pages) if self._page_is_open(page)]
        if not open_pages:
            return None
        # The first thread reuses one launch page to avoid a visible window swap; later
        # threads require CDP target IDs so concurrent windows cannot be misidentified.
        page = next((page for page in open_pages if self._page_url(page) in STARTUP_PAGE_URLS), open_pages[0])
        await self._close_unowned_pages(context, keep_ids={id(page)})
        if not self._page_is_open(page):
            raise RuntimeError(CLOSED_TARGET_MESSAGE)
        try:
            await self._maybe_await(page.goto(url, wait_until="domcontentloaded"))
        except Exception as exc:
            if not self._page_is_open(page):
                raise RuntimeError(CLOSED_TARGET_MESSAGE) from exc
            raise
        if not self._page_is_open(page):
            raise RuntimeError(CLOSED_TARGET_MESSAGE)
        return page

    async def _create_new_window_page(self, url: str) -> Any:
        context = self._context()
        anchor_page, close_anchor = await self._cdp_anchor_page(context)
        excluded_page_ids = {id(page) for page in self._open_owned_pages()}
        excluded_page_ids.add(id(anchor_page))
        session = None
        target_id: str | None = None
        try:
            await self._close_unowned_pages(context, keep_ids={id(anchor_page)})
            session = await self._maybe_await(context.new_cdp_session(anchor_page))
            response = await self._maybe_await(
                session.send("Target.createTarget", {"url": url, "newWindow": True, "background": False})
            )
            target_id = response.get("targetId") if isinstance(response, dict) else None
            if not isinstance(target_id, str) or not target_id:
                raise RuntimeError(f"Patchright CDP Target.createTarget returned no valid target for {url!r}")
            try:
                return await self._wait_for_created_target_page(context, url, excluded_page_ids, target_id)
            except Exception:
                await self._best_effort_close_target(session, target_id)
                raise
        finally:
            await self._best_effort_detach(session)
            if close_anchor:
                await self._best_effort_close_page(anchor_page)

    async def _cdp_anchor_page(self, context: Any) -> tuple[Any, bool]:
        owned_pages = self._open_owned_pages()
        if owned_pages:
            return owned_pages[0], False
        for page in list(context.pages):
            if self._page_is_open(page):
                return page, True
        # Patchright CDP sessions need a page anchor. This temporary page must never become
        # the controlled thread page; it is closed after Target.createTarget finishes.
        return await self._maybe_await(context.new_page()), True

    def _open_owned_pages(self) -> list[Any]:
        return [slot.page for slot in self.pages.values() if self._page_is_open(slot.page)]

    async def _close_unowned_pages(self, context: Any, *, keep_ids: set[int] | None = None) -> None:
        keep_ids = keep_ids or set()
        owned_ids = {id(page) for page in self._open_owned_pages()}
        for page in list(context.pages):
            if id(page) in keep_ids or id(page) in owned_ids or not self._page_is_open(page):
                continue
            try:
                await self._maybe_await(page.close())
            except Exception as exc:
                if not self._is_closed_target_error(exc):
                    raise

    async def _wait_for_created_target_page(
        self, context: Any, url: str, excluded_page_ids: set[int], target_id: str
    ) -> Any:
        deadline = time.monotonic() + CDP_NEW_WINDOW_TIMEOUT_S
        while time.monotonic() < deadline:
            candidates = [
                page
                for page in list(context.pages)
                if id(page) not in excluded_page_ids and self._page_is_open(page)
            ]
            for page in candidates:
                if await self._page_target_id(context, page) == target_id:
                    return page
            await asyncio.sleep(CDP_NEW_WINDOW_POLL_INTERVAL_S)
        raise RuntimeError(
            f"Patchright CDP Target.createTarget did not expose target {target_id!r} for {url!r}"
        )

    async def _page_target_id(self, context: Any, page: Any) -> str | None:
        session = None
        try:
            session = await self._maybe_await(context.new_cdp_session(page))
            response = await self._maybe_await(session.send("Target.getTargetInfo"))
            target_id = response["targetInfo"]["targetId"]
            if not isinstance(target_id, str) or not target_id:
                raise RuntimeError("Patchright CDP Target.getTargetInfo returned no valid targetId")
            if not self._page_is_open(page):
                return None
            return target_id
        except Exception as exc:
            if self._is_closed_target_error(exc) or not self._page_is_open(page):
                return None
            raise
        finally:
            await self._best_effort_detach(session)

    async def _best_effort_close_target(self, session: Any, target_id: str) -> None:
        try:
            await self._maybe_await(session.send("Target.closeTarget", {"targetId": target_id}))
        except Exception:
            return

    async def _best_effort_detach(self, session: Any | None) -> None:
        if session is None:
            return
        detach = getattr(session, "detach", None)
        if detach is None:
            return
        try:
            await self._maybe_await(detach())
        except Exception:
            return

    async def _best_effort_close_page(self, page: Any) -> None:
        try:
            if self._page_is_open(page):
                await self._maybe_await(page.close())
        except Exception:
            return

    def _page_url(self, page: Any) -> str:
        return str(getattr(page, "url", "") or "")

    async def _restart_closed_context(self) -> None:
        # This runtime cannot safely invoke the CLI process's lifecycle coordinator:
        # Patchright objects are thread-affine and this is the bridge server thread.
        # Stop and request a fresh bridge process so LocalBridgeClient.before_start
        # performs the cookie preflight before the next persistent-context launch.
        self._cancel_idle_shutdown()
        try:
            await self._stop_async()
        except Exception:
            self.manager = None
            self.browser_or_context = None
            self.pages.clear()
        self.restart_requested = True
        raise RuntimeError(CONTEXT_RESTART_REQUIRED)

    def _page_is_open(self, page: Any) -> bool:
        try:
            return not page.is_closed()
        except Exception as exc:
            if self._is_closed_target_error(exc):
                return False
            raise

    def _is_closed_target_error(self, exc: Exception) -> bool:
        return CLOSED_TARGET_MESSAGE in str(exc)

    async def _body_text(self, page: Any) -> str:
        try:
            text = await self._maybe_await(page.locator("body").inner_text(timeout=SNAPSHOT_BODY_TIMEOUT_MS))
        except Exception:
            text = await self._maybe_await(page.content())
        return text + ("" if text.endswith("\n") else "\n")

    async def _snapshot(self, slot: PageSlot) -> str:
        page = slot.page
        parts = ["snapshot:"]
        try:
            aria = await self._aria_snapshot(page)
        except Exception:
            try:
                aria = await self._aria_snapshot(page.locator("body"))
            except Exception:
                aria = (await self._body_text(page)).strip()
        if aria:
            parts.append(str(aria).rstrip())
        return "\n".join(parts).rstrip() + "\n"

    async def _aria_snapshot(self, target: Any) -> str:
        # Match Playwright CLI snapshots: AI-mode ARIA tree, optional depth, optional boxes.
        options = {
            "mode": "ai",
            "timeout": SNAPSHOT_ARIA_TIMEOUT_MS,
            "depth": SNAPSHOT_DEPTH,
            "boxes": SNAPSHOT_BOXES,
        }
        try:
            return str(await self._maybe_await(target.aria_snapshot(**options)))
        except TypeError:
            try:
                return str(await self._maybe_await(target.aria_snapshot(mode="ai", timeout=SNAPSHOT_ARIA_TIMEOUT_MS)))
            except TypeError:
                return str(await self._maybe_await(target.aria_snapshot(timeout=SNAPSHOT_ARIA_TIMEOUT_MS)))

    async def _target_locator(self, slot: PageSlot, target: str) -> Any:
        normalized = target[1:] if target.startswith("@") else target
        is_native_ref = NATIVE_ARIA_REF_PATTERN.fullmatch(normalized) is not None
        if target.startswith("@") and not is_native_ref:
            raise RuntimeError(STALE_REF_MESSAGE.format(ref=normalized))
        if is_native_ref:
            return await self._ref_locator(slot, normalized)
        return await self._selector_locator(slot, target)

    async def _ref_locator(self, slot: PageSlot, ref: str) -> Any:
        try:
            locator = slot.page.locator(f"aria-ref={ref}")
            if await self._maybe_await(locator.count()) != 1:
                raise RuntimeError
            return locator
        except Exception as exc:
            raise RuntimeError(STALE_REF_MESSAGE.format(ref=ref)) from exc

    async def _locator_candidates(self, locator: Any, *, limit: int) -> list[Any]:
        candidates: list[Any] = []
        try:
            count = min(await self._maybe_await(locator.count()), limit)
        except Exception:
            return candidates
        for index in range(count):
            try:
                candidates.append(locator.nth(index))
            except Exception:
                continue
        return candidates

    async def _selector_locator(self, slot: PageSlot, selector: str) -> Any:
        try:
            locator = slot.page.locator(selector)
            if await self._maybe_await(locator.count()) < 1:
                raise RuntimeError
            candidate = locator.first
            if hasattr(candidate, "is_visible") and not await self._maybe_await(candidate.is_visible(timeout=250)):
                for item in await self._locator_candidates(locator, limit=50):
                    if await self._maybe_await(item.is_visible(timeout=250)):
                        return item
            return candidate
        except Exception as exc:
            raise RuntimeError(f"target {selector!r} is neither a current snapshot ref nor a matching selector") from exc

    async def _metadata(self, slot: PageSlot) -> dict[str, str | int]:
        page = slot.page
        return {"page_id": slot.page_token, "url": str(getattr(page, "url", "") or ""), "title": await self._title(page)}

    async def _title(self, page: Any) -> str:
        try:
            return str(await self._maybe_await(page.title()))
        except Exception:
            return ""

    def _format_opened(self, page: Any) -> str:
        return f"opened {getattr(page, 'url', '')}\n"


class RequestHandler(BridgeRequestHandler):
    runtime: Any


class PatchrightHTTPServer(HTTPServer):
    def service_actions(self) -> None:
        runtime = RequestHandler.runtime
        if runtime.restart_requested or runtime.service_actions():
            self._BaseServer__shutdown_request = True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=int(os.environ.get("SURF_AGENT_PATCHRIGHT_PORT", "9346")))
    parser.add_argument(
        "--profile-dir",
        default=os.environ.get("SURF_AGENT_PATCHRIGHT_PROFILE_DIR") or os.environ.get("SURF_AGENT_CHROME_PROFILE_DIR") or os.environ.get("CHROME_DEVTOOLS_AXI_USER_DATA_DIR") or "",
    )
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--app-id", default=os.environ.get("SURF_AGENT_PATCHRIGHT_APP_ID") or os.environ.get("SURF_AGENT_PATCHRIGHT_CLASS") or DEFAULT_PATCHRIGHT_APP_ID)
    parser.add_argument("--class", dest="window_class", default=os.environ.get("SURF_AGENT_PATCHRIGHT_CLASS") or os.environ.get("SURF_AGENT_PATCHRIGHT_APP_ID") or DEFAULT_PATCHRIGHT_APP_ID)
    args = parser.parse_args(argv)
    home = os.environ.get("SURF_AGENT_HOME")
    default_profile_dir = (Path(home).expanduser() if home else Path(PlatformDirs("surf-agent", appauthor=False).user_data_dir)) / "profiles" / "chrome"
    profile_dir = Path(args.profile_dir).expanduser() if args.profile_dir else default_profile_dir
    RequestHandler.runtime = PatchrightRuntime(profile_dir=profile_dir, headless=args.headless, app_id=args.app_id, window_class=args.window_class)
    # Playwright/Patchright sync objects are bound to the thread that created them.
    # Use a single-threaded HTTP server so every browser call runs on one thread.
    server = PatchrightHTTPServer(("127.0.0.1", args.port), RequestHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 130
    finally:
        RequestHandler.runtime.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
