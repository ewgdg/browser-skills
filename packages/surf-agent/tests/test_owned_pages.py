from __future__ import annotations

import json
from collections.abc import Callable
from types import SimpleNamespace

import pytest

from surf_agent.owned_pages import (
    AbandonOwnedPage,
    AllocateOwnedPage,
    ClassifyOwnedPageAttempt,
    CloseTerminalOwnedPage,
    CloseOwnedDiscoveryPage,
    DiscoverOwnedPageSessions,
    ExtractOwnedPageResult,
    InspectOwnedPage,
    ObserveOwnedPageAssignment,
    OwnedPageAssignmentObservation,
    OwnedPageAssignmentState,
    OwnedPageAttemptMetadata,
    OwnedPageAttemptResult,
    OwnedPageAttemptState,
    OwnedPageAmbiguousSession,
    OwnedPageAllocationPolicy,
    OwnedPageAbandonment,
    OwnedPageCapacity,
    OwnedPageCapacityExceeded,
    OwnedPageClassifier,
    OwnedPageInspectionState,
    OwnedPageNotFound,
    OwnedPageProtection,
    OwnedPageProgram,
    OwnedPagePromptSubmission,
    OwnedPageRecentSession,
    OwnedPageRecentSessions,
    OwnedPageRecentSessionsState,
    OwnedPageRef,
    OwnedPageRetainedPage,
    OwnedPageRetentionReason,
    OwnedPageScope,
    OwnedPageSelection,
    OwnedPageSelectionDimension,
    OwnedPageSubmissionPreparation,
    OwnedPageSubmissionAlreadyAttempted,
    OwnedPagePreparationState,
    OwnedPageSubmissionState,
    OwnedPageCapabilities,
    PatchrightOwnedPageBridge,
    ProtectOwnedPage,
    PrepareOwnedPageSubmission,
    RebindOwnedPage,
    REQUIRED_OWNED_PAGE_CAPABILITIES,
    ResolveOwnedPage,
    ResolvedOwnedPage,
    UnsupportedOwnedPageCapability,
    UnsupportedOwnedPageBridge,
    SubmitOwnedPagePrompt,
    create_owned_page_bridge,
)


CLASSIFIER = OwnedPageClassifier("() => ({state: 'session'})")
ALLOCATION_POLICY = OwnedPageAllocationPolicy(
    limit=10,
    sweep_program=OwnedPageProgram("() => ({state: 'generating'})"),
)


class NoBrowserCallsClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def call_tool(self, name: str, args: object = None) -> str:
        self.calls.append((name, args))
        raise AssertionError("capability checks must not start or inspect the browser")


def test_patchright_affirms_the_complete_owned_page_capability_contract_without_browser_work() -> (
    None
):
    client = NoBrowserCallsClient()
    bridge = PatchrightOwnedPageBridge(client)

    capabilities = bridge.capabilities()

    assert capabilities.available == REQUIRED_OWNED_PAGE_CAPABILITIES
    assert capabilities.supported is True
    assert client.calls == []


def test_unsupported_backend_affirms_no_owned_page_capability_without_browser_work() -> (
    None
):
    bridge = UnsupportedOwnedPageBridge()

    capabilities = bridge.capabilities()

    assert capabilities == OwnedPageCapabilities()


def test_axi_selection_returns_the_unsupported_bridge_without_touching_its_browser_client() -> (
    None
):
    client = NoBrowserCallsClient()
    bridge = create_owned_page_bridge(
        SimpleNamespace(backend="axi", patchright_client=client)
    )

    assert isinstance(bridge, UnsupportedOwnedPageBridge)
    assert bridge.capabilities() == OwnedPageCapabilities()
    assert client.calls == []


class ScriptedClient:
    def __init__(self, result: dict[str, object]) -> None:
        self.result = result
        self.calls: list[tuple[str, object]] = []

    def call_tool(self, name: str, args: object = None) -> str:
        self.calls.append((name, args))
        return json.dumps(self.result)

    def call_tool_if_running(
        self,
        name: str,
        args: object = None,
        *,
        on_request_may_have_been_dispatched: Callable[[], None] | None = None,
    ) -> str:
        self.calls.append((name, args))
        if on_request_may_have_been_dispatched is not None:
            on_request_may_have_been_dispatched()
        return json.dumps(self.result)


