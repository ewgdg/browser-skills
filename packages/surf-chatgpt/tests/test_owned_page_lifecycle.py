from __future__ import annotations

import io
import json
from dataclasses import dataclass
from typing import Any

import pytest

from surf_agent.owned_pages import (
    AbandonOwnedPage,
    AllocateOwnedPage,
    ClassifyOwnedPageAttempt,
    CloseTerminalOwnedPage,
    ExtractOwnedPageResult,
    InspectOwnedPage,
    OwnedPageCapabilities,
    OwnedPageAbandonment,
    OwnedPageAbandonmentFailed,
    OwnedPageCapacity,
    OwnedPageCapacityExceeded,
    OwnedPageAmbiguousSession,
    OwnedPageAttemptMetadata,
    OwnedPageAttemptResult,
    OwnedPageAttemptState,
    OwnedPageInspection,
    OwnedPageInspectionFailed,
    OwnedPageInspectionState,
    OwnedPageNotFound,
    OwnedPageOwnershipConflict,
    OwnedPageProtection,
    OwnedPageRef,
    OwnedPageRetainedPage,
    OwnedPageRetentionReason,
    OwnedPageBridge,
    ProtectOwnedPage,
    RebindOwnedPage,
    ResolveOwnedPage,
    ResolvedOwnedPage,
    owned_page_url_is_in_scope,
)
from surf_agent.errors import BridgeIdentityUnproven, BridgeUnavailable
from surf_chatgpt import cli
from surf_chatgpt.session_lifecycle import OwnedPageSessionLifecycle


@dataclass
class MemoryPage:
    owner: str
    reference: OwnedPageRef
    protection: OwnedPageProtection | None
    inspection_state: OwnedPageInspectionState
    attempt_state: OwnedPageAttemptState = OwnedPageAttemptState.GENERATING
    result_text: str = ""


class InMemoryOwnedPageBridge:
    def __init__(self) -> None:
        self.pages: dict[str, MemoryPage] = {}
        self.calls: list[tuple[str, object]] = []
        self.next_page_token = 700

    def capabilities(self) -> OwnedPageCapabilities:
        return OwnedPageCapabilities.complete()

    def allocate(self, request: AllocateOwnedPage) -> OwnedPageRef:
        self.calls.append(("allocate", request))
        existing = self.pages.get(request.thread)
        if existing is not None:
            if (
                existing.owner != request.owner
                or existing.protection is not request.expected_protection
                or not owned_page_url_is_in_scope(
                    existing.reference.exact_url, request.allowed_scope
                )
            ):
                raise OwnedPageOwnershipConflict
            return existing.reference
        reference = OwnedPageRef(
            thread=request.thread,
            page_token=self.next_page_token,
            exact_url=request.url,
        )
        self.next_page_token += 1
        self.pages[request.thread] = MemoryPage(
            request.owner,
            reference,
            request.protection,
            OwnedPageInspectionState.PRE_SESSION,
        )
        return reference

    def inspect(self, request: InspectOwnedPage) -> OwnedPageInspection:
        self.calls.append(("inspect", request))
        try:
            page = self.pages[request.thread]
        except KeyError as error:
            raise OwnedPageNotFound from error
        if page.owner != request.owner or not owned_page_url_is_in_scope(
            page.reference.exact_url, request.allowed_scope
        ):
            raise OwnedPageOwnershipConflict
        return OwnedPageInspection(page.reference, page.inspection_state)

    def resolve(self, request: ResolveOwnedPage) -> ResolvedOwnedPage:
        self.calls.append(("resolve", request))
        existing = self.pages.get(request.thread)
        if existing is not None:
            if (
                existing.owner != request.owner
                or existing.reference.exact_url != request.exact_url
            ):
                raise OwnedPageOwnershipConflict
            return ResolvedOwnedPage(existing.reference, existing.protection)
        reference = OwnedPageRef(
            request.thread,
            self.next_page_token,
            request.exact_url,
        )
        self.next_page_token += 1
        self.pages[request.thread] = MemoryPage(
            request.owner,
            reference,
            None,
            OwnedPageInspectionState.SESSION,
        )
        return ResolvedOwnedPage(reference, None)

    def rebind(self, request: RebindOwnedPage) -> OwnedPageRef:
        raise AssertionError("rebind is outside the issue 14 lifecycle path")

    def protect(self, request: ProtectOwnedPage) -> None:
        self.calls.append(("protect", request))
        page = self.pages[request.thread]
        if (
            page.reference.page_token != request.expected_page_token
            or page.protection is not request.expected_protection
        ):
            raise OwnedPageOwnershipConflict
        page.protection = request.protection

    def classify_attempt(
        self, request: ClassifyOwnedPageAttempt
    ) -> OwnedPageAttemptMetadata:
        self.calls.append(("classify_attempt", request))
        page = self.pages[request.thread]
        return OwnedPageAttemptMetadata(page.reference, page.attempt_state)

    def extract_result(
        self, request: ExtractOwnedPageResult
    ) -> OwnedPageAttemptResult:
        self.calls.append(("extract_result", request))
        page = self.pages[request.thread]
        text = (
            page.result_text
            if page.attempt_state
            in {OwnedPageAttemptState.COMPLETED, OwnedPageAttemptState.STOPPED}
            else None
        )
        return OwnedPageAttemptResult(page.reference, page.attempt_state, text)

    def close_terminal(self, request: CloseTerminalOwnedPage) -> None:
        self.calls.append(("close_terminal", request))
        page = self.pages[request.thread]
        if (
            page.reference.page_token != request.expected_page_token
            or page.reference.exact_url != request.expected_exact_url
            or page.protection is not request.expected_protection
            or page.attempt_state is not request.expected_state
        ):
            raise OwnedPageOwnershipConflict
        del self.pages[request.thread]

    def abandon(self, request: AbandonOwnedPage) -> OwnedPageAbandonment:
        self.calls.append(("abandon", request))
        try:
            page = self.pages[request.thread]
        except KeyError as error:
            raise OwnedPageNotFound from error
        if (
            page.owner != request.owner
            or (
                request.expected_exact_url is not None
                and page.reference.exact_url != request.expected_exact_url
            )
        ):
            raise OwnedPageOwnershipConflict
        if page.attempt_state is OwnedPageAttemptState.GENERATING:
            page.attempt_state = OwnedPageAttemptState.STOPPED
        attempt_state = (
            None
            if page.inspection_state
            in {
                OwnedPageInspectionState.PRE_SESSION,
                OwnedPageInspectionState.HUMAN_GATE,
            }
            else page.attempt_state
        )
        del self.pages[request.thread]
        return OwnedPageAbandonment(attempt_state)


