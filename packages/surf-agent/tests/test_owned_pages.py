from __future__ import annotations

import json
from collections.abc import Callable
from types import SimpleNamespace

import pytest

from surf_agent.owned_pages import (
    AllocateOwnedPage,
    InspectOwnedPage,
    ObserveOwnedPageAssignment,
    OwnedPageAssignmentObservation,
    OwnedPageAssignmentState,
    OwnedPageAmbiguousSession,
    OwnedPageClassifier,
    OwnedPageInspectionState,
    OwnedPageNotFound,
    OwnedPageProtection,
    OwnedPageProgram,
    OwnedPagePromptSubmission,
    OwnedPageRef,
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
            },
        )
    ]


def test_unsupported_backend_rejects_allocation_without_browser_work() -> None:
    bridge = UnsupportedOwnedPageBridge()
    request = AllocateOwnedPage(
        owner="surf-chatgpt",
        thread="surf-chatgpt-login",
        url="https://chatgpt.com/",
        allowed_scope=OwnedPageScope.CHATGPT,
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
    client = ScriptedClient(
        {"ok": False, "error": "submission_already_attempted"}
    )
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