def test_patchright_owned_allocation_uses_the_typed_bridge_transport() -> None:
    client = ScriptedClient(
        {
            "ok": True,
            "page": {
                "thread": "surf-chatgpt-login",
                "page_token": 7,
                "url": "https://chatgpt.com/",
            },
        }
    )
    bridge = PatchrightOwnedPageBridge(client)
    request = AllocateOwnedPage(
        owner="surf-chatgpt",
        thread="surf-chatgpt-login",
        url="https://chatgpt.com/",
        allowed_scope=OwnedPageScope.CHATGPT_PRE_SESSION,
        policy=ALLOCATION_POLICY,
        expected_protection=OwnedPageProtection.HUMAN_INTERVENTION,
        protection=OwnedPageProtection.HUMAN_INTERVENTION,
    )

    page = bridge.allocate(request)

    assert page == OwnedPageRef(
        thread="surf-chatgpt-login",
        page_token=7,
        exact_url="https://chatgpt.com/",
    )
    assert client.calls == [
        (
            "owned-allocate",
            {
                "owner": "surf-chatgpt",
                "thread": "surf-chatgpt-login",
                "url": "https://chatgpt.com/",
                "allowed_scope": "chatgpt_pre_session",
                "expected_protection": "human_intervention",
                "protection": "human_intervention",
                "capacity_limit": 10,
                "sweep_program": ALLOCATION_POLICY.sweep_program.source,
            },
        )
    ]


def test_patchright_recent_discovery_uses_a_narrow_title_bearing_transport() -> None:
    client = ScriptedClient(
        {
            "ok": True,
            "page": {
                "thread": "surf-chatgpt-discovery-safe",
                "page_token": 7,
                "url": "https://chatgpt.com/",
            },
            "metadata": {
                "state": "sessions",
                "sessions": [
                    {"id": "first", "title": "First visible title"},
                    {"id": "second", "title": "Second visible title"},
                ],
            },
        }
    )
    bridge = PatchrightOwnedPageBridge(client)
    request = DiscoverOwnedPageSessions(
        owner="surf-chatgpt",
        thread="surf-chatgpt-discovery-safe",
        expected_page_token=7,
        allowed_scope=OwnedPageScope.CHATGPT_PRE_SESSION,
        expected_protection=None,
        program=OwnedPageProgram("() => ({state: 'sessions', sessions: []})"),
    )

    discovery = bridge.discover_sessions(request)

    assert discovery == OwnedPageRecentSessions(
        page=OwnedPageRef(
            thread="surf-chatgpt-discovery-safe",
            page_token=7,
            exact_url="https://chatgpt.com/",
        ),
        state=OwnedPageRecentSessionsState.SESSIONS,
        sessions=(
            OwnedPageRecentSession(id="first", title="First visible title"),
            OwnedPageRecentSession(id="second", title="Second visible title"),
        ),
    )
    assert client.calls == [
        (
            "owned-discover-sessions",
            {
                "owner": "surf-chatgpt",
                "thread": "surf-chatgpt-discovery-safe",
                "expected_page_token": 7,
                "allowed_scope": "chatgpt_pre_session",
                "expected_protection": None,
                "program": request.program.source,
            },
        )
    ]


def test_patchright_discovery_close_uses_the_captured_page_guards() -> None:
    client = ScriptedClient({"ok": True})
    bridge = PatchrightOwnedPageBridge(client)
    request = CloseOwnedDiscoveryPage(
        owner="surf-chatgpt",
        thread="surf-chatgpt-discovery-safe",
        expected_page_token=7,
        allowed_scope=OwnedPageScope.CHATGPT_PRE_SESSION,
        expected_protection=None,
    )

    bridge.close_discovery(request)

    assert client.calls == [
        (
            "owned-close-discovery",
            {
                "owner": "surf-chatgpt",
                "thread": "surf-chatgpt-discovery-safe",
                "expected_page_token": 7,
                "allowed_scope": "chatgpt_pre_session",
                "expected_protection": None,
            },
        )
    ]