def invoke(
    argv: list[str],
    bridge: OwnedPageBridge,
    *,
    monotonic=None,
    sleeper=None,
) -> tuple[int, dict[str, Any], str]:
    output = io.StringIO()
    errors = io.StringIO()
    code = cli.main(
        argv,
        stdout=output,
        stderr=errors,
        lifecycle=OwnedPageSessionLifecycle(
            bridge,
            monotonic=monotonic,
            sleeper=sleeper,
        ),
    )
    return code, json.loads(output.getvalue()), errors.getvalue()


def test_login_allocates_or_reuses_one_protected_owned_page_and_returns_only_the_handoff() -> (
    None
):
    bridge = InMemoryOwnedPageBridge()

    first = invoke(["login"], bridge)
    second = invoke(["login"], bridge)

    expected = {
        "ok": True,
        "handoff": {
            "action": "complete_login",
            "thread": "surf-chatgpt-login",
        },
    }
    assert first == (0, expected, "")
    assert second == (0, expected, "")
    assert len(bridge.pages) == 1
    assert bridge.pages["surf-chatgpt-login"].owner == "surf-chatgpt"
    assert (
        bridge.pages["surf-chatgpt-login"].protection
        is OwnedPageProtection.HUMAN_INTERVENTION
    )
    assert [operation for operation, _ in bridge.calls] == ["allocate", "allocate"]
    rendered = json.dumps(expected)
    assert '"owner"' not in rendered
    assert '"protection"' not in rendered
    assert '"page_token"' not in rendered
    assert "human_intervention" not in rendered
    assert "700" not in rendered


class CapacityExceededBridge(InMemoryOwnedPageBridge):
    def allocate(self, request: AllocateOwnedPage) -> OwnedPageRef:
        self.calls.append(("allocate", request))
        raise OwnedPageCapacityExceeded(
            OwnedPageCapacity(
                limit=10,
                retained=tuple(
                    OwnedPageRetainedPage(
                        session_id=f"session-{index}",
                        thread=(
                            "surf-chatgpt-submit-pending"
                            if index == 0
                            else f"surf-chatgpt-session-session-{index}"
                        ),
                        reason=(
                            OwnedPageRetentionReason.GENERATING
                            if index % 2 == 0
                            else OwnedPageRetentionReason.EXPLICITLY_RETAINED
                        ),
                    )
                    for index in range(10)
                ),
            )
        )


