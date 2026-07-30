from __future__ import annotations

import json
from typing import Any, Protocol

from ...owned_pages import (
    OwnedPageBridgeErrorCode,
    OwnedPageInspectionState,
    OwnedPageProtection,
    OwnedPageScope,
    decode_owned_page_protection,
    owned_page_url_is_canonical_session,
    owned_page_url_is_in_scope,
)
from ..bridge_common import PageSlot


class PatchrightOwnedPageHost(Protocol):
    async def _start_async(self) -> None: ...

    async def _maybe_await(self, value: Any) -> Any: ...

    def _page_is_open(self, page: Any) -> bool: ...

    def _page_url(self, page: Any) -> str: ...

    def _context(self) -> Any: ...

    async def _wait_for_created_target_page(
        self,
        context: Any,
        url: str,
        excluded_page_ids: set[int],
        target_id: str,
    ) -> Any: ...

    async def _best_effort_close_target(self, session: Any, target_id: str) -> None: ...

    async def _best_effort_detach(self, session: Any | None) -> None: ...

    async def _best_effort_close_page(self, page: Any) -> None: ...

    def _owned_page_slot(self, thread: str) -> PageSlot | None: ...

    def _discard_owned_page_binding(self, thread: str) -> None: ...

    def _bind_owned_page(
        self,
        thread: str,
        page: Any,
        owner: str,
        protection: OwnedPageProtection | None,
    ) -> PageSlot: ...

    def _rebind_owned_page(
        self,
        source_thread: str,
        destination_thread: str,
        slot: PageSlot,
    ) -> None: ...

    def _set_owned_page_protection(
        self,
        slot: PageSlot,
        protection: OwnedPageProtection | None,
    ) -> None: ...


