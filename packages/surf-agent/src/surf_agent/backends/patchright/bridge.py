from __future__ import annotations

import argparse
import asyncio
import fnmatch
import inspect
import json
import os
import sys
import time
from http.server import HTTPServer
from pathlib import Path

from platformdirs import PlatformDirs
from typing import Any
from ...constants import DEFAULT_PATCHRIGHT_APP_ID, DEFAULT_WAIT_TIMEOUT_MS, PATCHRIGHT_BACKEND
from ...errors import ErrorCode
from .constants import CONTEXT_RESTART_REQUIRED
from .launch_args import STARTUP_PAGE_ARG, capture_default_args
from ..bridge_common import (
    CLOSED_TARGET_MESSAGE,
    NATIVE_ARIA_REF_PATTERN,
    SNAPSHOT_BOXES,
    SNAPSHOT_DEPTH,
    STALE_REF_MESSAGE,
    BridgeCodedError,
    BridgeRequestHandler,
    PageSlot,
    bridge_health_payload,
)

SNAPSHOT_ARIA_TIMEOUT_MS = 3_000
SNAPSHOT_BODY_TIMEOUT_MS = 3_000
# Must stay below the client's transport timeout (15s default) so a blocked
# action returns a classified error instead of an unknown-outcome timeout.
ACTION_TIMEOUT_MS = 5_000
# Short: probes run after the action already waited ACTION_TIMEOUT_MS.
ACTIONABILITY_PROBE_TIMEOUT_MS = 250
WAIT_POLL_INTERVAL_S = 0.1
# Returns a description of the element receiving pointer events at the target's
# center, or null when the target (or its shadow content) receives them itself.
HIT_TEST_BLOCKER_JS = """element => {
  const box = element.getBoundingClientRect();
  const x = box.left + box.width / 2;
  const y = box.top + box.height / 2;
  if (x < 0 || y < 0 || x > innerWidth || y > innerHeight) return null;
  const hit = document.elementFromPoint(x, y);
  if (!hit || element.contains(hit) || hit.shadowRoot?.contains(element)) return null;
  const id = hit.id ? `#${hit.id}` : '';
  const classes = [...hit.classList].map(name => `.${name}`).join('');
  const text = (hit.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 60);
  return `<${hit.tagName.toLowerCase()}${id}${classes}>` + (text ? ` "${text}"` : '');
}"""
CDP_NEW_WINDOW_TIMEOUT_S = 3.0
CDP_NEW_WINDOW_POLL_INTERVAL_S = 0.05
# Linux v11 cookies require Chrome’s real OS password store/keychain, not Patchright automation defaults.
PATCHRIGHT_INCOMPATIBLE_DEFAULT_ARGS = ("--password-store=basic", "--use-mock-keychain")
# Chrome starts without a window: on macOS showing one activates Chrome and steals
# focus. Thread windows are created later, in the background.
NO_STARTUP_WINDOW_ARG = "--no-startup-window"
# The windowless launch relies on Patchright skipping its first-page wait; if a
# Patchright release stops skipping it, fail after this instead of hanging.
LAUNCH_TIMEOUT_MS = 30_000

async_playwright: Any = None
PlaywrightTimeoutError: type[Exception] | None = None
try:
    from patchright import async_api as patchright_async_api
except ImportError:
    pass
else:
    async_playwright = patchright_async_api.async_playwright
    PlaywrightTimeoutError = patchright_async_api.TimeoutError