def test_eleventh_scripted_allocation_returns_bounded_capacity_json() -> None:
    bridge = CapacityExceededBridge()

    code, payload, stderr = invoke(["login"], bridge)

    assert code == 1
    assert payload["error"]["type"] == "capacity_exceeded"
    assert payload["capacity"]["limit"] == 10
    assert len(payload["capacity"]["retained"]) == 10
    assert payload["capacity"]["retained"][0] == {
        "thread": "surf-chatgpt-submit-pending",
        "reason": "generating",
    }
    assert "url" not in json.dumps(payload)
    assert "page_token" not in json.dumps(payload)
    assert stderr == ""
    assert [operation for operation, _ in bridge.calls] == ["allocate"]


def test_session_current_reads_only_the_exact_owned_thread_for_not_ready_or_durable_identity() -> (
    None
):
    bridge = InMemoryOwnedPageBridge()
    bridge.pages["preserved"] = MemoryPage(
        owner="surf-chatgpt",
        reference=OwnedPageRef(
            thread="preserved",
            page_token=701,
            exact_url="https://chatgpt.com/",
        ),
        protection=OwnedPageProtection.HUMAN_INTERVENTION,
        inspection_state=OwnedPageInspectionState.PRE_SESSION,
    )

    not_ready = invoke(["session", "current", "--thread", "preserved"], bridge)
    bridge.pages["preserved"].inspection_state = OwnedPageInspectionState.HUMAN_GATE
    human_gate = invoke(["session", "current", "--thread", "preserved"], bridge)
    bridge.pages["preserved"].reference = OwnedPageRef(
        thread="preserved",
        page_token=701,
        exact_url="https://chatgpt.com/c/abc123",
    )
    bridge.pages["preserved"].inspection_state = OwnedPageInspectionState.SESSION
    assigned = invoke(["session", "current", "--thread", "preserved"], bridge)

    assert not_ready == (
        0,
        {
            "ok": True,
            "session": None,
            "observation": {"outcome": "not_ready"},
        },
        "",
    )
    assert human_gate == not_ready
    assert assigned == (0, {"ok": True, "session": {"id": "abc123"}}, "")
    assert [operation for operation, _ in bridge.calls] == [
        "inspect",
        "inspect",
        "inspect",
    ]


def test_session_handoff_resolves_and_retains_only_the_durable_session() -> None:
    bridge = InMemoryOwnedPageBridge()

    code, payload, stderr = invoke(["session", "handoff", "abc123"], bridge)

    assert (code, payload, stderr) == (
        0,
        {
            "ok": True,
            "session": {"id": "abc123"},
            "handoff": {
                "action": "inspect_browser",
                "thread": "surf-chatgpt-session-abc123",
            },
        },
        "",
    )
    page = bridge.pages["surf-chatgpt-session-abc123"]
    assert page.reference.exact_url == "https://chatgpt.com/c/abc123"
    assert page.protection is OwnedPageProtection.EXPLICITLY_RETAINED
    assert [operation for operation, _ in bridge.calls] == ["resolve", "protect"]
    rendered = json.dumps(payload)
    assert "page_token" not in rendered
    assert "protection" not in rendered
    assert "https://" not in rendered


def test_abandon_generating_session_stops_once_then_releases_the_page() -> None:
    bridge = InMemoryOwnedPageBridge()
    thread = "surf-chatgpt-session-abc123"
    bridge.pages[thread] = MemoryPage(
        owner="surf-chatgpt",
        reference=OwnedPageRef(thread, 701, "https://chatgpt.com/c/abc123"),
        protection=OwnedPageProtection.EXPLICITLY_RETAINED,
        inspection_state=OwnedPageInspectionState.SESSION,
        attempt_state=OwnedPageAttemptState.GENERATING,
    )

    code, payload, stderr = invoke(["abandon", "abc123"], bridge)

    assert (code, payload, stderr) == (
        0,
        {
            "ok": True,
            "session": {"id": "abc123"},
            "attempt": {"state": "stopped"},
        },
        "",
    )
    assert thread not in bridge.pages
    assert [operation for operation, _ in bridge.calls] == ["abandon"]


