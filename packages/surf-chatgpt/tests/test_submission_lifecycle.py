from __future__ import annotations

import io
import json
import signal
from dataclasses import dataclass, replace
from collections.abc import Callable
from typing import Any

import pytest

from surf_agent.errors import BridgeUnavailable
from surf_agent.owned_pages import (
    AllocateOwnedPage,
    ClassifyOwnedPageAttempt,
    CloseOwnedDiscoveryPage,
    CloseTerminalOwnedPage,
    ExtractOwnedPageResult,
    InspectOwnedPage,
    ObserveOwnedPageAssignment,
    OwnedPageAssignmentObservation,
    OwnedPageAssignmentState,
    OwnedPageAttemptMetadata,
    OwnedPageAttemptResult,
    OwnedPageAttemptState,
    OwnedPageCapabilities,
    OwnedPageInspection,
    OwnedPageInspectionState,
    OwnedPageNotFound,
    OwnedPagePreparationState,
    OwnedPagePromptSubmission,
    OwnedPageProtection,
    OwnedPageRef,
    OwnedPageSelection,
    OwnedPageSelectionDimension,
    OwnedPageScope,
    OwnedPageSubmissionAlreadyAttempted,
    OwnedPageSubmissionPreparation,
    OwnedPageSubmissionState,
    PrepareOwnedPageSubmission,
    ProtectOwnedPage,
    RebindOwnedPage,
    ResolveOwnedPage,
    ResolvedOwnedPage,
    SubmitOwnedPagePrompt,
)
from surf_chatgpt import cli
from surf_chatgpt.errors import SubmissionPhase
from surf_chatgpt.session_lifecycle import OwnedPageSessionLifecycle


@dataclass
class SubmissionPage:
    reference: OwnedPageRef
    protection: OwnedPageProtection | None = None
    send_may_have_occurred: bool = False
    inspection_state: OwnedPageInspectionState = OwnedPageInspectionState.PRE_SESSION