@pytest.mark.parametrize(
    "metadata",
    [
        {"state": "ui_changed", "sessions": []},
        {
            "state": "sessions",
            "sessions": [{"id": "abc", "title": "Visible", "text": "CANARY"}],
        },
        {
            "state": "sessions",
            "sessions": [
                {"id": "duplicate", "title": "First"},
                {"id": "duplicate", "title": "Second"},
            ],
        },
        {
            "state": "sessions",
            "sessions": [{"id": "abc?private=CANARY", "title": "Visible"}],
        },
        {
            "state": "sessions",
            "sessions": [
                {"id": f"session-{index}", "title": f"Title {index}"}
                for index in range(11)
            ],
        },
    ],
)
def test_recent_discovery_rejects_unbounded_or_content_bearing_bridge_metadata(
    metadata: dict[str, object],
) -> None:
    client = ScriptedClient(
        {
            "ok": True,
            "page": {
                "thread": "surf-chatgpt-discovery-safe",
                "page_token": 7,
                "url": "https://chatgpt.com/",
            },
            "metadata": metadata,
        }
    )
    bridge = PatchrightOwnedPageBridge(client)
    request = DiscoverOwnedPageSessions(
        owner="surf-chatgpt",
        thread="surf-chatgpt-discovery-safe",
        expected_page_token=7,
        allowed_scope=OwnedPageScope.CHATGPT_PRE_SESSION,
        expected_protection=None,
        program=OwnedPageProgram("discover-rendered-chats"),
    )

    with pytest.raises(ValueError) as caught:
        bridge.discover_sessions(request)

    assert "CANARY" not in str(caught.value)


@pytest.mark.parametrize(
    "page",
    [
        {
            "thread": "surf-chatgpt-discovery-replaced",
            "page_token": 7,
            "url": "https://chatgpt.com/",
        },
        {
            "thread": "surf-chatgpt-discovery-safe",
            "page_token": 8,
            "url": "https://chatgpt.com/",
        },
        {
            "thread": "surf-chatgpt-discovery-safe",
            "page_token": 7,
            "url": "https://chatgpt.com/c/replaced",
        },
    ],
)
def test_recent_discovery_rejects_a_bridge_result_for_a_different_page(
    page: dict[str, object],
) -> None:
    client = ScriptedClient(
        {
            "ok": True,
            "page": page,
            "metadata": {"state": "sessions", "sessions": []},
        }
    )
    bridge = PatchrightOwnedPageBridge(client)
    request = DiscoverOwnedPageSessions(
        owner="surf-chatgpt",
        thread="surf-chatgpt-discovery-safe",
        expected_page_token=7,
        allowed_scope=OwnedPageScope.CHATGPT_PRE_SESSION,
        expected_protection=None,
        program=OwnedPageProgram("discover-rendered-chats"),
    )

    with pytest.raises(ValueError):
        bridge.discover_sessions(request)


def test_patchright_capacity_failure_decodes_only_bounded_recovery_metadata() -> None:
    client = ScriptedClient(
        {
            "ok": False,
            "error": "capacity_exceeded",
            "capacity": {
                "limit": 2,
                "retained": [
                    {
                        "thread": "surf-chatgpt-session-abc123",
                        "session_id": "abc123",
                        "reason": "generating",
                    },
                    {"thread": "surf-chatgpt-login", "reason": "human_intervention"},
                ],
            },
        }
    )
    bridge = PatchrightOwnedPageBridge(client)
    request = AllocateOwnedPage(
        owner="surf-chatgpt",
        thread="surf-chatgpt-login",
        url="https://chatgpt.com/",
        allowed_scope=OwnedPageScope.CHATGPT_PRE_SESSION,
        expected_protection=OwnedPageProtection.HUMAN_INTERVENTION,
        protection=OwnedPageProtection.HUMAN_INTERVENTION,
        policy=OwnedPageAllocationPolicy(
            limit=2,
            sweep_program=OwnedPageProgram("() => ({state: 'generating'})"),
        ),
    )

    with pytest.raises(OwnedPageCapacityExceeded) as caught:
        bridge.allocate(request)

    assert caught.value.capacity == OwnedPageCapacity(
        limit=2,
        retained=(
            OwnedPageRetainedPage(
                session_id="abc123",
                thread="surf-chatgpt-session-abc123",
                reason=OwnedPageRetentionReason.GENERATING,
            ),
            OwnedPageRetainedPage(
                session_id=None,
                thread="surf-chatgpt-login",
                reason=OwnedPageRetentionReason.HUMAN_INTERVENTION,
            ),
        ),
    )
    assert client.calls == [
        (
            "owned-allocate",
            {
                "owner": "surf-chatgpt",
                "thread": "surf-chatgpt-login",
                "url": "https://chatgpt.com/",
                "allowed_scope": "chatgpt_pre_session",
                "expected_protection": "human_intervention",
                "protection": "human_intervention",
                "capacity_limit": 2,
                "sweep_program": request.policy.sweep_program.source,
            },
        )
    ]