class PatchrightOwnedPageOperations:
    """Run guarded owned-page transactions on the Patchright runtime thread."""

    def __init__(self, runtime: PatchrightOwnedPageHost) -> None:
        self._runtime = runtime

    async def allocate(self, args: dict[str, Any]) -> str:
        owner = self._required_argument(args, "owner")
        thread = self._required_argument(args, "thread")
        url = self._required_argument(args, "url")
        allowed_scope = self._scope_argument(args)
        try:
            expected_protection = decode_owned_page_protection(
                args["expected_protection"]
            )
            protection = decode_owned_page_protection(args["protection"])
        except (KeyError, ValueError) as error:
            raise RuntimeError("invalid owned-page protection request") from error
        if expected_protection is not protection:
            raise RuntimeError(
                "owned-page allocation cannot change existing protection"
            )
        if not owned_page_url_is_in_scope(url, allowed_scope):
            raise RuntimeError("owned-page URL is outside the allowed scope")

        existing = self._runtime._owned_page_slot(thread)
        if existing is not None and not self._runtime._page_is_open(existing.page):
            self._runtime._discard_owned_page_binding(thread)
            existing = None
        if existing is not None:
            if (
                existing.owner != owner
                or existing.protection != expected_protection
                or not owned_page_url_is_in_scope(
                    self._runtime._page_url(existing.page), allowed_scope
                )
            ):
                return self._error(OwnedPageBridgeErrorCode.OWNERSHIP_CONFLICT)
            return self._page_result(thread, existing)

        await self._runtime._start_async()
        page = await self._create_window_page(url)
        if not owned_page_url_is_in_scope(
            self._runtime._page_url(page), allowed_scope
        ):
            await self._runtime._best_effort_close_page(page)
            raise RuntimeError("owned-page URL is outside the allowed scope")
        slot = self._runtime._bind_owned_page(
            thread,
            page,
            owner,
            protection,
        )
        return self._page_result(thread, slot)

    async def inspect(self, args: dict[str, Any]) -> str:
        owner = self._required_argument(args, "owner")
        thread = self._required_argument(args, "thread")
        allowed_scope = self._scope_argument(args)
        classifier = self._required_argument(args, "classifier")
        slot = self._runtime._owned_page_slot(thread)
        if slot is None or not self._runtime._page_is_open(slot.page):
            return self._error(OwnedPageBridgeErrorCode.THREAD_NOT_FOUND)
        if slot.owner != owner or not owned_page_url_is_in_scope(
            self._runtime._page_url(slot.page), allowed_scope
        ):
            return self._error(OwnedPageBridgeErrorCode.OWNERSHIP_CONFLICT)
        try:
            metadata = await self._runtime._maybe_await(slot.page.evaluate(classifier))
            state = self._decode_inspection_state(metadata)
        except Exception:
            return self._error(OwnedPageBridgeErrorCode.INSPECTION_FAILED)
        result = json.loads(self._page_result(thread, slot))
        result["metadata"] = {"state": state.value}
        return json.dumps(result, separators=(",", ":"))

    def rebind(self, args: dict[str, Any]) -> str:
        owner = self._required_argument(args, "owner")
        source_thread = self._required_argument(args, "source_thread")
        destination_thread = self._required_argument(args, "destination_thread")
        expected_exact_url = self._required_argument(args, "expected_exact_url")
        allowed_scope = self._scope_argument(args)
        expected_page_token = args.get("expected_page_token")
        try:
            expected_protection = decode_owned_page_protection(
                args["expected_protection"]
            )
        except (KeyError, ValueError) as error:
            raise RuntimeError("invalid owned-page rebind request") from error
        if (
            not isinstance(expected_page_token, int)
            or isinstance(expected_page_token, bool)
            or expected_page_token < 1
        ):
            raise RuntimeError("owned-page expected_page_token is required")
        if not owned_page_url_is_canonical_session(
            expected_exact_url
        ) or not owned_page_url_is_in_scope(expected_exact_url, allowed_scope):
            return self._error(OwnedPageBridgeErrorCode.OWNERSHIP_CONFLICT)

        source = self._runtime._owned_page_slot(source_thread)
        if (
            source is None
            or not self._runtime._page_is_open(source.page)
            or source.owner != owner
            or source.page_token != expected_page_token
            or self._runtime._page_url(source.page) != expected_exact_url
            or not owned_page_url_is_in_scope(
                self._runtime._page_url(source.page), allowed_scope
            )
            or source.protection != expected_protection
        ):
            return self._error(OwnedPageBridgeErrorCode.OWNERSHIP_CONFLICT)

        destination = self._runtime._owned_page_slot(destination_thread)
        if destination is not None and destination is not source:
            return self._error(OwnedPageBridgeErrorCode.OWNERSHIP_CONFLICT)

        self._runtime._rebind_owned_page(
            source_thread,
            destination_thread,
            source,
        )
        return self._page_result(destination_thread, source)

    def protect(self, args: dict[str, Any]) -> str:
        owner = self._required_argument(args, "owner")
        thread = self._required_argument(args, "thread")
        allowed_scope = self._scope_argument(args)
        expected_page_token = args.get("expected_page_token")
        try:
            expected_protection = decode_owned_page_protection(
                args["expected_protection"]
            )
            protection = decode_owned_page_protection(args["protection"])
        except (KeyError, ValueError) as error:
            raise RuntimeError("invalid owned-page protection request") from error
        if (
            not isinstance(expected_page_token, int)
            or isinstance(expected_page_token, bool)
            or expected_page_token < 1
        ):
            raise RuntimeError("invalid owned-page protection request")
        slot = self._runtime._owned_page_slot(thread)
        if (
            slot is None
            or not self._runtime._page_is_open(slot.page)
            or slot.owner != owner
            or slot.page_token != expected_page_token
            or not owned_page_url_is_in_scope(
                self._runtime._page_url(slot.page), allowed_scope
            )
            or slot.protection != expected_protection
        ):
            return self._error(OwnedPageBridgeErrorCode.OWNERSHIP_CONFLICT)
        self._runtime._set_owned_page_protection(slot, protection)
        return json.dumps({"ok": True}, separators=(",", ":"))

    async def _create_window_page(self, url: str) -> Any:
        context = self._runtime._context()
        visible_pages = [
            page for page in list(context.pages) if self._runtime._page_is_open(page)
        ]
        if visible_pages:
            anchor_page = visible_pages[0]
            close_anchor = False
        else:
            anchor_page = await self._runtime._maybe_await(context.new_page())
            close_anchor = True
            visible_pages = [anchor_page]
        excluded_page_ids = {id(page) for page in visible_pages}
        session = None
        target_id: str | None = None
        try:
            session = await self._runtime._maybe_await(
                context.new_cdp_session(anchor_page)
            )
            response = await self._runtime._maybe_await(
                session.send(
                    "Target.createTarget",
                    {"url": url, "newWindow": True, "background": True},
                )
            )
            target_id = response.get("targetId") if isinstance(response, dict) else None
            if not isinstance(target_id, str) or not target_id:
                raise RuntimeError(
                    "Patchright CDP Target.createTarget returned no valid target"
                )
            try:
                return await self._runtime._wait_for_created_target_page(
                    context,
                    url,
                    excluded_page_ids,
                    target_id,
                )
            except Exception:
                await self._runtime._best_effort_close_target(session, target_id)
                raise
        finally:
            await self._runtime._best_effort_detach(session)
            if close_anchor:
                await self._runtime._best_effort_close_page(anchor_page)

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

    def _decode_inspection_state(self, metadata: object) -> OwnedPageInspectionState:
        if not isinstance(metadata, dict) or set(metadata) != {"state"}:
            raise RuntimeError("owned-page classifier returned invalid metadata")
        try:
            return OwnedPageInspectionState(metadata.get("state"))
        except (TypeError, ValueError) as error:
            raise RuntimeError(
                "owned-page classifier returned invalid metadata"
            ) from error

    def _page_result(self, thread: str, slot: PageSlot) -> str:
        return json.dumps(
            {
                "ok": True,
                "page": {
                    "thread": thread,
                    "page_token": slot.page_token,
                    "url": self._runtime._page_url(slot.page),
                },
            },
            separators=(",", ":"),
        )

    def _error(self, error: OwnedPageBridgeErrorCode) -> str:
        return json.dumps(
            {"ok": False, "error": error.value},
            separators=(",", ":"),
        )