class ScriptedSubmissionBridge:
    def __init__(self) -> None:
        self.pages: dict[str, SubmissionPage] = {}
        self.calls: list[tuple[str, object]] = []
        self.next_page_token = 900
        self.preparation_state = OwnedPagePreparationState.READY
        self.selection: tuple[OwnedPageSelection, ...] = ()
        self.submission_state = OwnedPageSubmissionState.SUBMITTED
        self.assignment_states: list[tuple[OwnedPageAssignmentState, str | None]] = [
            (OwnedPageAssignmentState.SESSION, "abc123")
        ]
        self.attempt_states: list[tuple[OwnedPageAttemptState, str]] = [
            (OwnedPageAttemptState.GENERATING, "")
        ]
        self.fail_operation: str | None = None
        self.after_send_marker: Callable[[], None] | None = None

    def capabilities(self) -> OwnedPageCapabilities:
        return OwnedPageCapabilities.complete()

    def allocate(self, request: AllocateOwnedPage) -> OwnedPageRef:
        self.calls.append(("allocate", request))
        self._fail_if_requested("allocate")
        reference = OwnedPageRef(
            request.thread,
            self.next_page_token,
            request.url,
        )
        self.next_page_token += 1
        self.pages[request.thread] = SubmissionPage(
            reference,
            request.protection,
        )
        return reference

    def inspect(self, request: InspectOwnedPage) -> OwnedPageInspection:
        self.calls.append(("inspect", request))
        try:
            page = self.pages[request.thread]
        except KeyError as error:
            raise OwnedPageNotFound from error
        return OwnedPageInspection(page.reference, page.inspection_state)

    def resolve(self, request: ResolveOwnedPage) -> ResolvedOwnedPage:
        self.calls.append(("resolve", request))
        page = self.pages.get(request.thread)
        if page is None:
            reference = OwnedPageRef(
                request.thread,
                self.next_page_token,
                request.exact_url,
            )
            self.next_page_token += 1
            page = SubmissionPage(
                reference,
                inspection_state=OwnedPageInspectionState.SESSION,
            )
            self.pages[request.thread] = page
        return ResolvedOwnedPage(page.reference, page.protection)

    def prepare_submission(
        self, request: PrepareOwnedPageSubmission
    ) -> OwnedPageSubmissionPreparation:
        self.calls.append(("prepare_submission", request))
        self._fail_if_requested("prepare_submission")
        page = self._guarded_page(request.thread, request.expected_page_token)
        if page.send_may_have_occurred:
            raise OwnedPageSubmissionAlreadyAttempted
        return OwnedPageSubmissionPreparation(
            page.reference,
            self.preparation_state,
            self.selection,
        )

    def submit_prompt(
        self,
        request: SubmitOwnedPagePrompt,
        *,
        on_send_may_have_occurred: Callable[[], None],
    ) -> OwnedPagePromptSubmission:
        self.calls.append(("submit_prompt", request))
        page = self._guarded_page(request.thread, request.expected_page_token)
        if page.send_may_have_occurred:
            raise OwnedPageSubmissionAlreadyAttempted
        on_send_may_have_occurred()
        page.send_may_have_occurred = True
        if self.after_send_marker is not None:
            self.after_send_marker()
        self._fail_if_requested("submit_prompt")
        return OwnedPagePromptSubmission(page.reference, self.submission_state)

    def observe_assignment(
        self, request: ObserveOwnedPageAssignment
    ) -> OwnedPageAssignmentObservation:
        self.calls.append(("observe_assignment", request))
        self._fail_if_requested("observe_assignment")
        page = self._guarded_page(request.thread, request.expected_page_token)
        state, session_id = self.assignment_states.pop(0)
        if session_id is not None:
            page.reference = replace(
                page.reference,
                exact_url=f"https://chatgpt.com/c/{session_id}",
            )
        if request.completion_exact_url is not None and (
            state is OwnedPageAssignmentState.SESSION
            and page.reference.exact_url == request.completion_exact_url
        ):
            page.send_may_have_occurred = False
        return OwnedPageAssignmentObservation(page.reference, state, session_id)

    def rebind(self, request: RebindOwnedPage) -> OwnedPageRef:
        self.calls.append(("rebind", request))
        self._fail_if_requested("rebind")
        page = self._guarded_page(
            request.source_thread,
            request.expected_page_token,
        )
        if page.reference.exact_url != request.expected_exact_url:
            raise AssertionError("test bridge received a stale rebind URL")
        self.pages.pop(request.source_thread)
        page.reference = replace(page.reference, thread=request.destination_thread)
        page.send_may_have_occurred = False
        self.pages[request.destination_thread] = page
        return page.reference

    def protect(self, request: ProtectOwnedPage) -> None:
        self.calls.append(("protect", request))
        self._fail_if_requested("protect")
        page = self._guarded_page(request.thread, request.expected_page_token)
        if page.protection is not request.expected_protection:
            raise AssertionError("test bridge received stale protection")
        page.protection = request.protection

    def classify_attempt(
        self, request: ClassifyOwnedPageAttempt
    ) -> OwnedPageAttemptMetadata:
        self.calls.append(("classify_attempt", request))
        page = self._guarded_page(request.thread, request.expected_page_token)
        return OwnedPageAttemptMetadata(page.reference, self.attempt_states[0][0])

    def extract_result(
        self, request: ExtractOwnedPageResult
    ) -> OwnedPageAttemptResult:
        self.calls.append(("extract_result", request))
        page = self._guarded_page(request.thread, request.expected_page_token)
        state, text = (
            self.attempt_states.pop(0)
            if len(self.attempt_states) > 1
            else self.attempt_states[0]
        )
        return OwnedPageAttemptResult(
            page.reference,
            state,
            text
            if state in {OwnedPageAttemptState.COMPLETED, OwnedPageAttemptState.STOPPED}
            else None,
        )

    def close_terminal(self, request: CloseTerminalOwnedPage) -> None:
        self.calls.append(("close_terminal", request))
        page = self._guarded_page(request.thread, request.expected_page_token)
        if page.protection is not request.expected_protection:
            raise AssertionError("test bridge received stale protection")
        del self.pages[request.thread]

    def close_discovery(self, request: CloseOwnedDiscoveryPage) -> None:
        self.calls.append(("close_pre_session", request))
        page = self._guarded_page(request.thread, request.expected_page_token)
        if page.protection is not request.expected_protection:
            raise AssertionError("test bridge received stale protection")
        del self.pages[request.thread]

    def _guarded_page(self, thread: str, page_token: int) -> SubmissionPage:
        page = self.pages[thread]
        if page.reference.page_token != page_token:
            raise AssertionError("test bridge received a stale page token")
        return page

    def _fail_if_requested(self, operation: str) -> None:
        if self.fail_operation == operation:
            raise BridgeUnavailable("private bridge failure")


def invoke(
    argv: list[str],
    bridge: ScriptedSubmissionBridge,
    *,
    stdin: str = "",
    monotonic: Any | None = None,
    sleeper: Any | None = None,
    phase_observer: Callable[[SubmissionPhase], None] | None = None,
) -> tuple[int, dict[str, Any], str]:
    output = io.StringIO()
    errors = io.StringIO()
    lifecycle = OwnedPageSessionLifecycle(
        bridge,
        submission_thread_factory=lambda: "surf-chatgpt-submit-fixed",
        monotonic=monotonic,
        sleeper=sleeper,
        phase_observer=phase_observer,
    )
    code = cli.main(
        argv,
        stdin=io.StringIO(stdin),
        stdout=output,
        stderr=errors,
        lifecycle=lifecycle,
    )
    return code, json.loads(output.getvalue()), errors.getvalue()