def test_patchright_abandonment_uses_one_typed_guarded_transaction() -> None:
    client = ScriptedClient({"ok": True, "attempt_state": "stopped"})
    bridge = PatchrightOwnedPageBridge(client)
    request = AbandonOwnedPage(
        owner="surf-chatgpt",
        thread="surf-chatgpt-session-abc123",
        expected_exact_url="https://chatgpt.com/c/abc123",
        allowed_scope=OwnedPageScope.CHATGPT,
        classify_program=OwnedPageProgram("() => ({state: 'generating'})"),
        stop_program=OwnedPageProgram("() => ({state: 'stop_requested'})"),
        stop_confirmation_timeout_seconds=10.0,
        stop_confirmation_poll_interval_seconds=0.1,
    )

    outcome = bridge.abandon(request)

    assert outcome == OwnedPageAbandonment(attempt_state=OwnedPageAttemptState.STOPPED)
    assert client.calls == [
        (
            "owned-abandon",
            {
                "owner": "surf-chatgpt",
                "thread": "surf-chatgpt-session-abc123",
                "expected_exact_url": "https://chatgpt.com/c/abc123",
                "allowed_scope": "chatgpt",
                "classify_program": request.classify_program.source,
                "stop_program": request.stop_program.source,
                "stop_confirmation_timeout_seconds": 10.0,
                "stop_confirmation_poll_interval_seconds": 0.1,
            },
        )
    ]


@pytest.mark.parametrize(
    "result",
    [
        {
            "ok": False,
            "error": "capacity_exceeded",
            "capacity": {
                "limit": 10,
                "retained": [
                    {
                        "session_id": "abc123",
                        "thread": "surf-chatgpt-session-abc123",
                        "reason": "generating",
                        "title": "CANARY-private-title",
                    }
                ],
            },
        },
        {
            "ok": True,
            "attempt_state": "stopped",
            "text": "CANARY-private-response",
        },
        {
            "ok": False,
            "error": "abandonment_failed",
            "text": "CANARY-private-response",
        },
    ],
)
def test_cleanup_transport_rejects_content_bearing_or_unexpected_metadata(
    result: dict[str, object],
) -> None:
    bridge = PatchrightOwnedPageBridge(ScriptedClient(result))

    if result.get("capacity") is not None:
        request = AllocateOwnedPage(
            owner="surf-chatgpt",
            thread="surf-chatgpt-login",
            url="https://chatgpt.com/",
            allowed_scope=OwnedPageScope.CHATGPT_PRE_SESSION,
            policy=ALLOCATION_POLICY,
            expected_protection=OwnedPageProtection.HUMAN_INTERVENTION,
            protection=OwnedPageProtection.HUMAN_INTERVENTION,
        )
    else:
        request = AbandonOwnedPage(
            owner="surf-chatgpt",
            thread="surf-chatgpt-session-abc123",
            expected_exact_url="https://chatgpt.com/c/abc123",
            allowed_scope=OwnedPageScope.CHATGPT,
            classify_program=OwnedPageProgram("classify"),
            stop_program=OwnedPageProgram("stop"),
            stop_confirmation_timeout_seconds=10.0,
            stop_confirmation_poll_interval_seconds=0.1,
        )

    with pytest.raises(ValueError) as caught:
        if result.get("capacity") is not None:
            bridge.allocate(request)
        else:
            bridge.abandon(request)

    assert "CANARY" not in str(caught.value)


def test_unsupported_backend_rejects_allocation_without_browser_work() -> None:
    bridge = UnsupportedOwnedPageBridge()
    request = AllocateOwnedPage(
        owner="surf-chatgpt",
        thread="surf-chatgpt-login",
        url="https://chatgpt.com/",
        allowed_scope=OwnedPageScope.CHATGPT,
        policy=ALLOCATION_POLICY,
        expected_protection=OwnedPageProtection.HUMAN_INTERVENTION,
        protection=OwnedPageProtection.HUMAN_INTERVENTION,
    )

    with pytest.raises(UnsupportedOwnedPageCapability):
        bridge.allocate(request)


def test_patchright_session_resolution_uses_one_typed_bridge_transaction() -> None:
    client = ScriptedClient(
        {
            "ok": True,
            "page": {
                "thread": "surf-chatgpt-session-abc123",
                "page_token": 12,
                "url": "https://chatgpt.com/c/abc123",
            },
            "protection": "explicitly_retained",
        }
    )
    bridge = PatchrightOwnedPageBridge(client)

    resolved = bridge.resolve(
        ResolveOwnedPage(
            owner="surf-chatgpt",
            thread="surf-chatgpt-session-abc123",
            exact_url="https://chatgpt.com/c/abc123",
            allowed_scope=OwnedPageScope.CHATGPT,
        )
    )

    assert resolved == ResolvedOwnedPage(
        page=OwnedPageRef(
            thread="surf-chatgpt-session-abc123",
            page_token=12,
            exact_url="https://chatgpt.com/c/abc123",
        ),
        protection=OwnedPageProtection.EXPLICITLY_RETAINED,
    )
    assert client.calls == [
        (
            "owned-resolve",
            {
                "owner": "surf-chatgpt",
                "thread": "surf-chatgpt-session-abc123",
                "exact_url": "https://chatgpt.com/c/abc123",
                "allowed_scope": "chatgpt",
            },
        )
    ]


