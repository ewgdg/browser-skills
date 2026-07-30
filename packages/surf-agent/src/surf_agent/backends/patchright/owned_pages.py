from __future__ import annotations

import json
from typing import Any, Protocol

from ...owned_pages import (
    MAX_RECENT_SESSIONS,
    OwnedPageAssignmentState,
    OwnedPageAttemptState,
    OwnedPageBridgeErrorCode,
    OwnedPageInspectionState,
    OwnedPagePreparationState,
    OwnedPageProtection,
    OwnedPageRecentSessionsState,
    OwnedPageScope,
    OwnedPageSelectionDimension,
    OwnedPageSubmissionState,
    decode_owned_page_protection,
    owned_page_canonical_session_url,
    owned_page_url_is_canonical_session,
    owned_page_url_is_in_scope,
)
from ..bridge_common import PageSlot
from .retained_pages import PatchrightRetainedPageOperations


_READ_ONLY_EVALUATION_MAX_ATTEMPTS = 3
_READ_ONLY_EVALUATION_RETRY_MILLISECONDS = 100


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

    def _owned_page_bindings(self) -> list[tuple[str, PageSlot]]: ...

    def _page_is_bound(self, page: Any) -> bool: ...

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
        self._retained_pages = PatchrightRetainedPageOperations(runtime)

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
        capacity_limit = args.get("capacity_limit")
        sweep_program = self._required_argument(args, "sweep_program")
        if (
            not isinstance(capacity_limit, int)
            or isinstance(capacity_limit, bool)
            or capacity_limit <= 0
        ):
            raise RuntimeError("invalid owned-page allocation policy")

        retained = await self._retained_pages.sweep(owner, sweep_program)

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
        if len(retained) >= capacity_limit:
            if len(retained) > capacity_limit:
                raise RuntimeError("owned-page capacity invariant exceeded")
            return json.dumps(
                {
                    "ok": False,
                    "error": OwnedPageBridgeErrorCode.CAPACITY_EXCEEDED.value,
                    "capacity": {
                        "limit": capacity_limit,
                        "retained": retained,
                    },
                },
                separators=(",", ":"),
            )

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

    async def resolve(self, args: dict[str, Any]) -> str:
        owner = self._required_argument(args, "owner")
        thread = self._required_argument(args, "thread")
        exact_url = self._required_argument(args, "exact_url")
        allowed_scope = self._scope_argument(args)
        if not owned_page_url_is_canonical_session(
            exact_url
        ) or not owned_page_url_is_in_scope(exact_url, allowed_scope):
            return self._error(OwnedPageBridgeErrorCode.OWNERSHIP_CONFLICT)

        existing = self._runtime._owned_page_slot(thread)
        if existing is not None and not self._runtime._page_is_open(existing.page):
            self._runtime._discard_owned_page_binding(thread)
            existing = None
        if existing is not None:
            if (
                existing.owner != owner
                or self._runtime._page_url(existing.page) != exact_url
            ):
                return self._error(OwnedPageBridgeErrorCode.OWNERSHIP_CONFLICT)
            return self._resolution_result(thread, existing)

        await self._runtime._start_async()
        context = self._runtime._context()
        restored_matches = [
            page
            for page in list(context.pages)
            if self._runtime._page_is_open(page)
            and not self._runtime._page_is_bound(page)
            and self._runtime._page_url(page) == exact_url
        ]
        if len(restored_matches) == 1:
            slot = self._runtime._bind_owned_page(
                thread,
                restored_matches[0],
                owner,
                None,
            )
            return self._resolution_result(thread, slot)

        if not restored_matches:
            page = await self._create_window_page(exact_url)
            if self._runtime._page_url(page) != exact_url:
                await self._runtime._best_effort_close_page(page)
                return self._error(OwnedPageBridgeErrorCode.OWNERSHIP_CONFLICT)
            slot = self._runtime._bind_owned_page(thread, page, owner, None)
            return self._resolution_result(thread, slot)

        return self._error(OwnedPageBridgeErrorCode.AMBIGUOUS_SESSION_PAGE)

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
        prompt = self._required_argument(args, "prompt")
        composer_selectors = self._string_list_argument(args, "composer_selectors")
        send_selectors = self._string_list_argument(args, "send_selectors")
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
            await self._type_into_visible_control(
                slot.page,
                composer_selectors,
                prompt,
            )
            metadata = await self._runtime._maybe_await(
                slot.page.evaluate(submission_program)
            )
            decoded = self._decode_submission_metadata(metadata)
        except Exception:
            return self._error(OwnedPageBridgeErrorCode.INSPECTION_FAILED)
        if decoded["state"] == OwnedPageSubmissionState.SUBMITTED:
            try:
                await self._click_visible_control(slot.page, send_selectors)
            except Exception:
                # Once native dispatch starts, the click may have succeeded even if
                # Patchright loses its acknowledgement or reports a transient URL.
                # The serialized transaction affirmed every page guard before this
                # point; only exact assignment observation may confirm the outcome.
                return self._metadata_result(
                    thread,
                    slot,
                    {"state": OwnedPageSubmissionState.SUBMITTED.value},
                )
        return self._metadata_result(thread, slot, decoded)

    async def observe_assignment(self, args: dict[str, Any]) -> str:
        guarded = self._submission_identity_slot(args)
        if isinstance(guarded, str):
            return guarded
        slot, thread, allowed_scope = guarded
        if not owned_page_url_is_in_scope(
            self._runtime._page_url(slot.page), allowed_scope
        ):
            if slot.send_may_have_occurred:
                return self._metadata_result(
                    thread,
                    slot,
                    {"state": OwnedPageAssignmentState.NOT_READY.value},
                )
            return self._error(OwnedPageBridgeErrorCode.OWNERSHIP_CONFLICT)
        program = self._required_argument(args, "program")
        completion_exact_url = args.get("completion_exact_url")
        if completion_exact_url is not None and (
            not isinstance(completion_exact_url, str)
            or not owned_page_url_is_canonical_session(completion_exact_url)
        ):
            raise RuntimeError("invalid owned-page assignment completion request")
        try:
            metadata = await self._runtime._maybe_await(slot.page.evaluate(program))
        except Exception:
            # Assignment observation is bounded and cannot send. A browser-side
            # evaluation interruption is therefore retryable only after revalidating
            # every original owner, token, protection, and scope guard.
            revalidated = self._submission_slot(args)
            if isinstance(revalidated, tuple) and revalidated[0] is slot:
                return self._metadata_result(
                    thread,
                    slot,
                    {"state": OwnedPageAssignmentState.NOT_READY.value},
                )
            return self._error(OwnedPageBridgeErrorCode.INSPECTION_FAILED)
        try:
            decoded = self._decode_assignment_metadata(metadata)
        except Exception:
            return self._error(OwnedPageBridgeErrorCode.INSPECTION_FAILED)
        decoded_session_id = decoded.get("session_id")
        # Clearing this barrier authorizes a later explicit follow-up. Do so only
        # when this serialized transaction affirms the caller's exact session.
        if completion_exact_url is not None and (
            decoded["state"] == OwnedPageAssignmentState.SESSION
            and self._runtime._page_url(slot.page) == completion_exact_url
            and isinstance(decoded_session_id, str)
            and completion_exact_url
            == owned_page_canonical_session_url(decoded_session_id)
        ):
            slot.send_may_have_occurred = False
        return self._metadata_result(thread, slot, decoded)

    async def classify_attempt(self, args: dict[str, Any]) -> str:
        guarded = self._observation_slot(args)
        if isinstance(guarded, str):
            return guarded
        slot, thread = guarded
        program = self._required_argument(args, "program")
        try:
            metadata = await self._evaluate_read_only(slot.page, program)
        except Exception:
            return self._error(OwnedPageBridgeErrorCode.INSPECTION_FAILED)
        try:
            decoded = self._decode_attempt_metadata(metadata)
        except Exception:
            return self._error(OwnedPageBridgeErrorCode.INSPECTION_FAILED)
        return self._metadata_result(thread, slot, decoded)

    async def extract_result(self, args: dict[str, Any]) -> str:
        guarded = self._observation_slot(args)
        if isinstance(guarded, str):
            return guarded
        slot, thread = guarded
        program = self._required_argument(args, "program")
        try:
            metadata = await self._evaluate_read_only(slot.page, program)
        except Exception:
            return self._error(OwnedPageBridgeErrorCode.INSPECTION_FAILED)
        try:
            decoded = self._decode_result_metadata(metadata)
        except Exception:
            return self._error(OwnedPageBridgeErrorCode.INSPECTION_FAILED)
        return self._metadata_result(thread, slot, decoded)

    async def discover_sessions(self, args: dict[str, Any]) -> str:
        guarded = self._submission_slot(args)
        if isinstance(guarded, str):
            return guarded
        slot, thread = guarded
        program = self._required_argument(args, "program")
        try:
            metadata = await self._runtime._maybe_await(slot.page.evaluate(program))
            decoded = self._decode_recent_sessions_metadata(metadata)
        except Exception:
            return self._error(OwnedPageBridgeErrorCode.INSPECTION_FAILED)
        return self._metadata_result(thread, slot, decoded)

    async def close_discovery(self, args: dict[str, Any]) -> str:
        # A successful explicit retry may still carry human-gate protection. Match
        # that exact live metadata immediately before closing the captured page.
        guarded = self._submission_slot(args)
        if isinstance(guarded, str):
            return guarded
        slot, thread = guarded
        try:
            await self._runtime._maybe_await(slot.page.close())
            if self._runtime._page_is_open(slot.page):
                raise RuntimeError("owned discovery page remained open")
        except Exception:
            return self._error(OwnedPageBridgeErrorCode.INSPECTION_FAILED)
        self._runtime._discard_owned_page_binding(thread)
        return json.dumps({"ok": True}, separators=(",", ":"))

    async def close_terminal(self, args: dict[str, Any]) -> str:
        guarded = self._observation_slot(args)
        if isinstance(guarded, str):
            return guarded
        slot, thread = guarded
        if slot.protection is not None:
            return self._error(OwnedPageBridgeErrorCode.OWNERSHIP_CONFLICT)
        program = self._required_argument(args, "program")
        try:
            expected_state = OwnedPageAttemptState(args["expected_state"])
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeError("invalid owned-page terminal close request") from error
        if expected_state is OwnedPageAttemptState.GENERATING:
            raise RuntimeError("invalid owned-page terminal close request")
        try:
            metadata = await self._runtime._maybe_await(slot.page.evaluate(program))
            decoded = self._decode_attempt_metadata(metadata)
        except Exception:
            return self._error(OwnedPageBridgeErrorCode.INSPECTION_FAILED)
        if decoded["state"] != expected_state.value:
            return self._error(OwnedPageBridgeErrorCode.INSPECTION_FAILED)
        revalidated = self._observation_slot(args)
        if isinstance(revalidated, str) or revalidated[0] is not slot:
            return self._error(OwnedPageBridgeErrorCode.OWNERSHIP_CONFLICT)
        try:
            await self._runtime._maybe_await(slot.page.close())
            if self._runtime._page_is_open(slot.page):
                raise RuntimeError("owned page remained open")
        except Exception:
            return self._error(OwnedPageBridgeErrorCode.INSPECTION_FAILED)
        self._runtime._discard_owned_page_binding(thread)
        return json.dumps({"ok": True}, separators=(",", ":"))

    async def abandon(self, args: dict[str, Any]) -> str:
        return await self._retained_pages.abandon(args)

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
                destination.send_may_have_occurred = False
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
        # A deterministic exact-URL binding proves the initial submission handshake
        # completed. A later explicit --session call may now start a distinct attempt.
        source.send_may_have_occurred = False
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

    def _string_list_argument(
        self,
        args: dict[str, Any],
        name: str,
    ) -> tuple[str, ...]:
        value = args.get(name)
        if (
            not isinstance(value, list)
            or not value
            or any(not isinstance(item, str) or not item for item in value)
            or len(set(value)) != len(value)
        ):
            raise RuntimeError(f"owned-page {name} must contain unique strings")
        return tuple(value)

    async def _type_into_visible_control(
        self,
        page: Any,
        selectors: tuple[str, ...],
        value: str,
    ) -> None:
        await self._focus_visible_control(page, selectors)
        # ChatGPT replaces its controlled textarea during trusted input. Locator
        # click/fill waits on that detached node, while one atomic browser input
        # completes before the replacement and can be verified before dispatch.
        await self._runtime._maybe_await(page.keyboard.insert_text(value))
        if await self._select_restored_editor_draft(page, selectors, value):
            await self._runtime._maybe_await(page.keyboard.insert_text(value))

    async def _select_restored_editor_draft(
        self,
        page: Any,
        selectors: tuple[str, ...],
        value: str,
    ) -> bool:
        state = await self._runtime._maybe_await(
            page.evaluate(
                """({expected, selectors}) => {
                  const editor = document.activeElement;
                  if (!editor || !selectors.some((selector) => editor.matches(selector))) {
                    return 'editor_unrecognized';
                  }
                  const text = 'value' in editor
                    ? String(editor.value) : String(editor.textContent || '');
                  if (text === expected) return 'exact';
                  if (!text.endsWith(expected)) return 'editor_unrecognized';
                  const paragraphs = editor.querySelectorAll('p');
                  if (paragraphs.length !== 1) return 'editor_unrecognized';
                  const selection = window.getSelection();
                  const range = document.createRange();
                  range.selectNodeContents(paragraphs[0]);
                  selection.removeAllRanges();
                  selection.addRange(range);
                  return selection.rangeCount === 1
                    ? 'selected' : 'editor_unrecognized';
                }""",
                {"expected": value, "selectors": list(selectors)},
            )
        )
        if state == "exact":
            return False
        if state == "selected":
            return True
        raise RuntimeError("Patchright could not reconcile the controlled editor")

    async def _focus_visible_control(
        self,
        page: Any,
        selectors: tuple[str, ...],
    ) -> None:
        for selector in selectors:
            locator = page.locator(selector)
            count = await self._runtime._maybe_await(locator.count())
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                raise RuntimeError("Patchright returned an invalid composer count")
            for index in range(count):
                candidate = locator.nth(index)
                visible = await self._runtime._maybe_await(candidate.is_visible())
                if visible is not True:
                    continue
                focused = await self._runtime._maybe_await(
                    candidate.evaluate(
                        "(node) => { node.focus(); return document.activeElement === node; }"
                    )
                )
                if focused is not True:
                    raise RuntimeError("Patchright could not focus the visible composer")
                return
        raise RuntimeError("Patchright could not identify a visible composer")

    async def _click_visible_control(
        self,
        page: Any,
        selectors: tuple[str, ...],
    ) -> None:
        for selector in selectors:
            locator = page.locator(selector)
            count = await self._runtime._maybe_await(locator.count())
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                raise RuntimeError("Patchright returned an invalid send-control count")
            for index in range(count):
                candidate = locator.nth(index)
                visible = await self._runtime._maybe_await(candidate.is_visible())
                if visible is not True:
                    continue
                await self._runtime._maybe_await(candidate.click())
                return
        raise RuntimeError("Patchright could not identify a visible send control")

    async def _evaluate_read_only(self, page: Any, program: str) -> object:
        for attempt in range(_READ_ONLY_EVALUATION_MAX_ATTEMPTS):
            try:
                return await self._runtime._maybe_await(page.evaluate(program))
            except Exception:
                if attempt + 1 == _READ_ONLY_EVALUATION_MAX_ATTEMPTS:
                    raise
                await self._runtime._maybe_await(
                    page.wait_for_timeout(_READ_ONLY_EVALUATION_RETRY_MILLISECONDS)
                )
        raise AssertionError("read-only evaluation retry loop did not terminate")

    def _resolution_result(self, thread: str, slot: PageSlot) -> str:
        result = json.loads(self._page_result(thread, slot))
        result["protection"] = (
            str(slot.protection) if slot.protection is not None else None
        )
        return json.dumps(result, separators=(",", ":"))

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
        guarded = self._submission_identity_slot(args)
        if isinstance(guarded, str):
            return guarded
        slot, thread, allowed_scope = guarded
        if not owned_page_url_is_in_scope(
            self._runtime._page_url(slot.page), allowed_scope
        ):
            return self._error(OwnedPageBridgeErrorCode.OWNERSHIP_CONFLICT)
        return slot, thread

    def _submission_identity_slot(
        self,
        args: dict[str, Any],
    ) -> tuple[PageSlot, str, OwnedPageScope] | str:
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
        ):
            return self._error(OwnedPageBridgeErrorCode.OWNERSHIP_CONFLICT)
        return slot, thread, allowed_scope

    def _observation_slot(self, args: dict[str, Any]) -> tuple[PageSlot, str] | str:
        owner = self._required_argument(args, "owner")
        thread = self._required_argument(args, "thread")
        expected_exact_url = self._required_argument(args, "expected_exact_url")
        allowed_scope = self._scope_argument(args)
        expected_page_token = args.get("expected_page_token")
        try:
            expected_protection = decode_owned_page_protection(
                args["expected_protection"]
            )
        except (KeyError, ValueError) as error:
            raise RuntimeError("invalid owned-page observation request") from error
        if (
            not isinstance(expected_page_token, int)
            or isinstance(expected_page_token, bool)
            or expected_page_token < 1
            or not owned_page_url_is_canonical_session(expected_exact_url)
        ):
            raise RuntimeError("invalid owned-page observation request")
        slot = self._runtime._owned_page_slot(thread)
        if not self._slot_matches_guards(
            slot,
            owner=owner,
            expected_page_token=expected_page_token,
            expected_exact_url=expected_exact_url,
            allowed_scope=allowed_scope,
            expected_protection=expected_protection,
        ):
            return self._error(OwnedPageBridgeErrorCode.OWNERSHIP_CONFLICT)
        assert slot is not None
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

    def _decode_attempt_metadata(self, metadata: object) -> dict[str, object]:
        fields = _require_string_keyed_object(
            metadata,
            "invalid owned-page attempt metadata",
        )
        if set(fields) != {"state"}:
            raise RuntimeError("invalid owned-page attempt metadata")
        try:
            state = OwnedPageAttemptState(fields["state"])
        except (TypeError, ValueError) as error:
            raise RuntimeError("invalid owned-page attempt metadata") from error
        return {"state": state.value}

    def _decode_result_metadata(self, metadata: object) -> dict[str, object]:
        fields = _require_string_keyed_object(
            metadata,
            "invalid owned-page result metadata",
        )
        try:
            state = OwnedPageAttemptState(fields.get("state"))
        except (TypeError, ValueError) as error:
            raise RuntimeError("invalid owned-page result metadata") from error
        includes_text = state in {
            OwnedPageAttemptState.COMPLETED,
            OwnedPageAttemptState.STOPPED,
        }
        expected_keys = {"state", "text"} if includes_text else {"state"}
        if set(fields) != expected_keys:
            raise RuntimeError("invalid owned-page result metadata")
        if includes_text and not isinstance(fields["text"], str):
            raise RuntimeError("invalid owned-page result metadata")
        return {key: fields[key] for key in expected_keys}

    def _decode_recent_sessions_metadata(
        self,
        metadata: object,
    ) -> dict[str, object]:
        fields = _require_string_keyed_object(
            metadata,
            "invalid owned-page recent-session metadata",
        )
        try:
            state = OwnedPageRecentSessionsState(fields.get("state"))
        except (TypeError, ValueError) as error:
            raise RuntimeError("invalid owned-page recent-session metadata") from error
        if state is not OwnedPageRecentSessionsState.SESSIONS:
            if set(fields) != {"state"}:
                raise RuntimeError("invalid owned-page recent-session metadata")
            return {"state": state.value}
        candidates = fields.get("sessions")
        if set(fields) != {"state", "sessions"} or not isinstance(candidates, list):
            raise RuntimeError("invalid owned-page recent-session metadata")
        if len(candidates) > MAX_RECENT_SESSIONS:
            raise RuntimeError("invalid owned-page recent-session metadata")
        decoded_sessions: list[dict[str, str]] = []
        seen: set[str] = set()
        for candidate in candidates:
            item = _require_string_keyed_object(
                candidate,
                "invalid owned-page recent-session metadata",
            )
            session_id = item.get("id")
            title = item.get("title")
            if (
                set(item) != {"id", "title"}
                or not isinstance(session_id, str)
                or not owned_page_url_is_canonical_session(
                    owned_page_canonical_session_url(session_id)
                )
                or session_id in seen
                or not isinstance(title, str)
                or not title
            ):
                raise RuntimeError("invalid owned-page recent-session metadata")
            seen.add(session_id)
            decoded_sessions.append({"id": session_id, "title": title})
        return {"state": state.value, "sessions": decoded_sessions}

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
            OwnedPageAssignmentState.RATE_LIMITED,
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
                owned_page_canonical_session_url(session_id)
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