def test_plain_ask_sends_once_rebinds_the_exact_page_then_returns_id_only() -> None:
    bridge = ScriptedSubmissionBridge()

    code, payload, stderr = invoke(["ask"], bridge, stdin="hello from stdin")

    assert (code, payload, stderr) == (
        0,
        {"ok": True, "session": {"id": "abc123"}},
        "",
    )
    assert [name for name, _ in bridge.calls] == [
        "allocate",
        "prepare_submission",
        "submit_prompt",
        "observe_assignment",
        "rebind",
    ]
    session_page = bridge.pages["surf-chatgpt-session-6ca13d52ca70c883e0f0bb101e425a89e8624de51db2d2392593af6a84118090"]
    assert session_page.reference.page_token == 900
    assert session_page.reference.exact_url == "https://chatgpt.com/c/abc123"
    assert session_page.send_may_have_occurred is False
    requests = {name: request for name, request in bridge.calls}
    assert requests["prepare_submission"].allowed_scope is (
        OwnedPageScope.CHATGPT_PRE_SESSION
    )
    assert requests["submit_prompt"].allowed_scope is (
        OwnedPageScope.CHATGPT_PRE_SESSION
    )
    assert requests["observe_assignment"].allowed_scope is OwnedPageScope.CHATGPT


def test_ask_wait_completes_one_handshake_then_uses_result_wait_observation() -> None:
    bridge = ScriptedSubmissionBridge()
    bridge.selection = (
        OwnedPageSelection(OwnedPageSelectionDimension.MODEL, "GPT-5.6"),
    )
    bridge.attempt_states = [
        (OwnedPageAttemptState.GENERATING, ""),
        (OwnedPageAttemptState.COMPLETED, "Answer"),
    ]
    clock = [0.0]

    def advance(duration: float) -> None:
        clock[0] += duration

    code, payload, stderr = invoke(
        ["ask", "--model", "latest", "--wait=5", "hello"],
        bridge,
        monotonic=lambda: clock[0],
        sleeper=advance,
    )

    assert (code, payload, stderr) == (
        0,
        {
            "ok": True,
            "session": {"id": "abc123"},
            "selection": {"model": "GPT-5.6"},
            "attempt": {"state": "completed"},
            "result": {"text": "Answer", "partial": False},
        },
        "",
    )
    operations = [name for name, _ in bridge.calls]
    assert operations == [
        "allocate",
        "prepare_submission",
        "submit_prompt",
        "observe_assignment",
        "rebind",
        "resolve",
        "extract_result",
        "extract_result",
        "close_terminal",
    ]
    assert operations.count("submit_prompt") == 1


def test_ask_session_resolves_and_submits_one_follow_up_on_the_same_binding() -> None:
    bridge = ScriptedSubmissionBridge()

    code, payload, stderr = invoke(
        ["ask", "--session", "abc123", "follow-up prompt"], bridge
    )

    assert (code, payload, stderr) == (
        0,
        {"ok": True, "session": {"id": "abc123"}},
        "",
    )
    assert [name for name, _ in bridge.calls] == [
        "resolve",
        "prepare_submission",
        "submit_prompt",
        "observe_assignment",
    ]
    session_page = bridge.pages["surf-chatgpt-session-6ca13d52ca70c883e0f0bb101e425a89e8624de51db2d2392593af6a84118090"]
    assert session_page.reference.page_token == 900
    assert session_page.reference.exact_url == "https://chatgpt.com/c/abc123"
    assert session_page.send_may_have_occurred is False
    guarded_requests = [request for name, request in bridge.calls if name != "resolve"]
    assert all(request.allowed_scope.value == "chatgpt" for request in guarded_requests)


def test_rebound_session_accepts_a_later_follow_up_from_a_short_lived_caller() -> None:
    bridge = ScriptedSubmissionBridge()

    first_code, first_payload, _ = invoke(["ask", "first prompt"], bridge)
    bridge.assignment_states = [(OwnedPageAssignmentState.SESSION, "abc123")]
    follow_up_code, follow_up_payload, _ = invoke(
        ["ask", "--session", "abc123", "follow-up prompt"], bridge
    )

    assert first_code == follow_up_code == 0
    assert first_payload["session"] == follow_up_payload["session"] == {"id": "abc123"}
    assert [name for name, _ in bridge.calls].count("submit_prompt") == 2
    assert bridge.pages["surf-chatgpt-session-6ca13d52ca70c883e0f0bb101e425a89e8624de51db2d2392593af6a84118090"].reference.page_token == 900


