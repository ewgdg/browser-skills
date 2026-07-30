from __future__ import annotations

import asyncio
import json
import math
from typing import Any, Protocol

from ...owned_pages import (
    OwnedPageAttemptState,
    OwnedPageBridgeErrorCode,
    OwnedPageProtection,
    OwnedPageScope,
    owned_page_url_is_canonical_session,
    owned_page_url_is_in_scope,
)
from ..bridge_common import PageSlot


class PatchrightRetainedPageHost(Protocol):
    async def _maybe_await(self, value: Any) -> Any: ...

    def _page_is_open(self, page: Any) -> bool: ...

    def _page_url(self, page: Any) -> str: ...

    def _owned_page_slot(self, thread: str) -> PageSlot | None: ...

    def _owned_page_bindings(self) -> list[tuple[str, PageSlot]]: ...

    def _discard_owned_page_binding(self, thread: str) -> None: ...


class PatchrightRetainedPageOperations:
    """Enforce retained-page cleanup, capacity, and abandonment policy."""

    def __init__(self, runtime: PatchrightRetainedPageHost) -> None:
        self._runtime = runtime

    async def sweep(
        self,
        owner: str,
        program: str,
    ) -> list[dict[str, str]]:
        retained: list[dict[str, str]] = []
        for thread, slot in self._runtime._owned_page_bindings():
            if slot.owner != owner:
                continue
            if not self._runtime._page_is_open(slot.page):
                self._runtime._discard_owned_page_binding(thread)
                continue
            if slot.protection is not None:
                reason = (
                    "explicitly_retained"
                    if slot.protection == OwnedPageProtection.EXPLICITLY_RETAINED
                    else "human_intervention"
                )
                retained.append(self._retained_page(thread, slot, reason))
                continue

            before_url = self._runtime._page_url(slot.page)
            if not self._cleanup_url_is_recognized(before_url):
                retained.append(
                    self._retained_page(thread, slot, "inspection_failed")
                )
                continue
            state = await self._classify(slot, program)
            current_url = self._runtime._page_url(slot.page)
            if (
                self._runtime._owned_page_slot(thread) is not slot
                or not self._runtime._page_is_open(slot.page)
                or slot.owner != owner
                or slot.protection is not None
                or current_url != before_url
                or not self._cleanup_url_is_recognized(current_url)
            ):
                retained.append(
                    self._retained_page(thread, slot, "inspection_failed")
                )
                continue
            if state in {
                OwnedPageAttemptState.COMPLETED.value,
                OwnedPageAttemptState.STOPPED.value,
                OwnedPageAttemptState.FAILED.value,
            }:
                try:
                    await self._runtime._maybe_await(slot.page.close())
                except Exception:
                    if self._runtime._page_is_open(slot.page):
                        retained.append(
                            self._retained_page(thread, slot, "inspection_failed")
                        )
                        continue
                if self._runtime._page_is_open(slot.page):
                    retained.append(
                        self._retained_page(thread, slot, "inspection_failed")
                    )
                    continue
                self._runtime._discard_owned_page_binding(thread)
                continue
            reason = (
                "generating"
                if state == OwnedPageAttemptState.GENERATING.value
                else "human_intervention"
                if state == "human_intervention"
                else "inspection_failed"
            )
            retained.append(self._retained_page(thread, slot, reason))
        return retained

    async def abandon(self, args: dict[str, Any]) -> str:
        owner = self._required_argument(args, "owner")
        thread = self._required_argument(args, "thread")
        allowed_scope = self._scope_argument(args)
        expected_exact_url = args.get("expected_exact_url")
        if expected_exact_url is not None and (
            not isinstance(expected_exact_url, str)
            or not owned_page_url_is_canonical_session(expected_exact_url)
            or not owned_page_url_is_in_scope(expected_exact_url, allowed_scope)
        ):
            raise RuntimeError("invalid owned-page abandonment request")
        classify_program = self._required_argument(args, "classify_program")
        stop_program = self._required_argument(args, "stop_program")
        timeout = args.get("stop_confirmation_timeout_seconds")
        poll_interval = args.get("stop_confirmation_poll_interval_seconds")
        if not all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and value > 0
            for value in (timeout, poll_interval)
        ):
            raise RuntimeError("invalid owned-page abandonment request")

        slot = self._runtime._owned_page_slot(thread)
        if slot is None or not self._runtime._page_is_open(slot.page):
            return self._error(OwnedPageBridgeErrorCode.THREAD_NOT_FOUND)
        live_url = self._runtime._page_url(slot.page)
        if slot.owner != owner:
            return self._error(OwnedPageBridgeErrorCode.OWNERSHIP_CONFLICT)
        if (
            not owned_page_url_is_in_scope(live_url, allowed_scope)
            or not self._cleanup_url_is_recognized(live_url)
            or (expected_exact_url is not None and live_url != expected_exact_url)
        ):
            return self._error(OwnedPageBridgeErrorCode.ABANDONMENT_FAILED)

        state = await self._classify(slot, classify_program)
        if state is None or not self._slot_matches(
            slot,
            thread=thread,
            owner=owner,
            allowed_scope=allowed_scope,
            expected_exact_url=expected_exact_url,
        ):
            return self._error(OwnedPageBridgeErrorCode.ABANDONMENT_FAILED)
        if state == OwnedPageAttemptState.GENERATING.value:
            try:
                stop_metadata = await self._runtime._maybe_await(
                    slot.page.evaluate(stop_program)
                )
                if stop_metadata != {"state": "stop_requested"}:
                    raise RuntimeError("stop request was not affirmed")
            except Exception:
                return self._error(OwnedPageBridgeErrorCode.ABANDONMENT_FAILED)

            if not self._slot_matches(
                slot,
                thread=thread,
                owner=owner,
                allowed_scope=allowed_scope,
                expected_exact_url=expected_exact_url,
            ):
                return self._error(OwnedPageBridgeErrorCode.ABANDONMENT_FAILED)

            assert isinstance(timeout, (int, float))
            assert isinstance(poll_interval, (int, float))
            loop = asyncio.get_running_loop()
            deadline = loop.time() + float(timeout)
            while True:
                state = await self._classify(slot, classify_program)
                if not self._slot_matches(
                    slot,
                    thread=thread,
                    owner=owner,
                    allowed_scope=allowed_scope,
                    expected_exact_url=expected_exact_url,
                ):
                    return self._error(OwnedPageBridgeErrorCode.ABANDONMENT_FAILED)
                if state == OwnedPageAttemptState.STOPPED.value:
                    break
                if state != OwnedPageAttemptState.GENERATING.value:
                    return self._error(OwnedPageBridgeErrorCode.ABANDONMENT_FAILED)
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return self._error(OwnedPageBridgeErrorCode.ABANDONMENT_FAILED)
                await asyncio.sleep(min(float(poll_interval), remaining))

        if state not in {
            OwnedPageAttemptState.COMPLETED.value,
            OwnedPageAttemptState.STOPPED.value,
            OwnedPageAttemptState.FAILED.value,
            "human_intervention",
        }:
            return self._error(OwnedPageBridgeErrorCode.ABANDONMENT_FAILED)
        if not await self._close(
            slot,
            thread=thread,
            owner=owner,
            allowed_scope=allowed_scope,
            expected_exact_url=expected_exact_url,
        ):
            return self._error(OwnedPageBridgeErrorCode.ABANDONMENT_FAILED)
        attempt_state = None if state == "human_intervention" else state
        return json.dumps(
            {"ok": True, "attempt_state": attempt_state},
            separators=(",", ":"),
        )

    async def _classify(self, slot: PageSlot, program: str) -> str | None:
        try:
            metadata = await self._runtime._maybe_await(slot.page.evaluate(program))
        except Exception:
            return None
        if not isinstance(metadata, dict) or set(metadata) != {"state"}:
            return None
        state = metadata.get("state")
        if state not in {
            *(item.value for item in OwnedPageAttemptState),
            "human_intervention",
        }:
            return None
        assert isinstance(state, str)
        return state

    async def _close(
        self,
        slot: PageSlot,
        *,
        thread: str,
        owner: str,
        allowed_scope: OwnedPageScope,
        expected_exact_url: str | None,
    ) -> bool:
        if not self._slot_matches(
            slot,
            thread=thread,
            owner=owner,
            allowed_scope=allowed_scope,
            expected_exact_url=expected_exact_url,
        ):
            return False
        try:
            await self._runtime._maybe_await(slot.page.close())
        except Exception:
            if self._runtime._page_is_open(slot.page):
                return False
        if self._runtime._page_is_open(slot.page):
            return False
        self._runtime._discard_owned_page_binding(thread)
        return True

    def _slot_matches(
        self,
        slot: PageSlot,
        *,
        thread: str,
        owner: str,
        allowed_scope: OwnedPageScope,
        expected_exact_url: str | None,
    ) -> bool:
        if (
            self._runtime._owned_page_slot(thread) is not slot
            or not self._runtime._page_is_open(slot.page)
        ):
            return False
        live_url = self._runtime._page_url(slot.page)
        return (
            slot.owner == owner
            and owned_page_url_is_in_scope(live_url, allowed_scope)
            and self._cleanup_url_is_recognized(live_url)
            and (expected_exact_url is None or live_url == expected_exact_url)
        )

    def _retained_page(
        self,
        thread: str,
        slot: PageSlot,
        reason: str,
    ) -> dict[str, str]:
        url = self._runtime._page_url(slot.page)
        identity = (
            {"thread": thread, "session_id": url.rsplit("/", 1)[-1]}
            if owned_page_url_is_canonical_session(url)
            else {"thread": thread}
        )
        return {**identity, "reason": reason}

    def _cleanup_url_is_recognized(self, url: str) -> bool:
        return owned_page_url_is_canonical_session(url) or owned_page_url_is_in_scope(
            url,
            OwnedPageScope.CHATGPT_PRE_SESSION,
        )

    def _required_argument(self, args: dict[str, Any], name: str) -> str:
        value = args.get(name)
        if not isinstance(value, str) or not value:
            raise RuntimeError(f"owned-page {name} is required")
        return value

    def _scope_argument(self, args: dict[str, Any]) -> OwnedPageScope:
        try:
            return OwnedPageScope(args["allowed_scope"])
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeError("invalid owned-page scope") from error

    def _error(self, error: OwnedPageBridgeErrorCode) -> str:
        return json.dumps(
            {"ok": False, "error": error.value},
            separators=(",", ":"),
        )