@pytest.mark.parametrize(
    "attempt_state",
    [
        OwnedPageAttemptState.COMPLETED,
        OwnedPageAttemptState.STOPPED,
        OwnedPageAttemptState.FAILED,
    ],
)
def test_abandon_terminal_session_closes_directly(
    attempt_state: OwnedPageAttemptState,
) -> None:
    bridge = InMemoryOwnedPageBridge()
    thread = "surf-chatgpt-session-abc123"
    bridge.pages[thread] = MemoryPage(
        owner="surf-chatgpt",
        reference=OwnedPageRef(thread, 701, "https://chatgpt.com/c/abc123"),
        protection=OwnedPageProtection.EXPLICITLY_RETAINED,
        inspection_state=OwnedPageInspectionState.SESSION,
        attempt_state=attempt_state,
    )

    code, payload, _ = invoke(["abandon", "abc123"], bridge)

    assert code == 0
    assert payload == {
        "ok": True,
        "session": {"id": "abc123"},
        "attempt": {"state": attempt_state.value},
    }
    assert thread not in bridge.pages


def test_abandon_affirmed_pre_session_human_page_returns_only_its_thread() -> None:
    bridge = InMemoryOwnedPageBridge()
    thread = "surf-chatgpt-login"
    bridge.pages[thread] = MemoryPage(
        owner="surf-chatgpt",
        reference=OwnedPageRef(thread, 701, "https://chatgpt.com/auth/login"),
        protection=OwnedPageProtection.HUMAN_INTERVENTION,
        inspection_state=OwnedPageInspectionState.HUMAN_GATE,
    )

    code, payload, _ = invoke(["abandon", "--thread", thread], bridge)

    assert code == 0
    assert payload == {"ok": True, "thread": thread}
    assert thread not in bridge.pages


class FailedAbandonmentBridge(InMemoryOwnedPageBridge):
    def abandon(self, request: AbandonOwnedPage) -> OwnedPageAbandonment:
        self.calls.append(("abandon", request))
        raise OwnedPageAbandonmentFailed


def test_failed_abandonment_preserves_the_addressed_page_and_identity() -> None:
    bridge = FailedAbandonmentBridge()
    thread = "surf-chatgpt-session-abc123"
    bridge.pages[thread] = MemoryPage(
        owner="surf-chatgpt",
        reference=OwnedPageRef(thread, 701, "https://chatgpt.com/c/abc123"),
        protection=OwnedPageProtection.EXPLICITLY_RETAINED,
        inspection_state=OwnedPageInspectionState.SESSION,
    )

    code, payload, stderr = invoke(["abandon", "abc123"], bridge)

    assert code == 1
    assert payload["error"]["type"] == "abandonment_failed"
    assert payload["session"] == {"id": "abc123"}
    assert thread in bridge.pages
    assert stderr == ""


def test_abandon_missing_session_uses_durable_session_error_semantics() -> None:
    bridge = InMemoryOwnedPageBridge()

    code, payload, _ = invoke(["abandon", "abc123"], bridge)

    assert code == 1
    assert payload["error"]["type"] == "session_not_found"
    assert payload["session"] == {"id": "abc123"}


def test_session_status_affirms_generating_without_extracting_response_content() -> None:
    bridge = InMemoryOwnedPageBridge()

    code, payload, stderr = invoke(["session", "status", "abc123"], bridge)

    assert (code, payload, stderr) == (
        0,
        {
            "ok": True,
            "session": {"id": "abc123"},
            "attempt": {"state": "generating"},
        },
        "",
    )
    assert [operation for operation, _ in bridge.calls] == [
        "resolve",
        "classify_attempt",
    ]


@pytest.mark.parametrize(
    ("state", "extra_fields"),
    [
        (OwnedPageAttemptState.COMPLETED, {}),
        (OwnedPageAttemptState.STOPPED, {}),
        (OwnedPageAttemptState.FAILED, {"result": None}),
    ],
)
def test_session_status_closes_only_after_affirming_a_terminal_attempt(
    state: OwnedPageAttemptState,
    extra_fields: dict[str, object],
) -> None:
    bridge = InMemoryOwnedPageBridge()
    thread = "surf-chatgpt-session-abc123"
    bridge.pages[thread] = MemoryPage(
        owner="surf-chatgpt",
        reference=OwnedPageRef(thread, 701, "https://chatgpt.com/c/abc123"),
        protection=None,
        inspection_state=OwnedPageInspectionState.SESSION,
        attempt_state=state,
    )

    code, payload, stderr = invoke(["session", "status", "abc123"], bridge)

    assert (code, payload, stderr) == (
        0,
        {
            "ok": True,
            "session": {"id": "abc123"},
            "attempt": {"state": state.value},
            **extra_fields,
        },
        "",
    )
    assert thread not in bridge.pages
    assert [operation for operation, _ in bridge.calls] == [
        "resolve",
        "classify_attempt",
        "close_terminal",
    ]