def test_ask_session_pre_send_gate_preserves_known_session_without_sending() -> None:
    bridge = ScriptedSubmissionBridge()
    bridge.preparation_state = OwnedPagePreparationState.CHALLENGE

    code, payload, _ = invoke(
        ["ask", "--session", "abc123", "follow-up prompt"], bridge
    )

    assert code == 1
    assert payload["error"]["type"] == "human_intervention_required"
    assert payload["session"] == {"id": "abc123"}
    assert payload["handoff"] == {
        "action": "complete_challenge",
        "thread": "surf-chatgpt-session-6ca13d52ca70c883e0f0bb101e425a89e8624de51db2d2392593af6a84118090",
    }
    assert "thread" not in payload
    assert bridge.pages["surf-chatgpt-session-6ca13d52ca70c883e0f0bb101e425a89e8624de51db2d2392593af6a84118090"].protection is (
        OwnedPageProtection.HUMAN_INTERVENTION
    )
    assert [name for name, _ in bridge.calls] == [
        "resolve",
        "prepare_submission",
        "protect",
    ]


def test_ask_session_bridge_loss_after_send_preserves_known_recovery_identity() -> None:
    bridge = ScriptedSubmissionBridge()
    bridge.fail_operation = "submit_prompt"

    code, payload, _ = invoke(
        ["ask", "--session", "abc123", "follow-up prompt"], bridge
    )

    assert code == 1
    assert payload["error"]["type"] == "browser_unavailable"
    assert payload["session"] == {"id": "abc123"}
    assert payload["ok"] is False
    assert "thread" not in payload
    assert bridge.pages["surf-chatgpt-session-6ca13d52ca70c883e0f0bb101e425a89e8624de51db2d2392593af6a84118090"].send_may_have_occurred is True
    assert [name for name, _ in bridge.calls].count("submit_prompt") == 1


@pytest.mark.parametrize(
    ("signal_number", "expected_exit"),
    [(signal.SIGINT, 130), (signal.SIGTERM, 143)],
)
def test_ask_session_signal_after_send_never_becomes_indeterminate(
    signal_number: signal.Signals,
    expected_exit: int,
) -> None:
    bridge = ScriptedSubmissionBridge()
    bridge.after_send_marker = lambda: signal.raise_signal(signal_number)

    code, payload, _ = invoke(
        ["ask", "--session", "abc123", "follow-up prompt"], bridge
    )

    assert code == expected_exit
    assert payload["error"]["type"] == "interrupted"
    assert payload["session"] == {"id": "abc123"}
    assert "thread" not in payload
    assert bridge.pages["surf-chatgpt-session-6ca13d52ca70c883e0f0bb101e425a89e8624de51db2d2392593af6a84118090"].send_may_have_occurred is True
    assert [name for name, _ in bridge.calls].count("submit_prompt") == 1


def test_ask_session_stays_pending_until_exact_assignment_is_affirmed() -> None:
    bridge = ScriptedSubmissionBridge()
    phases: list[SubmissionPhase] = []

    code, payload, _ = invoke(
        ["ask", "--session", "abc123", "follow-up prompt"],
        bridge,
        phase_observer=phases.append,
    )

    assert code == 0
    assert payload["session"] == {"id": "abc123"}
    assert phases == [
        SubmissionPhase.BEFORE_SEND,
        SubmissionPhase.ID_KNOWN_REBIND_PENDING,
        SubmissionPhase.HANDSHAKE_COMPLETE,
    ]


def test_ask_session_rejects_conflicting_assignment_without_rebinding() -> None:
    bridge = ScriptedSubmissionBridge()
    bridge.assignment_states = [(OwnedPageAssignmentState.SESSION, "different")]

    code, payload, _ = invoke(
        ["ask", "--session", "abc123", "follow-up prompt"], bridge
    )

    assert code == 1
    assert payload["error"]["type"] == "ownership_conflict"
    assert payload["session"] == {"id": "abc123"}
    assert "rebind" not in [name for name, _ in bridge.calls]
    assert [name for name, _ in bridge.calls].count("submit_prompt") == 1
    assert bridge.pages["surf-chatgpt-session-6ca13d52ca70c883e0f0bb101e425a89e8624de51db2d2392593af6a84118090"].send_may_have_occurred is True


def test_ask_session_preserves_existing_explicit_retention_without_reapplying_it() -> None:
    bridge = ScriptedSubmissionBridge()
    thread = "surf-chatgpt-session-6ca13d52ca70c883e0f0bb101e425a89e8624de51db2d2392593af6a84118090"
    bridge.pages[thread] = SubmissionPage(
        OwnedPageRef(thread, 777, "https://chatgpt.com/c/abc123"),
        OwnedPageProtection.EXPLICITLY_RETAINED,
        inspection_state=OwnedPageInspectionState.SESSION,
    )

    code, payload, _ = invoke(
        ["ask", "--session", "abc123", "follow-up prompt"], bridge
    )

    assert code == 0
    assert payload["session"] == {"id": "abc123"}
    assert bridge.pages[thread].protection is OwnedPageProtection.EXPLICITLY_RETAINED
    assert "protect" not in [name for name, _ in bridge.calls]