def test_patchright_session_resolution_projects_ambiguous_exact_matches() -> None:
    bridge = PatchrightOwnedPageBridge(
        ScriptedClient({"ok": False, "error": "ambiguous_session_page"})
    )

    with pytest.raises(OwnedPageAmbiguousSession):
        bridge.resolve(
            ResolveOwnedPage(
                owner="surf-chatgpt",
                thread="surf-chatgpt-session-abc123",
                exact_url="https://chatgpt.com/c/abc123",
                allowed_scope=OwnedPageScope.CHATGPT,
            )
        )


def test_patchright_inspection_addresses_only_the_live_thread_without_starting_a_bridge() -> (
    None
):
    client = ScriptedClient(
        {
            "ok": True,
            "page": {
                "thread": "preserved",
                "page_token": 9,
                "url": "https://chatgpt.com/c/abc123",
            },
            "metadata": {"state": "session"},
        }
    )
    bridge = PatchrightOwnedPageBridge(client)

    page = bridge.inspect(
        InspectOwnedPage(
            owner="surf-chatgpt",
            thread="preserved",
            allowed_scope=OwnedPageScope.CHATGPT,
            classifier=CLASSIFIER,
        )
    )

    assert page.page.exact_url == "https://chatgpt.com/c/abc123"
    assert page.state is OwnedPageInspectionState.SESSION
    assert client.calls == [
        (
            "owned-inspect",
            {
                "owner": "surf-chatgpt",
                "thread": "preserved",
                "allowed_scope": "chatgpt",
                "classifier": CLASSIFIER.source,
            },
        )
    ]


def test_patchright_attempt_classification_uses_guarded_metadata_only_transport() -> (
    None
):
    client = ScriptedClient(
        {
            "ok": True,
            "page": {
                "thread": "surf-chatgpt-session-abc123",
                "page_token": 9,
                "url": "https://chatgpt.com/c/abc123",
            },
            "metadata": {"state": "generating"},
        }
    )
    bridge = PatchrightOwnedPageBridge(client)
    request = ClassifyOwnedPageAttempt(
        owner="surf-chatgpt",
        thread="surf-chatgpt-session-abc123",
        expected_page_token=9,
        expected_exact_url="https://chatgpt.com/c/abc123",
        allowed_scope=OwnedPageScope.CHATGPT,
        expected_protection=None,
        program=OwnedPageProgram("() => ({state: 'generating'})"),
    )

    observation = bridge.classify_attempt(request)

    assert observation == OwnedPageAttemptMetadata(
        page=OwnedPageRef(
            thread="surf-chatgpt-session-abc123",
            page_token=9,
            exact_url="https://chatgpt.com/c/abc123",
        ),
        state=OwnedPageAttemptState.GENERATING,
    )
    assert client.calls == [
        (
            "owned-classify-attempt",
            {
                "owner": "surf-chatgpt",
                "thread": "surf-chatgpt-session-abc123",
                "expected_page_token": 9,
                "expected_exact_url": "https://chatgpt.com/c/abc123",
                "allowed_scope": "chatgpt",
                "expected_protection": None,
                "program": request.program.source,
            },
        )
    ]


def test_patchright_explicit_result_transport_allows_terminal_response_text() -> None:
    client = ScriptedClient(
        {
            "ok": True,
            "page": {
                "thread": "surf-chatgpt-session-abc123",
                "page_token": 9,
                "url": "https://chatgpt.com/c/abc123",
            },
            "metadata": {"state": "stopped", "text": "Partial answer"},
        }
    )
    bridge = PatchrightOwnedPageBridge(client)
    request = ExtractOwnedPageResult(
        owner="surf-chatgpt",
        thread="surf-chatgpt-session-abc123",
        expected_page_token=9,
        expected_exact_url="https://chatgpt.com/c/abc123",
        allowed_scope=OwnedPageScope.CHATGPT,
        expected_protection=None,
        program=OwnedPageProgram("() => ({state: 'stopped', text: 'Partial answer'})"),
    )

    result = bridge.extract_result(request)

    assert result == OwnedPageAttemptResult(
        page=OwnedPageRef(
            thread="surf-chatgpt-session-abc123",
            page_token=9,
            exact_url="https://chatgpt.com/c/abc123",
        ),
        state=OwnedPageAttemptState.STOPPED,
        text="Partial answer",
    )
    assert client.calls[0][0] == "owned-extract-result"