class PatchrightRuntime:
    def __init__(self, *, profile_dir: Path, headless: bool = False, app_id: str = DEFAULT_PATCHRIGHT_APP_ID, window_class: str | None = None) -> None:
        self.profile_dir = profile_dir
        self.headless = headless
        self.app_id = app_id
        self.window_class = window_class or app_id
        self.manager: Any | None = None
        self.browser_or_context: Any | None = None
        self.pages: dict[str, PageSlot] = {}
        self._next_page_id = 1
        self._runner: asyncio.Runner | None = None
        self.shutdown_requested = False
        self._close_shutdown_pending = False
        self.restart_requested = False

    def health_payload(self) -> dict[str, object]:
        return bridge_health_payload(PATCHRIGHT_BACKEND, self.profile_dir)

    def start(self) -> None:
        self._run(self._start_async())

    def stop(self) -> str:
        result = self._run(self._stop_async())
        self._close_runner()
        return result

    def call(self, name: str, args: dict[str, Any]) -> str:
        try:
            result = self._run(self._call_async(name, args))
        except BridgeCodedError:
            raise
        except Exception as exc:
            if self._is_closed_target_error(exc):
                raise BridgeCodedError(ErrorCode.PAGE_CLOSED, str(exc)) from exc
            raise
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
        self._cancel_shutdown_request()
        launch_options = {
            "user_data_dir": str(self.profile_dir),
            "headless": self.headless,
            "no_viewport": True,
            # Patchright otherwise emulates light mode instead of honoring the desktop theme.
            "color_scheme": "null",
            "chromium_sandbox": True,
        }
        # Patchright opens a startup page and waits for it; the wait is skipped only when
        # it adds no args of its own, so pass its own flags back without that page.
        # Remove this workaround (capture_default_args, ignore_default_args) once Patchright
        # launches with --no-startup-window without hanging; requested upstream at
        # https://redirect.github.com/Kaliiiiiiiiii-Vinyzu/patchright/discussions/232
        patchright_args = await capture_default_args(playwright.chromium, **launch_options)
        chrome_args = [
            arg for arg in patchright_args
            if arg != STARTUP_PAGE_ARG and arg not in PATCHRIGHT_INCOMPATIBLE_DEFAULT_ARGS
        ]
        self.browser_or_context = await self._maybe_await(
            playwright.chromium.launch_persistent_context(
                **launch_options,
                # Chrome channel keeps existing Chrome profile behavior; bundled browsers break that reuse.
                channel="chrome",
                ignore_default_args=True,
                timeout=LAUNCH_TIMEOUT_MS,
                args=[*chrome_args, *launch_args, NO_STARTUP_WINDOW_ARG],
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
        self._cancel_shutdown_request()
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
        if name == "rename-thread":
            destination_thread = args.get("destination_thread")
            if not isinstance(destination_thread, str) or not destination_thread:
                raise RuntimeError("rename-thread requires destination_thread")
            if thread == destination_thread:
                if thread not in self.pages:
                    raise RuntimeError(f"thread not found: {thread}")
                return f"renamed {destination_thread}\n"
            if thread not in self.pages:
                raise RuntimeError(f"thread not found: {thread}")
            if destination_thread in self.pages:
                raise RuntimeError(f"thread already exists: {destination_thread}")
            self.pages[destination_thread] = self.pages.pop(thread)
            return f"renamed {destination_thread}\n"
        if name == "close":
            old = self.pages.pop(thread, None)
            if old:
                await self._maybe_await(old.page.close())
            # The HTTP handler arms this only after its success response is written.
            self._close_shutdown_pending = True
            return "closed\n"
        if name == "close-matching":
            pattern = str(args.get("pattern") or "").strip()
            if not pattern:
                raise RuntimeError("close-matching requires a thread glob pattern")
            result: dict[str, Any] = {"pattern": pattern, "closed": [], "failed": []}
            for managed_thread, slot in sorted(self.pages.items()):
                if not fnmatch.fnmatchcase(managed_thread, pattern):
                    continue
                item = {"thread": managed_thread, "page_id": slot.page_id}
                try:
                    await self._maybe_await(slot.page.close())
                except Exception:
                    result["failed"].append(item)
                else:
                    self.pages.pop(managed_thread, None)
                    result["closed"].append(item)
            # The HTTP handler arms this only after its success response is written.
            if not result["failed"]:
                self._close_shutdown_pending = True
            return json.dumps(result, sort_keys=True) + "\n"
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
            # BFCache restores do not emit DOMContentLoaded again. Wait for the
            # history commit, then inspect document readiness rather than waiting
            # for a lifecycle event that may already belong to the cached page.
            await self._maybe_await(slot.page.go_back(wait_until="commit"))
            await self._maybe_await(slot.page.wait_for_function(
                "document.readyState === 'complete' || "
                "(performance.getEntriesByType('navigation')[0]?.domContentLoadedEventEnd ?? 0) > 0"
            ))
            return self._format_opened(slot.page)
        if name == "text":
            target = args.get("target")
            if target is None:
                return await self._body_text(slot.page)
            locator = await self._target_locator(slot, str(target))
            text = await self._maybe_await(locator.inner_text(timeout=ACTION_TIMEOUT_MS))
            return text + ("" if text.endswith("\n") else "\n")
        if name == "snapshot":
            return await self._snapshot(slot)
        if name == "click":
            target = str(args["uid"])
            locator = await self._target_locator(slot, target)
            await self._act(target, locator, lambda: locator.click(timeout=ACTION_TIMEOUT_MS), editable=False)
            return "clicked\n"
        if name == "fill":
            target = str(args["uid"])
            locator = await self._target_locator(slot, target)
            text = str(args.get("text") or "")
            await self._act(target, locator, lambda: locator.fill(text, timeout=ACTION_TIMEOUT_MS), editable=True)
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
            await self._maybe_await(slot.page.wait_for_timeout(float(args["target"])))
            return "waited\n"
        if name == "wait-for":
            await self._wait_for(
                slot.page,
                text=args.get("text"),
                gone=args.get("gone"),
                url=args.get("url"),
                timeout_ms=int(args.get("timeoutMs") or DEFAULT_WAIT_TIMEOUT_MS),
            )
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
        if name not in {"close", "close-matching"} or not self._close_shutdown_pending:
            return
        self._close_shutdown_pending = False
        if self._visible_pages():
            return
        self.shutdown_requested = True

    def service_actions(self) -> bool:
        if not self.shutdown_requested:
            return False
        if self._visible_pages():
            self._cancel_shutdown_request()
            return False
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

    def _cancel_shutdown_request(self) -> None:
        self.shutdown_requested = False
        self._close_shutdown_pending = False

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
        self._cancel_shutdown_request()
        old = self.pages.pop(thread, None)
        if old and self._page_is_open(old.page):
            await self._maybe_await(old.page.close())
        target_url = str(url or "about:blank")

        try:
            page = await self._create_new_window_page(target_url)
        except Exception as exc:
            if not self._is_closed_target_error(exc):
                raise
            # Manual window close can close the whole persistent context; recreate it.
            await self._restart_closed_context()
            page = await self._create_new_window_page(target_url)
        slot = PageSlot(page=page, page_id=self._next_page_id)
        self._next_page_id += 1
        self.pages[thread] = slot
        return slot

    async def _create_new_window_page(self, url: str) -> Any:
        context = self._context()
        excluded_page_ids = {id(page) for page in self._open_managed_pages()}
        session = None
        target_id: str | None = None
        try:
            await self._close_unmanaged_pages(context)
            # Browser-level: a page-level session needs an open page, and opening one
            # while Chrome has no window shows it in the foreground.
            session = await self._maybe_await(context.browser.new_browser_cdp_session())
            # Background: a foreground window activates Chrome and steals focus from the
            # user's app (measured on macOS). Thread.focus() raises a window on request.
            response = await self._maybe_await(
                session.send("Target.createTarget", {"url": url, "newWindow": True, "background": True})
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

    def _open_managed_pages(self) -> list[Any]:
        return [slot.page for slot in self.pages.values() if self._page_is_open(slot.page)]

    async def _close_unmanaged_pages(self, context: Any, *, keep_ids: set[int] | None = None) -> None:
        keep_ids = keep_ids or set()
        managed_ids = {id(page) for page in self._open_managed_pages()}
        for page in list(context.pages):
            if id(page) in keep_ids or id(page) in managed_ids or not self._page_is_open(page):
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

    def _page_url(self, page: Any) -> str:
        return str(getattr(page, "url", "") or "")

    async def _restart_closed_context(self) -> None:
        # This runtime cannot safely invoke the CLI process's lifecycle coordinator:
        # Patchright objects are thread-affine and this is the bridge server thread.
        # Stop and request a fresh bridge process so LocalBridgeClient.before_start
        # performs the cookie preflight before the next persistent-context launch.
        self._cancel_shutdown_request()
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

    async def _act(self, target: str, locator: Any, action: Any, *, editable: bool) -> None:
        try:
            await self._maybe_await(action())
        except Exception as exc:
            if PlaywrightTimeoutError is None or not isinstance(exc, PlaywrightTimeoutError):
                raise
            raise await self._actionability_error(target, locator, editable=editable) from exc

    async def _actionability_error(self, target: str, locator: Any, *, editable: bool) -> BridgeCodedError:
        """Classify a timed-out action by probing the element's current state."""
        probe_ms = ACTIONABILITY_PROBE_TIMEOUT_MS
        if await self._maybe_await(locator.count()) == 0:
            if self._is_ref_target(target):
                return BridgeCodedError(ErrorCode.STALE_REF, STALE_REF_MESSAGE.format(ref=target.removeprefix("@")))
            return BridgeCodedError(ErrorCode.NOT_FOUND, f"target {target!r} no longer matches any element")
        if not await self._maybe_await(locator.is_visible()):
            return BridgeCodedError(ErrorCode.NOT_VISIBLE, f"target {target!r} is not visible")
        if not await self._maybe_await(locator.is_enabled(timeout=probe_ms)):
            return BridgeCodedError(ErrorCode.NOT_ENABLED, f"target {target!r} is disabled")
        if editable and not await self._maybe_await(locator.is_editable(timeout=probe_ms)):
            return BridgeCodedError(ErrorCode.NOT_EDITABLE, f"target {target!r} is not editable")
        blocker = await self._maybe_await(locator.evaluate(HIT_TEST_BLOCKER_JS, timeout=probe_ms))
        if blocker:
            return BridgeCodedError(
                ErrorCode.INTERCEPTED, f"target {target!r} is covered by {blocker}; dismiss or target that element"
            )
        return BridgeCodedError(
            ErrorCode.ACTION_TIMEOUT, f"target {target!r} looked actionable but the action did not complete in {ACTION_TIMEOUT_MS}ms"
        )

    async def _wait_for(
        self, page: Any, *, text: str | None, gone: str | None, url: str | None, timeout_ms: int
    ) -> None:
        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            unmet = await self._unmet_conditions(page, text=text, gone=gone, url=url)
            if not unmet:
                return
            if time.monotonic() >= deadline:
                raise BridgeCodedError(
                    ErrorCode.WAIT_TIMEOUT,
                    f"wait timed out after {timeout_ms}ms: {'; '.join(unmet)}; "
                    f"page url {self._page_url(page)!r}, title {await self._title(page)!r}",
                )
            await asyncio.sleep(WAIT_POLL_INTERVAL_S)

    async def _unmet_conditions(self, page: Any, *, text: str | None, gone: str | None, url: str | None) -> list[str]:
        unmet: list[str] = []
        if text is not None and await self._visible_text_count(page, text) == 0:
            unmet.append(f"text {text!r} not visible")
        if gone is not None and await self._visible_text_count(page, gone) > 0:
            unmet.append(f"text {gone!r} still visible")
        if url is not None and not fnmatch.fnmatchcase(self._page_url(page), url):
            unmet.append(f"url does not match {url!r}")
        return unmet

    async def _visible_text_count(self, page: Any, text: str) -> int:
        return await self._maybe_await(page.get_by_text(text).filter(visible=True).count())

    def _is_ref_target(self, target: str) -> bool:
        return target.startswith("@") or NATIVE_ARIA_REF_PATTERN.fullmatch(target) is not None

    async def _target_locator(self, slot: PageSlot, target: str) -> Any:
        normalized = target[1:] if target.startswith("@") else target
        is_native_ref = NATIVE_ARIA_REF_PATTERN.fullmatch(normalized) is not None
        if target.startswith("@") and not is_native_ref:
            raise BridgeCodedError(ErrorCode.STALE_REF, STALE_REF_MESSAGE.format(ref=normalized))
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
            raise BridgeCodedError(ErrorCode.STALE_REF, STALE_REF_MESSAGE.format(ref=ref)) from exc

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
            raise BridgeCodedError(
                ErrorCode.NOT_FOUND, f"target {selector!r} is neither a current snapshot ref nor a matching selector"
            ) from exc

    async def _metadata(self, slot: PageSlot) -> dict[str, str | int]:
        page = slot.page
        return {"page_id": slot.page_id, "url": str(getattr(page, "url", "") or ""), "title": await self._title(page)}

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