def test_ask_session_does_not_replay_an_unfinished_follow_up_attempt() -> None:
    bridge = ScriptedSubmissionBridge()
    thread = "surf-chatgpt-session-6ca13d52ca70c883e0f0bb101e425a89e8624de51db2d2392593af6a84118090"
    bridge.pages[thread] = SubmissionPage(
        OwnedPageRef(thread, 777, "https://chatgpt.com/c/abc123"),
        send_may_have_occurred=True,
        inspection_state=OwnedPageInspectionState.SESSION,
    )

    code, payload, _ = invoke(
        ["ask", "--session", "abc123", "follow-up prompt"], bridge
    )

    assert code == 1
    assert payload["error"]["type"] == "inspection_failed"
    assert payload["session"] == {"id": "abc123"}
    assert "thread" not in payload
    assert [name for name, _ in bridge.calls] == ["resolve", "prepare_submission"]


def test_plain_ask_reports_only_affirmed_requested_picker_labels() -> None:
    bridge = ScriptedSubmissionBridge()
    bridge.selection = (
        OwnedPageSelection(OwnedPageSelectionDimension.MODEL, "GPT-5.6"),
        OwnedPageSelection(OwnedPageSelectionDimension.THINKING, "Pro"),
    )

    _, payload, _ = invoke(
        [
            "ask",
            "--pace",
            "none",
            "--model",
            "latest",
            "--thinking",
            "highest",
            "prompt",
        ],
        bridge,
    )

    assert payload == {
        "ok": True,
        "session": {"id": "abc123"},
        "selection": {"model": "GPT-5.6", "thinking": "Pro"},
    }


def test_retry_thread_clears_gate_protection_only_after_readiness_is_affirmed() -> None:
    bridge = ScriptedSubmissionBridge()
    thread = "surf-chatgpt-login"
    bridge.pages[thread] = SubmissionPage(
        OwnedPageRef(thread, 777, "https://chatgpt.com/"),
        OwnedPageProtection.HUMAN_INTERVENTION,
    )

    code, payload, _ = invoke(["ask", "--thread", thread, "prompt"], bridge)

    assert code == 0
    assert payload["session"] == {"id": "abc123"}
    assert [name for name, _ in bridge.calls] == [
        "inspect",
        "prepare_submission",
        "protect",
        "submit_prompt",
        "observe_assignment",
        "rebind",
    ]
    assert bridge.pages["surf-chatgpt-session-6ca13d52ca70c883e0f0bb101e425a89e8624de51db2d2392593af6a84118090"].protection is None


def test_pre_send_gate_preserves_a_retryable_protected_thread_without_sending() -> None:
    bridge = ScriptedSubmissionBridge()
    bridge.preparation_state = OwnedPagePreparationState.CHALLENGE

    code, payload, _ = invoke(["ask", "private prompt"], bridge)

    thread = "surf-chatgpt-submit-fixed"
    assert code == 1
    assert payload["error"]["type"] == "human_intervention_required"
    assert payload["handoff"] == {
        "action": "complete_challenge",
        "thread": thread,
    }
    assert payload["thread"] == thread
    assert bridge.pages[thread].protection is OwnedPageProtection.HUMAN_INTERVENTION
    assert bridge.pages[thread].send_may_have_occurred is False
    assert [name for name, _ in bridge.calls] == [
        "allocate",
        "prepare_submission",
        "protect",
    ]
    assert "private prompt" not in json.dumps(payload)


def test_pre_send_rate_limit_reports_specific_failure_without_sending() -> None:
    bridge = ScriptedSubmissionBridge()
    bridge.preparation_state = OwnedPagePreparationState.RATE_LIMITED

    code, payload, _ = invoke(["ask", "private prompt"], bridge)

    assert code == 1
    assert payload["error"]["type"] == "rate_limited"
    assert "thread" not in payload
    assert "surf-chatgpt-submit-fixed" not in bridge.pages
    assert [name for name, _ in bridge.calls] == [
        "allocate",
        "prepare_submission",
        "close_pre_session",
    ]
    assert "private prompt" not in json.dumps(payload)


def test_retained_pre_send_rate_limit_preserves_recovery_thread() -> None:
    bridge = ScriptedSubmissionBridge()
    bridge.preparation_state = OwnedPagePreparationState.RATE_LIMITED

    code, payload, _ = invoke(["ask", "--retain", "private prompt"], bridge)

    thread = "surf-chatgpt-submit-fixed"
    assert code == 1
    assert payload["error"]["type"] == "rate_limited"
    assert payload["thread"] == thread
    assert bridge.pages[thread].protection is OwnedPageProtection.EXPLICITLY_RETAINED
    assert bridge.pages[thread].send_may_have_occurred is False
    assert "close_pre_session" not in [name for name, _ in bridge.calls]