def test_session_result_reports_generating_as_repeatable_not_ready_without_text() -> None:
    bridge = InMemoryOwnedPageBridge()

    first = invoke(["session", "result", "abc123"], bridge)
    second = invoke(["session", "result", "abc123"], bridge)

    expected = (
        0,
        {
            "ok": True,
            "session": {"id": "abc123"},
            "attempt": {"state": "generating"},
            "observation": {"outcome": "not_ready"},
            "result": None,
        },
        "",
    )
    assert first == expected
    assert second == expected
    assert [operation for operation, _ in bridge.calls] == [
        "resolve",
        "extract_result",
        "resolve",
        "extract_result",
    ]


@pytest.mark.parametrize(
    ("state", "text", "expected_result"),
    [
        (
            OwnedPageAttemptState.COMPLETED,
            "Answer",
            {"text": "Answer", "partial": False},
        ),
        (
            OwnedPageAttemptState.STOPPED,
            "Partial answer",
            {"text": "Partial answer", "partial": True},
        ),
        (OwnedPageAttemptState.FAILED, "CANARY-stale-fragment", None),
    ],
)
def test_session_result_returns_terminal_schema_then_guardedly_closes(
    state: OwnedPageAttemptState,
    text: str,
    expected_result: object,
) -> None:
    bridge = InMemoryOwnedPageBridge()
    thread = "surf-chatgpt-session-abc123"
    bridge.pages[thread] = MemoryPage(
        owner="surf-chatgpt",
        reference=OwnedPageRef(thread, 701, "https://chatgpt.com/c/abc123"),
        protection=None,
        inspection_state=OwnedPageInspectionState.SESSION,
        attempt_state=state,
        result_text=text,
    )

    code, payload, stderr = invoke(["session", "result", "abc123"], bridge)

    assert (code, payload, stderr) == (
        0,
        {
            "ok": True,
            "session": {"id": "abc123"},
            "attempt": {"state": state.value},
            "result": expected_result,
        },
        "",
    )
    assert "CANARY" not in json.dumps(payload)
    assert thread not in bridge.pages
    assert [operation for operation, _ in bridge.calls] == [
        "resolve",
        "extract_result",
        "close_terminal",
    ]


def test_retain_protects_terminal_result_before_observation_and_skips_cleanup() -> None:
    bridge = InMemoryOwnedPageBridge()
    thread = "surf-chatgpt-session-abc123"
    bridge.pages[thread] = MemoryPage(
        owner="surf-chatgpt",
        reference=OwnedPageRef(thread, 701, "https://chatgpt.com/c/abc123"),
        protection=None,
        inspection_state=OwnedPageInspectionState.SESSION,
        attempt_state=OwnedPageAttemptState.COMPLETED,
        result_text="Answer",
    )

    code, payload, _ = invoke(
        ["session", "result", "abc123", "--retain"],
        bridge,
    )

    assert code == 0
    assert payload["result"] == {"text": "Answer", "partial": False}
    assert bridge.pages[thread].protection is OwnedPageProtection.EXPLICITLY_RETAINED
    assert [operation for operation, _ in bridge.calls] == [
        "resolve",
        "protect",
        "extract_result",
    ]


class SequencedAttemptBridge(InMemoryOwnedPageBridge):
    def __init__(
        self,
        attempts: list[tuple[OwnedPageAttemptState, str]],
    ) -> None:
        super().__init__()
        self.attempts = attempts

    def extract_result(
        self, request: ExtractOwnedPageResult
    ) -> OwnedPageAttemptResult:
        state, text = self.attempts.pop(0) if len(self.attempts) > 1 else self.attempts[0]
        page = self.pages[request.thread]
        page.attempt_state = state
        page.result_text = text
        return super().extract_result(request)


class DeterministicClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, duration: float) -> None:
        self.now += duration