def test_patchright_terminal_close_transport_carries_all_guards_and_no_content() -> (
    None
):
    client = ScriptedClient({"ok": True})
    bridge = PatchrightOwnedPageBridge(client)
    request = CloseTerminalOwnedPage(
        owner="surf-chatgpt",
        thread="surf-chatgpt-session-abc123",
        expected_page_token=9,
        expected_exact_url="https://chatgpt.com/c/abc123",
        allowed_scope=OwnedPageScope.CHATGPT,
        expected_protection=None,
        program=OwnedPageProgram("() => ({state: 'completed'})"),
        expected_state=OwnedPageAttemptState.COMPLETED,
    )

    bridge.close_terminal(request)

    assert client.calls == [
        (
            "owned-close-terminal",
            {
                "owner": "surf-chatgpt",
                "thread": "surf-chatgpt-session-abc123",
                "expected_page_token": 9,
                "expected_exact_url": "https://chatgpt.com/c/abc123",
                "allowed_scope": "chatgpt",
                "expected_protection": None,
                "program": request.program.source,
                "expected_state": "completed",
            },
        )
    ]


class StoppedBridgeClient(NoBrowserCallsClient):
    def call_tool_if_running(
        self,
        name: str,
        args: object = None,
        *,
        on_request_may_have_been_dispatched: Callable[[], None] | None = None,
    ) -> None:
        _ = on_request_may_have_been_dispatched
        self.calls.append((name, args))
        return None


def test_patchright_inspection_of_a_stopped_bridge_is_thread_not_found_without_starting_it() -> (
    None
):
    client = StoppedBridgeClient()
    bridge = PatchrightOwnedPageBridge(client)

    with pytest.raises(OwnedPageNotFound):
        bridge.inspect(
            InspectOwnedPage(
                owner="surf-chatgpt",
                thread="missing",
                allowed_scope=OwnedPageScope.CHATGPT,
                classifier=CLASSIFIER,
            )
        )

    assert client.calls == [
        (
            "owned-inspect",
            {
                "owner": "surf-chatgpt",
                "thread": "missing",
                "allowed_scope": "chatgpt",
                "classifier": CLASSIFIER.source,
            },
        )
    ]


def test_patchright_submission_preparation_does_not_restart_a_stopped_bridge() -> None:
    client = StoppedBridgeClient()
    bridge = PatchrightOwnedPageBridge(client)

    with pytest.raises(OwnedPageNotFound):
        bridge.prepare_submission(
            PrepareOwnedPageSubmission(
                owner="surf-chatgpt",
                thread="temporary",
                expected_page_token=11,
                allowed_scope=OwnedPageScope.CHATGPT,
                expected_protection=None,
                program=OwnedPageProgram("() => ({state: 'ready', selection: {}})"),
                requested_selection_dimensions=frozenset(),
            )
        )

    assert client.calls[0][0] == "owned-prepare-submission"


def test_patchright_rebind_sends_all_identity_guards_through_one_bridge_call() -> None:
    client = ScriptedClient(
        {
            "ok": True,
            "page": {
                "thread": "surf-chatgpt-session-abc123",
                "page_token": 5,
                "url": "https://chatgpt.com/c/abc123",
            },
        }
    )
    bridge = PatchrightOwnedPageBridge(client)

    page = bridge.rebind(
        RebindOwnedPage(
            owner="surf-chatgpt",
            source_thread="temporary",
            destination_thread="surf-chatgpt-session-abc123",
            expected_page_token=5,
            expected_exact_url="https://chatgpt.com/c/abc123",
            allowed_scope=OwnedPageScope.CHATGPT,
            expected_protection=OwnedPageProtection.HUMAN_INTERVENTION,
        )
    )

    assert page.thread == "surf-chatgpt-session-abc123"
    assert client.calls == [
        (
            "owned-rebind",
            {
                "owner": "surf-chatgpt",
                "source_thread": "temporary",
                "destination_thread": "surf-chatgpt-session-abc123",
                "expected_page_token": 5,
                "expected_exact_url": "https://chatgpt.com/c/abc123",
                "allowed_scope": "chatgpt",
                "expected_protection": "human_intervention",
            },
        )
    ]