def test_retained_pre_send_gate_becomes_human_protected_and_retries_exact_page() -> None:
    bridge = ScriptedSubmissionBridge()
    bridge.preparation_state = OwnedPagePreparationState.CHALLENGE

    first_code, first_payload, _ = invoke(
        ["ask", "--retain", "private prompt"],
        bridge,
    )

    thread = "surf-chatgpt-submit-fixed"
    assert first_code == 1
    assert first_payload["error"]["type"] == "human_intervention_required"
    assert bridge.pages[thread].protection is OwnedPageProtection.HUMAN_INTERVENTION
    assert bridge.pages[thread].send_may_have_occurred is False

    bridge.preparation_state = OwnedPagePreparationState.READY
    retry_code, retry_payload, _ = invoke(
        ["ask", "--thread", thread, "--retain", "private prompt"],
        bridge,
    )

    assert retry_code == 0
    assert retry_payload == {"ok": True, "session": {"id": "abc123"}}
    assert bridge.pages["surf-chatgpt-session-6ca13d52ca70c883e0f0bb101e425a89e8624de51db2d2392593af6a84118090"].protection is (
        OwnedPageProtection.EXPLICITLY_RETAINED
    )
    assert [name for name, _ in bridge.calls].count("submit_prompt") == 1


def test_pre_send_gate_does_not_claim_handoff_when_protection_cannot_be_established() -> None:
    bridge = ScriptedSubmissionBridge()
    bridge.preparation_state = OwnedPagePreparationState.LOGIN_REQUIRED
    bridge.fail_operation = "protect"

    code, payload, _ = invoke(["ask", "prompt"], bridge)

    assert code == 1
    assert payload["error"]["type"] == "browser_unavailable"
    assert "handoff" not in payload


def test_post_send_gate_is_indeterminate_and_the_same_thread_cannot_resubmit() -> None:
    bridge = ScriptedSubmissionBridge()
    bridge.assignment_states = [(OwnedPageAssignmentState.CHALLENGE, None)]

    code, payload, _ = invoke(["ask", "prompt"], bridge)

    thread = "surf-chatgpt-submit-fixed"
    assert code == 1
    assert payload["error"]["type"] == "submission_outcome_indeterminate"
    assert payload["thread"] == thread
    assert payload["handoff"] == {
        "action": "complete_challenge",
        "thread": thread,
    }
    assert bridge.pages[thread].send_may_have_occurred is True
    first_submit_count = [name for name, _ in bridge.calls].count("submit_prompt")

    retry_code, retry_payload, _ = invoke(
        ["ask", "--thread", thread, "prompt"], bridge
    )

    assert retry_code == 1
    assert retry_payload["error"]["type"] == "submission_outcome_indeterminate"
    assert [name for name, _ in bridge.calls].count("submit_prompt") == first_submit_count


@pytest.mark.parametrize("rate_limit_stage", ["submission", "assignment"])
def test_post_send_rate_limit_is_indeterminate_and_never_retries(
    rate_limit_stage: str,
) -> None:
    bridge = ScriptedSubmissionBridge()
    if rate_limit_stage == "submission":
        bridge.submission_state = OwnedPageSubmissionState.RATE_LIMITED
    else:
        bridge.assignment_states = [(OwnedPageAssignmentState.RATE_LIMITED, None)]

    code, payload, _ = invoke(["ask", "prompt"], bridge)

    assert code == 1
    assert payload["error"]["type"] == "submission_outcome_indeterminate"
    assert payload["error"]["cause"] == {
        "type": "rate_limited",
        "phase": "send_may_have_occurred_id_unknown",
        "message": "ChatGPT reported a request rate limit.",
    }
    assert payload["thread"] == "surf-chatgpt-submit-fixed"
    assert bridge.pages["surf-chatgpt-submit-fixed"].send_may_have_occurred is True
    assert [name for name, _ in bridge.calls].count("submit_prompt") == 1


def test_known_rate_limited_assignment_rebinds_before_reporting_failure() -> None:
    bridge = ScriptedSubmissionBridge()
    bridge.assignment_states = [(OwnedPageAssignmentState.RATE_LIMITED, "abc123")]

    code, payload, _ = invoke(["ask", "prompt"], bridge)

    assert code == 1
    assert payload["error"]["type"] == "rate_limited"
    assert payload["session"] == {"id": "abc123"}
    assert "thread" not in payload
    assert "surf-chatgpt-submit-fixed" not in bridge.pages
    assert [name for name, _ in bridge.calls].count("submit_prompt") == 1
    assert [name for name, _ in bridge.calls].count("rebind") == 1