def test_wait_reuses_result_observation_until_terminal_without_generation_actions() -> None:
    bridge = SequencedAttemptBridge(
        [
            (OwnedPageAttemptState.GENERATING, ""),
            (OwnedPageAttemptState.COMPLETED, "Answer"),
        ]
    )
    clock = DeterministicClock()

    code, payload, stderr = invoke(
        ["session", "result", "abc123", "--wait=5"],
        bridge,
        monotonic=clock.monotonic,
        sleeper=clock.sleep,
    )

    assert (code, payload, stderr) == (
        0,
        {
            "ok": True,
            "session": {"id": "abc123"},
            "attempt": {"state": "completed"},
            "result": {"text": "Answer", "partial": False},
        },
        "",
    )
    assert [operation for operation, _ in bridge.calls] == [
        "resolve",
        "extract_result",
        "extract_result",
        "close_terminal",
    ]


def test_wait_timeout_is_a_successful_observation_not_attempt_failure() -> None:
    bridge = SequencedAttemptBridge(
        [(OwnedPageAttemptState.GENERATING, "CANARY-unstable-fragment")]
    )
    clock = DeterministicClock()

    code, payload, stderr = invoke(
        ["session", "result", "abc123", "--wait=0.5"],
        bridge,
        monotonic=clock.monotonic,
        sleeper=clock.sleep,
    )

    assert (code, payload, stderr) == (
        0,
        {
            "ok": True,
            "session": {"id": "abc123"},
            "attempt": {"state": "generating"},
            "observation": {"outcome": "timed_out"},
            "result": None,
        },
        "",
    )
    assert "CANARY" not in json.dumps(payload)
    assert clock.now == 0.5
    assert "surf-chatgpt-session-abc123" in bridge.pages
    assert {operation for operation, _ in bridge.calls} == {
        "resolve",
        "extract_result",
    }


class FailingAttemptInspectionBridge(InMemoryOwnedPageBridge):
    def classify_attempt(
        self, request: ClassifyOwnedPageAttempt
    ) -> OwnedPageAttemptMetadata:
        self.calls.append(("classify_attempt", request))
        raise OwnedPageInspectionFailed

    def extract_result(
        self, request: ExtractOwnedPageResult
    ) -> OwnedPageAttemptResult:
        self.calls.append(("extract_result", request))
        raise OwnedPageInspectionFailed


class InvalidAttemptMetadataBridge(FailingAttemptInspectionBridge):
    def classify_attempt(
        self, request: ClassifyOwnedPageAttempt
    ) -> OwnedPageAttemptMetadata:
        self.calls.append(("classify_attempt", request))
        raise ValueError("CANARY-content-bearing-metadata")


@pytest.mark.parametrize(
    ("argv", "bridge_type"),
    [
        (["session", "status", "abc123"], FailingAttemptInspectionBridge),
        (["session", "result", "abc123"], FailingAttemptInspectionBridge),
        (["session", "status", "abc123"], InvalidAttemptMetadataBridge),
    ],
)
def test_unclassifiable_ui_preserves_session_without_inventing_attempt_state(
    argv: list[str],
    bridge_type: type[InMemoryOwnedPageBridge],
) -> None:
    bridge = bridge_type()

    code, payload, stderr = invoke(argv, bridge)

    assert code == 1
    assert payload["session"] == {"id": "abc123"}
    assert payload["error"]["type"] == "inspection_failed"
    assert "attempt" not in payload
    assert "result" not in payload
    assert "CANARY" not in json.dumps(payload)
    assert stderr == ""


class FailingTerminalCloseBridge(InMemoryOwnedPageBridge):
    def close_terminal(self, request: CloseTerminalOwnedPage) -> None:
        self.calls.append(("close_terminal", request))
        raise BridgeUnavailable("CANARY-private-close-failure")


class PagePresentAtFlush(io.StringIO):
    def __init__(self, bridge: InMemoryOwnedPageBridge, thread: str) -> None:
        super().__init__()
        self.bridge = bridge
        self.thread = thread
        self.flush_observed_page = False

    def flush(self) -> None:
        self.flush_observed_page = self.thread in self.bridge.pages
        super().flush()