def test_patchright_protection_update_carries_live_identity_and_metadata_guards() -> (
    None
):
    client = ScriptedClient({"ok": True})
    bridge = PatchrightOwnedPageBridge(client)

    bridge.protect(
        ProtectOwnedPage(
            owner="surf-chatgpt",
            thread="preserved",
            expected_page_token=8,
            allowed_scope=OwnedPageScope.CHATGPT,
            expected_protection=OwnedPageProtection.HUMAN_INTERVENTION,
            protection=OwnedPageProtection.EXPLICITLY_RETAINED,
        )
    )

    assert client.calls == [
        (
            "owned-protect",
            {
                "owner": "surf-chatgpt",
                "thread": "preserved",
                "expected_page_token": 8,
                "allowed_scope": "chatgpt",
                "expected_protection": "human_intervention",
                "protection": "explicitly_retained",
            },
        )
    ]


def test_patchright_submission_preparation_uses_typed_wire_and_decodes_requested_selection() -> (
    None
):
    client = ScriptedClient(
        {
            "ok": True,
            "page": {
                "thread": "temporary",
                "page_token": 11,
                "url": "https://chatgpt.com/",
            },
            "metadata": {
                "state": "ready",
                "selection": {"model": "GPT-5.6", "thinking": "Pro"},
            },
        }
    )
    bridge = PatchrightOwnedPageBridge(client)
    program = OwnedPageProgram("() => ({state: 'ready', selection: {}})")

    outcome = bridge.prepare_submission(
        PrepareOwnedPageSubmission(
            owner="surf-chatgpt",
            thread="temporary",
            expected_page_token=11,
            allowed_scope=OwnedPageScope.CHATGPT,
            expected_protection=None,
            program=program,
            requested_selection_dimensions=frozenset(
                {
                    OwnedPageSelectionDimension.MODEL,
                    OwnedPageSelectionDimension.THINKING,
                }
            ),
        )
    )

    assert outcome == OwnedPageSubmissionPreparation(
        page=OwnedPageRef("temporary", 11, "https://chatgpt.com/"),
        state=OwnedPagePreparationState.READY,
        selection=(
            OwnedPageSelection(OwnedPageSelectionDimension.MODEL, "GPT-5.6"),
            OwnedPageSelection(OwnedPageSelectionDimension.THINKING, "Pro"),
        ),
    )
    assert client.calls == [
        (
            "owned-prepare-submission",
            {
                "owner": "surf-chatgpt",
                "thread": "temporary",
                "expected_page_token": 11,
                "allowed_scope": "chatgpt",
                "expected_protection": None,
                "program": program.source,
                "requested_selection_dimensions": ["model", "thinking"],
            },
        )
    ]


def test_patchright_prompt_submission_sends_readiness_and_mutation_programs_in_one_call() -> (
    None
):
    client = ScriptedClient(
        {
            "ok": True,
            "page": {
                "thread": "temporary",
                "page_token": 11,
                "url": "https://chatgpt.com/",
            },
            "metadata": {"state": "submitted"},
        }
    )
    bridge = PatchrightOwnedPageBridge(client)
    readiness = OwnedPageProgram("() => ({state: 'ready', selection: {}})")
    submission = OwnedPageProgram("() => ({state: 'submitted'})")
    dispatches: list[bool] = []

    outcome = bridge.submit_prompt(
        SubmitOwnedPagePrompt(
            owner="surf-chatgpt",
            thread="temporary",
            expected_page_token=11,
            allowed_scope=OwnedPageScope.CHATGPT,
            expected_protection=None,
            readiness_program=readiness,
            submission_program=submission,
        ),
        on_send_may_have_occurred=lambda: dispatches.append(True),
    )

    assert outcome == OwnedPagePromptSubmission(
        page=OwnedPageRef("temporary", 11, "https://chatgpt.com/"),
        state=OwnedPageSubmissionState.SUBMITTED,
    )
    assert dispatches == [True]
    assert client.calls == [
        (
            "owned-submit-prompt",
            {
                "owner": "surf-chatgpt",
                "thread": "temporary",
                "expected_page_token": 11,
                "allowed_scope": "chatgpt",
                "expected_protection": None,
                "readiness_program": readiness.source,
                "submission_program": submission.source,
            },
        )
    ]