def test_post_send_gate_with_known_id_returns_handoff_without_reporting_success() -> None:
    bridge = ScriptedSubmissionBridge()
    bridge.assignment_states = [(OwnedPageAssignmentState.CHALLENGE, "abc123")]

    code, payload, _ = invoke(["ask", "prompt"], bridge)

    thread = "surf-chatgpt-submit-fixed"
    assert code == 1
    assert payload == {
        "ok": False,
        "error": {
            "type": "human_intervention_required",
            "message": "The browser requires user intervention.",
            "hint": "Complete the requested browser action manually before retrying.",
        },
        "session": {"id": "abc123"},
        "thread": thread,
        "handoff": {"action": "complete_challenge", "thread": thread},
    }
    assert bridge.pages[thread].protection is OwnedPageProtection.HUMAN_INTERVENTION
    assert "surf-chatgpt-session-6ca13d52ca70c883e0f0bb101e425a89e8624de51db2d2392593af6a84118090" not in bridge.pages
    assert [name for name, _ in bridge.calls].count("submit_prompt") == 1


def test_rebind_disconnect_returns_known_session_and_preserved_source_thread() -> None:
    bridge = ScriptedSubmissionBridge()
    bridge.fail_operation = "rebind"

    code, payload, _ = invoke(["ask", "prompt"], bridge)

    assert code == 1
    assert payload["error"]["type"] == "session_rebind_failed"
    assert payload["error"]["cause"] == {
        "type": "bridge_disconnected",
        "phase": "id_known_rebind_pending",
        "message": "The browser bridge connection ended.",
    }
    assert payload["session"] == {"id": "abc123"}
    assert payload["thread"] == "surf-chatgpt-submit-fixed"
    assert bridge.pages["surf-chatgpt-submit-fixed"].send_may_have_occurred is True


def test_assignment_observation_repeats_read_only_until_the_id_is_available() -> None:
    bridge = ScriptedSubmissionBridge()
    bridge.assignment_states = [
        (OwnedPageAssignmentState.NOT_READY, None),
        (OwnedPageAssignmentState.SESSION, "abc123"),
    ]
    observed_delays: list[float] = []

    code, payload, _ = invoke(
        ["ask", "prompt"],
        bridge,
        monotonic=lambda: 0.0,
        sleeper=observed_delays.append,
    )

    assert code == 0
    assert payload == {"ok": True, "session": {"id": "abc123"}}
    assert [name for name, _ in bridge.calls].count("submit_prompt") == 1
    assert [name for name, _ in bridge.calls].count("observe_assignment") == 2
    assert len(observed_delays) == 1


def test_assignment_deadline_is_indeterminate_and_never_replays_send() -> None:
    bridge = ScriptedSubmissionBridge()
    bridge.assignment_states = [
        (OwnedPageAssignmentState.NOT_READY, None),
        (OwnedPageAssignmentState.NOT_READY, None),
    ]
    clock = [0.0]

    def advance_past_deadline(_: float) -> None:
        clock[0] = 31.0

    code, payload, _ = invoke(
        ["ask", "prompt"],
        bridge,
        monotonic=lambda: clock[0],
        sleeper=advance_past_deadline,
    )

    assert code == 1
    assert payload["error"]["type"] == "submission_outcome_indeterminate"
    assert payload["thread"] == "surf-chatgpt-submit-fixed"
    assert [name for name, _ in bridge.calls].count("submit_prompt") == 1


def test_assignment_finishing_after_deadline_cannot_report_late_success() -> None:
    bridge = ScriptedSubmissionBridge()
    clock = iter((0.0, 0.0, 31.0))

    code, payload, _ = invoke(
        ["ask", "prompt"],
        bridge,
        monotonic=lambda: next(clock),
    )

    assert code == 1
    assert payload["error"]["type"] == "submission_outcome_indeterminate"
    assert payload["thread"] == "surf-chatgpt-submit-fixed"
    assert "surf-chatgpt-session-6ca13d52ca70c883e0f0bb101e425a89e8624de51db2d2392593af6a84118090" not in bridge.pages


def test_retain_protects_the_same_live_page_through_rebind() -> None:
    bridge = ScriptedSubmissionBridge()

    code, payload, _ = invoke(["ask", "--retain", "prompt"], bridge)

    assert code == 0
    assert payload == {"ok": True, "session": {"id": "abc123"}}
    page = bridge.pages["surf-chatgpt-session-6ca13d52ca70c883e0f0bb101e425a89e8624de51db2d2392593af6a84118090"]
    assert page.reference.page_token == 900
    assert page.protection is OwnedPageProtection.EXPLICITLY_RETAINED