def test_terminal_json_flushes_before_cleanup_and_survives_close_failure() -> None:
    bridge = FailingTerminalCloseBridge()
    thread = "surf-chatgpt-session-abc123"
    bridge.pages[thread] = MemoryPage(
        owner="surf-chatgpt",
        reference=OwnedPageRef(thread, 701, "https://chatgpt.com/c/abc123"),
        protection=None,
        inspection_state=OwnedPageInspectionState.SESSION,
        attempt_state=OwnedPageAttemptState.COMPLETED,
        result_text="Answer",
    )
    output = PagePresentAtFlush(bridge, thread)

    code = cli.main(
        ["session", "result", "abc123"],
        stdout=output,
        stderr=io.StringIO(),
        lifecycle=OwnedPageSessionLifecycle(bridge),
    )
    payload = json.loads(output.getvalue())

    assert code == 0
    assert payload["result"] == {"text": "Answer", "partial": False}
    assert "CANARY" not in json.dumps(payload)
    assert output.flush_observed_page is True
    assert thread in bridge.pages
    assert [operation for operation, _ in bridge.calls] == [
        "resolve",
        "extract_result",
        "close_terminal",
    ]


def test_session_handoff_reuses_existing_retained_protection_idempotently() -> None:
    bridge = InMemoryOwnedPageBridge()
    thread = "surf-chatgpt-session-abc123"
    bridge.pages[thread] = MemoryPage(
        owner="surf-chatgpt",
        reference=OwnedPageRef(
            thread,
            701,
            "https://chatgpt.com/c/abc123",
        ),
        protection=OwnedPageProtection.EXPLICITLY_RETAINED,
        inspection_state=OwnedPageInspectionState.SESSION,
    )

    code, payload, _ = invoke(["session", "handoff", "abc123"], bridge)

    assert code == 0
    assert payload["session"] == {"id": "abc123"}
    assert bridge.pages[thread].reference.page_token == 701
    assert bridge.pages[thread].protection is OwnedPageProtection.EXPLICITLY_RETAINED
    assert [operation for operation, _ in bridge.calls] == ["resolve"]


class AmbiguousSessionBridge(InMemoryOwnedPageBridge):
    def resolve(self, request: ResolveOwnedPage) -> ResolvedOwnedPage:
        self.calls.append(("resolve", request))
        raise OwnedPageAmbiguousSession


def test_session_handoff_projects_ambiguous_recovery_without_protection() -> None:
    bridge = AmbiguousSessionBridge()

    code, payload, _ = invoke(["session", "handoff", "abc123"], bridge)

    assert code == 1
    assert payload["error"]["type"] == "ambiguous_session_page"
    assert payload["session"] == {"id": "abc123"}
    assert "handoff" not in payload
    assert [operation for operation, _ in bridge.calls] == ["resolve"]


class DisconnectedProtectionBridge(InMemoryOwnedPageBridge):
    def protect(self, request: ProtectOwnedPage) -> None:
        self.calls.append(("protect", request))
        raise BridgeUnavailable("private bridge failure")


def test_session_handoff_preserves_durable_identity_when_protection_fails() -> None:
    bridge = DisconnectedProtectionBridge()

    code, payload, stderr = invoke(["session", "handoff", "abc123"], bridge)

    assert code == 1
    assert payload["error"]["type"] == "browser_unavailable"
    assert payload["session"] == {"id": "abc123"}
    assert "handoff" not in payload
    assert stderr == ""
    assert [operation for operation, _ in bridge.calls] == ["resolve", "protect"]


def test_session_current_missing_thread_fails_without_allocating_or_recovering() -> (
    None
):
    bridge = InMemoryOwnedPageBridge()

    code, payload, stderr = invoke(
        ["session", "current", "--thread", "missing"], bridge
    )

    assert code == 1
    assert payload["error"]["type"] == "thread_not_found"
    assert stderr == ""
    assert bridge.pages == {}
    assert [operation for operation, _ in bridge.calls] == ["inspect"]


def test_session_current_fails_closed_for_an_unrecognized_in_scope_url() -> None:
    bridge = InMemoryOwnedPageBridge()
    bridge.pages["preserved"] = MemoryPage(
        owner="surf-chatgpt",
        reference=OwnedPageRef(
            thread="preserved",
            page_token=701,
            exact_url="https://chatgpt.com/c/abc123?private=canary",
        ),
        protection=OwnedPageProtection.HUMAN_INTERVENTION,
        inspection_state=OwnedPageInspectionState.SESSION,
    )

    code, payload, stderr = invoke(
        ["session", "current", "--thread", "preserved"], bridge
    )

    assert code == 1
    assert payload["error"]["type"] == "inspection_failed"
    assert "canary" not in json.dumps(payload)
    assert stderr == ""
    assert [operation for operation, _ in bridge.calls] == ["inspect"]