def test_patchright_assignment_observation_decodes_only_the_session_identity() -> None:
    client = ScriptedClient(
        {
            "ok": True,
            "page": {
                "thread": "temporary",
                "page_token": 11,
                "url": "https://chatgpt.com/c/abc123",
            },
            "metadata": {"state": "session", "session_id": "abc123"},
        }
    )
    bridge = PatchrightOwnedPageBridge(client)
    program = OwnedPageProgram("() => ({state: 'session', session_id: 'abc123'})")

    outcome = bridge.observe_assignment(
        ObserveOwnedPageAssignment(
            owner="surf-chatgpt",
            thread="temporary",
            expected_page_token=11,
            allowed_scope=OwnedPageScope.CHATGPT,
            expected_protection=None,
            program=program,
            completion_exact_url=None,
        )
    )

    assert outcome == OwnedPageAssignmentObservation(
        page=OwnedPageRef("temporary", 11, "https://chatgpt.com/c/abc123"),
        state=OwnedPageAssignmentState.SESSION,
        session_id="abc123",
    )
    assert client.calls == [
        (
            "owned-observe-assignment",
            {
                "owner": "surf-chatgpt",
                "thread": "temporary",
                "expected_page_token": 11,
                "allowed_scope": "chatgpt",
                "expected_protection": None,
                "program": program.source,
                "completion_exact_url": None,
            },
        )
    ]


def test_patchright_assignment_observation_preserves_identity_during_gate() -> None:
    client = ScriptedClient(
        {
            "ok": True,
            "page": {
                "thread": "temporary",
                "page_token": 11,
                "url": "https://chatgpt.com/c/abc123",
            },
            "metadata": {"state": "challenge", "session_id": "abc123"},
        }
    )
    bridge = PatchrightOwnedPageBridge(client)

    outcome = bridge.observe_assignment(
        ObserveOwnedPageAssignment(
            owner="surf-chatgpt",
            thread="temporary",
            expected_page_token=11,
            allowed_scope=OwnedPageScope.CHATGPT,
            expected_protection=None,
            program=OwnedPageProgram("() => ({state: 'challenge'})"),
            completion_exact_url=None,
        )
    )

    assert outcome.state is OwnedPageAssignmentState.CHALLENGE
    assert outcome.session_id == "abc123"


def test_patchright_preparation_rejects_unrequested_browser_metadata() -> None:
    client = ScriptedClient(
        {
            "ok": True,
            "page": {
                "thread": "temporary",
                "page_token": 11,
                "url": "https://chatgpt.com/",
            },
            "metadata": {
                "state": "ready",
                "selection": {"model": "GPT-5.6", "private_dom": "secret"},
            },
        }
    )
    bridge = PatchrightOwnedPageBridge(client)

    with pytest.raises(ValueError, match="invalid preparation metadata"):
        bridge.prepare_submission(
            PrepareOwnedPageSubmission(
                owner="surf-chatgpt",
                thread="temporary",
                expected_page_token=11,
                allowed_scope=OwnedPageScope.CHATGPT,
                expected_protection=None,
                program=OwnedPageProgram("() => ({state: 'ready'})"),
                requested_selection_dimensions=frozenset(
                    {OwnedPageSelectionDimension.MODEL}
                ),
            )
        )


def test_patchright_assignment_rejects_browser_metadata_beyond_session_identity() -> (
    None
):
    client = ScriptedClient(
        {
            "ok": True,
            "page": {
                "thread": "temporary",
                "page_token": 11,
                "url": "https://chatgpt.com/c/abc123",
            },
            "metadata": {
                "state": "session",
                "session_id": "abc123",
                "private_dom": "secret",
            },
        }
    )
    bridge = PatchrightOwnedPageBridge(client)

    with pytest.raises(ValueError, match="invalid assignment metadata"):
        bridge.observe_assignment(
            ObserveOwnedPageAssignment(
                owner="surf-chatgpt",
                thread="temporary",
                expected_page_token=11,
                allowed_scope=OwnedPageScope.CHATGPT,
                expected_protection=None,
                program=OwnedPageProgram("() => ({state: 'not_ready'})"),
                completion_exact_url=None,
            )
        )


def test_patchright_submission_marker_conflict_has_a_distinct_typed_error() -> None:
    client = ScriptedClient({"ok": False, "error": "submission_already_attempted"})
    bridge = PatchrightOwnedPageBridge(client)

    with pytest.raises(OwnedPageSubmissionAlreadyAttempted):
        bridge.prepare_submission(
            PrepareOwnedPageSubmission(
                owner="surf-chatgpt",
                thread="temporary",
                expected_page_token=11,
                allowed_scope=OwnedPageScope.CHATGPT,
                expected_protection=None,
                program=OwnedPageProgram("() => ({state: 'ready'})"),
                requested_selection_dimensions=frozenset(),
            )
        )
