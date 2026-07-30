from __future__ import annotations

import json
from typing import Any, Protocol

from ...owned_pages import (
    OwnedPageAssignmentState,
    OwnedPageBridgeErrorCode,
    OwnedPageInspectionState,
    OwnedPagePreparationState,
    OwnedPageProtection,
    OwnedPageScope,
    OwnedPageSelectionDimension,
    OwnedPageSubmissionState,
    decode_owned_page_protection,
    owned_page_url_is_canonical_session,
    owned_page_url_is_in_scope,
)
from ..bridge_common import PageSlot


def _require_string_keyed_object(value: object, message: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(
        not isinstance(key, str) for key in value
    ):
        raise RuntimeError(message)
    return {key: item for key, item in value.items() if isinstance(key, str)}


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

    async def prepare_submission(self, args: dict[str, Any]) -> str:
        guarded = self._submission_slot(args)
        if isinstance(guarded, str):
            return guarded
        slot, thread = guarded
        if slot.send_may_have_occurred:
            return self._error(
                OwnedPageBridgeErrorCode.SUBMISSION_ALREADY_ATTEMPTED
            )
        program = self._required_argument(args, "program")
        requested_dimensions = self._selection_dimensions_argument(args)
        try:
            metadata = await self._runtime._maybe_await(slot.page.evaluate(program))
            decoded = self._decode_preparation_metadata(
                metadata, requested_dimensions
            )
        except Exception:
            return self._error(OwnedPageBridgeErrorCode.INSPECTION_FAILED)
        return self._metadata_result(thread, slot, decoded)

    async def submit_prompt(self, args: dict[str, Any]) -> str:
        guarded = self._submission_slot(args)
        if isinstance(guarded, str):
            return guarded
        slot, thread = guarded
        if slot.send_may_have_occurred:
            return self._error(
                OwnedPageBridgeErrorCode.SUBMISSION_ALREADY_ATTEMPTED
            )
        readiness_program = self._required_argument(args, "readiness_program")
        submission_program = self._required_argument(args, "submission_program")
        try:
            readiness = await self._runtime._maybe_await(
                slot.page.evaluate(readiness_program)
            )
            prepared = self._decode_preparation_metadata(readiness, frozenset())
        except Exception:
            return self._error(OwnedPageBridgeErrorCode.INSPECTION_FAILED)
        if prepared["state"] != OwnedPagePreparationState.READY:
            return self._metadata_result(
                thread,
                slot,
                {"state": OwnedPageSubmissionState(prepared["state"]).value},
            )

        # The readiness evaluation above must be the last browser interaction before
        # crossing this irreversible barrier. Any later loss is treated as post-send.
        slot.send_may_have_occurred = True
        try:
            metadata = await self._runtime._maybe_await(
                slot.page.evaluate(submission_program)
            )
            decoded = self._decode_submission_metadata(metadata)
        except Exception:
            return self._error(OwnedPageBridgeErrorCode.INSPECTION_FAILED)
        return self._metadata_result(thread, slot, decoded)

    async def observe_assignment(self, args: dict[str, Any]) -> str:
        guarded = self._submission_slot(args)
        if isinstance(guarded, str):
            return guarded
        slot, thread = guarded
        program = self._required_argument(args, "program")
        try:
            metadata = await self._runtime._maybe_await(slot.page.evaluate(program))
            decoded = self._decode_assignment_metadata(metadata)
        except Exception:
            return self._error(OwnedPageBridgeErrorCode.INSPECTION_FAILED)
        return self._metadata_result(thread, slot, decoded)

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
        destination = self._runtime._owned_page_slot(destination_thread)
        if source is None:
            if destination is not None and self._slot_matches_guards(
                destination,
                owner=owner,
                expected_page_token=expected_page_token,
                expected_exact_url=expected_exact_url,
                allowed_scope=allowed_scope,
                expected_protection=expected_protection,
            ):
                # A caller may lose the first response after the atomic move. Replaying
                # the original CAS must affirm it instead of creating uncertainty.
                return self._page_result(destination_thread, destination)
            return self._error(OwnedPageBridgeErrorCode.OWNERSHIP_CONFLICT)
        if not self._slot_matches_guards(
            source,
            owner=owner,
            expected_page_token=expected_page_token,
            expected_exact_url=expected_exact_url,
            allowed_scope=allowed_scope,
            expected_protection=expected_protection,
        ):
            return self._error(OwnedPageBridgeErrorCode.OWNERSHIP_CONFLICT)
        if destination is not None and destination is not source:
            return self._error(OwnedPageBridgeErrorCode.OWNERSHIP_CONFLICT)

        self._runtime._rebind_owned_page(
            source_thread,
            destination_thread,
            source,
        )
        return self._page_result(destination_thread, source)

    def _slot_matches_guards(
        self,
        slot: PageSlot | None,
        *,
        owner: str,
        expected_page_token: int,
        expected_exact_url: str,
        allowed_scope: OwnedPageScope,
        expected_protection: OwnedPageProtection | None,
    ) -> bool:
        if slot is None or not self._runtime._page_is_open(slot.page):
            return False
        live_url = self._runtime._page_url(slot.page)
        return (
            slot.owner == owner
            and slot.page_token == expected_page_token
            and live_url == expected_exact_url
            and owned_page_url_is_in_scope(live_url, allowed_scope)
            and slot.protection == expected_protection
        )

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

    def _submission_slot(self, args: dict[str, Any]) -> tuple[PageSlot, str] | str:
        owner = self._required_argument(args, "owner")
        thread = self._required_argument(args, "thread")
        allowed_scope = self._scope_argument(args)
        expected_page_token = args.get("expected_page_token")
        try:
            expected_protection = decode_owned_page_protection(
                args["expected_protection"]
            )
        except (KeyError, ValueError) as error:
            raise RuntimeError("invalid owned-page submission request") from error
        if (
            not isinstance(expected_page_token, int)
            or isinstance(expected_page_token, bool)
            or expected_page_token < 1
        ):
            raise RuntimeError("invalid owned-page submission request")
        slot = self._runtime._owned_page_slot(thread)
        if slot is None or not self._runtime._page_is_open(slot.page):
            return self._error(OwnedPageBridgeErrorCode.THREAD_NOT_FOUND)
        if (
            slot.owner != owner
            or slot.page_token != expected_page_token
            or slot.protection != expected_protection
            or not owned_page_url_is_in_scope(
                self._runtime._page_url(slot.page), allowed_scope
            )
        ):
            return self._error(OwnedPageBridgeErrorCode.OWNERSHIP_CONFLICT)
        return slot, thread

    def _selection_dimensions_argument(
        self, args: dict[str, Any]
    ) -> frozenset[OwnedPageSelectionDimension]:
        values = args.get("requested_selection_dimensions")
        if not isinstance(values, list) or len(values) != len(set(values)):
            raise RuntimeError("invalid owned-page selection dimensions")
        try:
            return frozenset(OwnedPageSelectionDimension(value) for value in values)
        except (TypeError, ValueError) as error:
            raise RuntimeError("invalid owned-page selection dimensions") from error

    def _decode_preparation_metadata(
        self,
        metadata: object,
        requested_dimensions: frozenset[OwnedPageSelectionDimension],
    ) -> dict[str, object]:
        fields = _require_string_keyed_object(
            metadata,
            "invalid owned-page preparation metadata",
        )
        try:
            state = OwnedPagePreparationState(fields.get("state"))
        except (TypeError, ValueError) as error:
            raise RuntimeError("invalid owned-page preparation metadata") from error
        if state is not OwnedPagePreparationState.READY:
            if set(fields) != {"state"}:
                raise RuntimeError("invalid owned-page preparation metadata")
            return {"state": state.value}
        if set(fields) != {"state", "selection"}:
            raise RuntimeError("invalid owned-page preparation metadata")
        selection = _require_string_keyed_object(
            fields["selection"],
            "invalid owned-page preparation metadata",
        )
        expected_keys = {dimension.value for dimension in requested_dimensions}
        if set(selection) != expected_keys:
            raise RuntimeError("invalid owned-page preparation metadata")
        if any(not isinstance(label, str) or not label for label in selection.values()):
            raise RuntimeError("invalid owned-page preparation metadata")
        return {
            "state": state.value,
            "selection": {
                dimension.value: selection[dimension.value]
                for dimension in sorted(
                    requested_dimensions, key=lambda item: item.value
                )
            },
        }

    def _decode_submission_metadata(self, metadata: object) -> dict[str, object]:
        fields = _require_string_keyed_object(
            metadata,
            "invalid owned-page submission metadata",
        )
        if set(fields) != {"state"}:
            raise RuntimeError("invalid owned-page submission metadata")
        try:
            state = OwnedPageSubmissionState(fields["state"])
        except (TypeError, ValueError) as error:
            raise RuntimeError("invalid owned-page submission metadata") from error
        return {"state": state.value}

    def _decode_assignment_metadata(self, metadata: object) -> dict[str, object]:
        fields = _require_string_keyed_object(
            metadata,
            "invalid owned-page assignment metadata",
        )
        try:
            state = OwnedPageAssignmentState(fields.get("state"))
        except (TypeError, ValueError) as error:
            raise RuntimeError("invalid owned-page assignment metadata") from error
        allows_session_identity = state in {
            OwnedPageAssignmentState.SESSION,
            OwnedPageAssignmentState.LOGIN_REQUIRED,
            OwnedPageAssignmentState.CHALLENGE,
        }
        has_session_identity = set(fields) == {"state", "session_id"}
        if state is OwnedPageAssignmentState.SESSION and not has_session_identity:
            raise RuntimeError("invalid owned-page assignment metadata")
        if not has_session_identity:
            if set(fields) != {"state"}:
                raise RuntimeError("invalid owned-page assignment metadata")
            return {"state": state.value}
        session_id = fields["session_id"]
        if (
            not allows_session_identity
            or not isinstance(session_id, str)
            or not owned_page_url_is_canonical_session(
                f"https://chatgpt.com/c/{session_id}"
            )
        ):
            raise RuntimeError("invalid owned-page assignment metadata")
        return {"state": state.value, "session_id": session_id}

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

    def _metadata_result(
        self, thread: str, slot: PageSlot, metadata: dict[str, object]
    ) -> str:
        result = json.loads(self._page_result(thread, slot))
        result["metadata"] = metadata
        return json.dumps(result, separators=(",", ":"))

    def _error(self, error: OwnedPageBridgeErrorCode) -> str:
        return json.dumps(
            {"ok": False, "error": error.value},
            separators=(",", ":"),
        )