@pytest.mark.parametrize(
    ("signal_number", "expected_exit"),
    [(signal.SIGINT, 130), (signal.SIGTERM, 143)],
)
@pytest.mark.parametrize(
    "barrier",
    [
        SubmissionPhase.BEFORE_SEND,
        SubmissionPhase.SEND_MAY_HAVE_OCCURRED_ID_UNKNOWN,
        SubmissionPhase.ID_KNOWN_REBIND_PENDING,
        SubmissionPhase.HANDSHAKE_COMPLETE,
    ],
)
def test_real_signals_project_submission_phase_and_preserve_durable_side_effects(
    barrier: SubmissionPhase,
    signal_number: signal.Signals,
    expected_exit: int,
) -> None:
    bridge = ScriptedSubmissionBridge()

    def interrupt_at_phase(phase: SubmissionPhase) -> None:
        if barrier is phase and phase is not SubmissionPhase.SEND_MAY_HAVE_OCCURRED_ID_UNKNOWN:
            signal.raise_signal(signal_number)

    if barrier is SubmissionPhase.SEND_MAY_HAVE_OCCURRED_ID_UNKNOWN:
        bridge.after_send_marker = lambda: signal.raise_signal(signal_number)

    code, payload, _ = invoke(
        ["ask", "prompt"],
        bridge,
        phase_observer=interrupt_at_phase,
    )

    assert code == expected_exit
    if barrier is SubmissionPhase.SEND_MAY_HAVE_OCCURRED_ID_UNKNOWN:
        assert payload["error"]["type"] == "submission_outcome_indeterminate"
        assert payload["thread"] == "surf-chatgpt-submit-fixed"
        assert bridge.pages["surf-chatgpt-submit-fixed"].send_may_have_occurred is True
    else:
        assert payload["error"]["type"] == "interrupted"
    if barrier in {
        SubmissionPhase.ID_KNOWN_REBIND_PENDING,
        SubmissionPhase.HANDSHAKE_COMPLETE,
    }:
        assert payload["session"] == {"id": "abc123"}
    if barrier is SubmissionPhase.BEFORE_SEND:
        assert [name for name, _ in bridge.calls].count("submit_prompt") == 0
    if barrier is SubmissionPhase.HANDSHAKE_COMPLETE:
        assert "surf-chatgpt-session-6ca13d52ca70c883e0f0bb101e425a89e8624de51db2d2392593af6a84118090" in bridge.pages


@pytest.mark.parametrize(
    ("operation", "expected_error", "expected_phase"),
    [
        ("allocate", "browser_unavailable", None),
        ("prepare_submission", "browser_unavailable", None),
        (
            "submit_prompt",
            "submission_outcome_indeterminate",
            "send_may_have_occurred_id_unknown",
        ),
        (
            "observe_assignment",
            "submission_outcome_indeterminate",
            "send_may_have_occurred_id_unknown",
        ),
    ],
)
def test_bridge_disconnect_respects_the_send_replay_boundary(
    operation: str,
    expected_error: str,
    expected_phase: str | None,
) -> None:
    bridge = ScriptedSubmissionBridge()
    bridge.fail_operation = operation

    code, payload, _ = invoke(["ask", "prompt"], bridge)

    assert code == 1
    assert payload["error"]["type"] == expected_error
    if expected_phase is None:
        assert "cause" not in payload["error"]
        if operation == "allocate":
            assert bridge.pages == {}
        else:
            assert bridge.pages["surf-chatgpt-submit-fixed"].send_may_have_occurred is False
    else:
        assert payload["error"]["cause"]["phase"] == expected_phase
        assert payload["thread"] == "surf-chatgpt-submit-fixed"
        assert bridge.pages["surf-chatgpt-submit-fixed"].send_may_have_occurred is True
    assert [name for name, _ in bridge.calls].count("submit_prompt") <= 1


def test_retry_thread_must_resolve_to_an_exact_live_owned_page() -> None:
    bridge = ScriptedSubmissionBridge()

    code, payload, _ = invoke(
        ["ask", "--thread", "missing-preserved-thread", "prompt"],
        bridge,
    )

    assert code == 1
    assert payload["error"]["type"] == "thread_not_found"
    assert [name for name, _ in bridge.calls] == ["inspect"]


def test_retry_thread_never_uses_a_conversation_as_pre_session_addressing() -> None:
    bridge = ScriptedSubmissionBridge()
    thread = "preserved"
    bridge.pages[thread] = SubmissionPage(
        OwnedPageRef(thread, 777, "https://chatgpt.com/c/existing"),
        OwnedPageProtection.HUMAN_INTERVENTION,
        inspection_state=OwnedPageInspectionState.SESSION,
    )

    code, payload, _ = invoke(["ask", "--thread", thread, "prompt"], bridge)

    assert code == 1
    assert payload["error"]["type"] == "ownership_conflict"
    assert [name for name, _ in bridge.calls] == ["inspect"]


def test_retry_does_not_send_when_ready_page_protection_update_disconnects() -> None:
    bridge = ScriptedSubmissionBridge()
    thread = "surf-chatgpt-login"
    bridge.pages[thread] = SubmissionPage(
        OwnedPageRef(thread, 777, "https://chatgpt.com/"),
        OwnedPageProtection.HUMAN_INTERVENTION,
    )
    bridge.fail_operation = "protect"

    code, payload, _ = invoke(
        ["ask", "--thread", thread, "--retain", "prompt"],
        bridge,
    )

    assert code == 1
    assert payload["error"]["type"] == "browser_unavailable"
    assert [name for name, _ in bridge.calls].count("submit_prompt") == 0