def test_session_current_requires_affirmative_gate_metadata() -> None:
    bridge = InMemoryOwnedPageBridge()
    bridge.pages["preserved"] = MemoryPage(
        owner="surf-chatgpt",
        reference=OwnedPageRef(
            thread="preserved",
            page_token=701,
            exact_url="https://chatgpt.com/",
        ),
        protection=OwnedPageProtection.HUMAN_INTERVENTION,
        inspection_state=OwnedPageInspectionState.UNRECOGNIZED,
    )

    code, payload, stderr = invoke(
        ["session", "current", "--thread", "preserved"], bridge
    )

    assert code == 1
    assert payload["error"]["type"] == "inspection_failed"
    assert stderr == ""


class FailedInspectionBridge(InMemoryOwnedPageBridge):
    def inspect(self, request: InspectOwnedPage) -> OwnedPageInspection:
        self.calls.append(("inspect", request))
        raise OwnedPageInspectionFailed


def test_session_current_projects_classifier_failure_without_browser_details() -> None:
    bridge = FailedInspectionBridge()

    code, payload, stderr = invoke(
        ["session", "current", "--thread", "preserved"], bridge
    )

    assert code == 1
    assert payload["error"]["type"] == "inspection_failed"
    assert stderr == ""


class UnsupportedBridge:
    def __init__(self) -> None:
        self.capability_checks = 0

    def capabilities(self) -> OwnedPageCapabilities:
        self.capability_checks += 1
        return OwnedPageCapabilities()

    def allocate(self, request: AllocateOwnedPage) -> OwnedPageRef:
        raise AssertionError("unsupported capability must fail before allocation")

    def inspect(self, request: InspectOwnedPage) -> OwnedPageInspection:
        raise AssertionError("unsupported capability must fail before inspection")

    def rebind(self, request: RebindOwnedPage) -> OwnedPageRef:
        raise AssertionError("unsupported capability must fail before rebinding")

    def protect(self, request: ProtectOwnedPage) -> None:
        raise AssertionError("unsupported capability must fail before protection")


def test_unsupported_backend_fails_login_and_current_before_any_browser_operation() -> (
    None
):
    bridge = UnsupportedBridge()

    login = invoke(["login"], bridge)
    current = invoke(["session", "current", "--thread", "preserved"], bridge)

    assert login[0] == 1
    assert login[1]["error"]["type"] == "unsupported_browser_capability"
    assert current[0] == 1
    assert current[1]["error"]["type"] == "unsupported_browser_capability"
    assert bridge.capability_checks == 2


class ConflictingLoginBridge(InMemoryOwnedPageBridge):
    def allocate(self, request: AllocateOwnedPage) -> OwnedPageRef:
        self.calls.append(("allocate", request))
        raise OwnedPageOwnershipConflict


def test_login_ownership_conflict_is_safe_and_preserves_the_existing_page() -> None:
    bridge = ConflictingLoginBridge()

    code, payload, stderr = invoke(["login"], bridge)

    assert code == 1
    assert payload["error"]["type"] == "ownership_conflict"
    assert stderr == ""
    assert [operation for operation, _ in bridge.calls] == ["allocate"]


class UnprovenIdentityBridge(InMemoryOwnedPageBridge):
    def allocate(self, request: AllocateOwnedPage) -> OwnedPageRef:
        self.calls.append(("allocate", request))
        raise BridgeIdentityUnproven("Patchright bridge identity is unproven")


def test_login_fails_closed_when_the_bridge_profile_identity_is_unproven() -> None:
    bridge = UnprovenIdentityBridge()

    code, payload, stderr = invoke(["login"], bridge)

    assert code == 1
    assert payload["error"]["type"] == "browser_identity_unproven"
    assert stderr == ""
    assert [operation for operation, _ in bridge.calls] == ["allocate"]


def test_login_never_reuses_a_conversation_page_bound_to_the_login_thread() -> None:
    bridge = InMemoryOwnedPageBridge()
    reference = OwnedPageRef(
        thread="surf-chatgpt-login",
        page_token=702,
        exact_url="https://chatgpt.com/c/existing",
    )
    bridge.pages["surf-chatgpt-login"] = MemoryPage(
        owner="surf-chatgpt",
        reference=reference,
        protection=OwnedPageProtection.HUMAN_INTERVENTION,
        inspection_state=OwnedPageInspectionState.SESSION,
    )

    code, payload, stderr = invoke(["login"], bridge)

    assert code == 1
    assert payload["error"]["type"] == "ownership_conflict"
    assert stderr == ""
    assert bridge.pages["surf-chatgpt-login"].reference is reference
