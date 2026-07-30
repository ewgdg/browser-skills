from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from surf_agent.owned_pages import (
    OwnedPageBridge,
    OwnedPageAssignmentState,
    OwnedPageInspectionState,
    OwnedPagePreparationState,
    OwnedPageProtection,
    OwnedPageRef,
    OwnedPageSubmissionAlreadyAttempted,
    OwnedPageSubmissionState,
    create_owned_page_bridge,
)
from surf_agent.errors import BridgeUnavailable
from surf_agent.pacing import Pacer

from .contracts import (
    AbandonRequest,
    AskRequest,
    CommandOutcome,
    CurrentSessionRequest,
    HandoffRequest,
    LoginRequest,
    ObservationOutcome,
    ObservationRequest,
    ProcessExitCode,
    RecentSessionsRequest,
)
from .errors import (
    PublicError,
    PublicErrorCause,
    PublicErrorCauseType,
    PublicErrorType,
    SubmissionPhase,
)
from .session_address import InvalidSessionAddress, SessionAddress
from .surf_pages import LOGIN_THREAD, ChatGptOwnedPages


SESSION_ASSIGNMENT_TIMEOUT_SECONDS = 30.0
SESSION_ASSIGNMENT_POLL_INTERVAL_SECONDS = 0.1
SUBMISSION_THREAD_PREFIX = "surf-chatgpt-submit-"


@dataclass
class _SubmissionProgress:
    phase: SubmissionPhase = SubmissionPhase.BEFORE_SEND
    thread: str | None = None
    session: SessionAddress | None = None


class SessionLifecycle(Protocol):
    def ask(self, request: AskRequest) -> CommandOutcome: ...

    def interruption_outcome(
        self,
        exit_code: ProcessExitCode,
    ) -> CommandOutcome: ...

    def observe(self, request: ObservationRequest) -> CommandOutcome: ...

    def current(self, request: CurrentSessionRequest) -> CommandOutcome: ...

    def handoff(self, request: HandoffRequest) -> CommandOutcome: ...

    def abandon(self, request: AbandonRequest) -> CommandOutcome: ...

    def recent(self, request: RecentSessionsRequest) -> CommandOutcome: ...

    def login(self, request: LoginRequest) -> CommandOutcome: ...


class OwnedPageSessionLifecycle:
    def __init__(
        self,
        bridge: OwnedPageBridge,
        *,
        submission_thread_factory: Callable[[], str] | None = None,
        monotonic: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
        phase_observer: Callable[[SubmissionPhase], None] | None = None,
    ) -> None:
        self._pages = ChatGptOwnedPages(bridge)
        self._submission_thread_factory = (
            submission_thread_factory or _new_submission_thread
        )
        self._monotonic = monotonic or time.monotonic
        self._sleeper = sleeper or time.sleep
        self._phase_observer = phase_observer
        self._submission = _SubmissionProgress()

    def login(self, request: LoginRequest) -> CommandOutcome:
        _ = request
        self._pages.prepare_login()
        return CommandOutcome.success(
            {
                "handoff": {
                    "action": "complete_login",
                    "thread": LOGIN_THREAD,
                }
            }
        )

    def ask(self, request: AskRequest) -> CommandOutcome:
        if request.session is not None or request.wait_timeout_seconds is not None:
            return self._pending_operation(request)

        self._submission = _SubmissionProgress()
        if request.thread is None:
            thread = self._submission_thread_factory()
            expected_protection = (
                OwnedPageProtection.EXPLICITLY_RETAINED if request.retain else None
            )
            try:
                page = self._pages.allocate_submission(
                    thread,
                    protection=expected_protection,
                )
            except BridgeUnavailable as error:
                raise PublicError(PublicErrorType.BROWSER_UNAVAILABLE) from error
        else:
            thread = request.thread
            inspection = self._pages.inspect_thread(thread)
            if inspection.state not in {
                OwnedPageInspectionState.PRE_SESSION,
                OwnedPageInspectionState.HUMAN_GATE,
            }:
                raise PublicError(PublicErrorType.OWNERSHIP_CONFLICT)
            page = inspection.page
            expected_protection = OwnedPageProtection.HUMAN_INTERVENTION
        self._submission.thread = thread

        try:
            preparation = self._pages.prepare_submission(
                page,
                expected_protection=expected_protection,
                model_query=request.model,
                thinking_query=request.thinking,
                allow_logged_out=request.allow_logged_out,
            )
        except OwnedPageSubmissionAlreadyAttempted:
            return self._indeterminate_outcome()
        except BridgeUnavailable as error:
            raise PublicError(PublicErrorType.BROWSER_UNAVAILABLE) from error

        if preparation.state is not OwnedPagePreparationState.READY:
            return self._before_send_preparation_failure(
                preparation.state,
                preparation.page,
                expected_protection,
            )
        page = preparation.page

        desired_protection = (
            OwnedPageProtection.EXPLICITLY_RETAINED if request.retain else None
        )
        if desired_protection is not expected_protection:
            try:
                self._pages.protect_submission(
                    page,
                    expected_protection=expected_protection,
                    protection=desired_protection,
                )
            except BridgeUnavailable as error:
                raise PublicError(PublicErrorType.BROWSER_UNAVAILABLE) from error
            expected_protection = desired_protection

        if preparation.selection:
            Pacer.for_name(request.pace.value).pause()

        self._transition(SubmissionPhase.BEFORE_SEND)
        try:
            submission = self._pages.submit_prompt(
                page,
                expected_protection=expected_protection,
                prompt=request.prompt,
                allow_logged_out=request.allow_logged_out,
                pace=request.pace,
                on_send_may_have_occurred=lambda: self._transition(
                    SubmissionPhase.SEND_MAY_HAVE_OCCURRED_ID_UNKNOWN
                ),
            )
        except BridgeUnavailable as error:
            if self._submission.phase is SubmissionPhase.BEFORE_SEND:
                raise PublicError(PublicErrorType.BROWSER_UNAVAILABLE) from error
            return self._indeterminate_outcome(bridge_disconnected=True)
        except Exception:
            if self._submission.phase is SubmissionPhase.BEFORE_SEND:
                raise
            # The bridge crossed its irreversible marker before running the send
            # program, so every non-signal failure must forbid automatic replay.
            return self._indeterminate_outcome()
        page = submission.page
        if submission.state is not OwnedPageSubmissionState.SUBMITTED:
            return self._after_send_state_outcome(
                submission.state,
                page,
                expected_protection,
            )

        deadline = self._monotonic() + SESSION_ASSIGNMENT_TIMEOUT_SECONDS
        while self._monotonic() < deadline:
            try:
                assignment = self._pages.observe_assignment(
                    page,
                    expected_protection=expected_protection,
                )
            except BridgeUnavailable:
                return self._indeterminate_outcome(bridge_disconnected=True)
            except Exception:
                # Session identity is still unknown; the submission error must
                # outrank browser, transport, decoder, and UI implementation detail.
                return self._indeterminate_outcome()
            page = assignment.page
            observed_at = self._monotonic()
            if observed_at >= deadline:
                return self._indeterminate_outcome()
            if assignment.state is OwnedPageAssignmentState.SESSION:
                assert assignment.session_id is not None
                try:
                    session = SessionAddress(assignment.session_id)
                except InvalidSessionAddress:
                    return self._indeterminate_outcome()
                break
            if assignment.session_id is not None:
                try:
                    session = SessionAddress(assignment.session_id)
                except InvalidSessionAddress:
                    return self._indeterminate_outcome()
                self._submission.session = session
                self._transition(SubmissionPhase.ID_KNOWN_REBIND_PENDING)
                return self._known_session_gate_outcome(
                    assignment.state,
                    page,
                    expected_protection,
                )
            if assignment.state is not OwnedPageAssignmentState.NOT_READY:
                return self._after_send_state_outcome(
                    assignment.state,
                    page,
                    expected_protection,
                )
            self._sleeper(
                min(
                    SESSION_ASSIGNMENT_POLL_INTERVAL_SECONDS,
                    deadline - observed_at,
                )
            )
        else:
            return self._indeterminate_outcome()

        self._submission.session = session
        self._transition(SubmissionPhase.ID_KNOWN_REBIND_PENDING)
        try:
            self._pages.rebind_submission(
                page,
                session,
                expected_protection=expected_protection,
            )
        except BridgeUnavailable:
            return self._rebind_failure_outcome(bridge_disconnected=True)
        except Exception:
            # Identity is durable now, but no error may claim that the guarded
            # registry move completed or authorize another send.
            return self._rebind_failure_outcome()

        self._transition(SubmissionPhase.HANDSHAKE_COMPLETE)
        fields = {"session": session.to_public_json()}
        if preparation.selection:
            fields["selection"] = {
                selected.dimension.value: selected.label
                for selected in preparation.selection
            }
        return CommandOutcome.success(fields)

    def interruption_outcome(
        self,
        exit_code: ProcessExitCode,
    ) -> CommandOutcome:
        if (
            self._submission.phase
            is SubmissionPhase.SEND_MAY_HAVE_OCCURRED_ID_UNKNOWN
        ):
            return self._indeterminate_outcome(exit_code=exit_code)
        public_fields = {}
        if self._submission.session is not None:
            public_fields["session"] = self._submission.session.to_public_json()
        return CommandOutcome.failure(
            PublicError(PublicErrorType.INTERRUPTED),
            exit_code=exit_code,
            public_fields=public_fields,
        )

    def observe(self, request: ObservationRequest) -> CommandOutcome:
        return self._pending_operation(request)

    def current(self, request: CurrentSessionRequest) -> CommandOutcome:
        inspection = self._pages.inspect_thread(request.thread)
        if inspection.state in {
            OwnedPageInspectionState.PRE_SESSION,
            OwnedPageInspectionState.HUMAN_GATE,
        }:
            return CommandOutcome.success(
                {
                    "session": None,
                    "observation": {"outcome": ObservationOutcome.NOT_READY.value},
                }
            )
        if inspection.state is not OwnedPageInspectionState.SESSION:
            raise PublicError(PublicErrorType.INSPECTION_FAILED)
        try:
            session = SessionAddress.parse(inspection.page.exact_url)
        except InvalidSessionAddress as error:
            raise PublicError(PublicErrorType.INSPECTION_FAILED) from error
        return CommandOutcome.success({"session": session.to_public_json()})

    def handoff(self, request: HandoffRequest) -> CommandOutcome:
        return self._pending_operation(request)

    def abandon(self, request: AbandonRequest) -> CommandOutcome:
        return self._pending_operation(request)

    def recent(self, request: RecentSessionsRequest) -> CommandOutcome:
        return self._pending_operation(request)

    def _pending_operation(self, request: object) -> CommandOutcome:
        _ = request
        raise PublicError(PublicErrorType.UNSUPPORTED_BROWSER_CAPABILITY)

    def _before_send_preparation_failure(
        self,
        state: OwnedPagePreparationState,
        page: OwnedPageRef,
        expected_protection: OwnedPageProtection | None,
    ) -> CommandOutcome:
        if state is OwnedPagePreparationState.MODEL_UNAVAILABLE:
            raise PublicError(PublicErrorType.MODEL_UNAVAILABLE)
        if state is OwnedPagePreparationState.UI_CHANGED:
            raise PublicError(PublicErrorType.UI_CHANGED)
        action = _human_gate_action(state.value)
        if action is None:
            raise PublicError(PublicErrorType.INSPECTION_FAILED)
        self._establish_human_protection(
            page,
            expected_protection,
            best_effort=False,
        )
        return self._human_intervention_outcome(action)

    def _after_send_state_outcome(
        self,
        state: OwnedPageSubmissionState | OwnedPageAssignmentState,
        page: OwnedPageRef,
        expected_protection: OwnedPageProtection | None,
    ) -> CommandOutcome:
        action = _human_gate_action(state.value)
        if action is None:
            return self._indeterminate_outcome()
        self._establish_human_protection(
            page,
            expected_protection,
            best_effort=True,
        )
        return self._indeterminate_outcome(handoff_action=action)

    def _known_session_gate_outcome(
        self,
        state: OwnedPageAssignmentState,
        page: OwnedPageRef,
        expected_protection: OwnedPageProtection | None,
    ) -> CommandOutcome:
        action = _human_gate_action(state.value)
        if action is None:
            return self._rebind_failure_outcome()
        assert self._submission.thread is not None
        assert self._submission.session is not None
        recovery_fields = {
            "session": self._submission.session.to_public_json(),
            "thread": self._submission.thread,
        }
        try:
            self._establish_human_protection(
                page,
                expected_protection,
                best_effort=False,
            )
        except PublicError as error:
            return CommandOutcome.failure(error, public_fields=recovery_fields)
        except Exception:
            return CommandOutcome.failure(
                PublicError(PublicErrorType.INTERNAL_ERROR),
                public_fields=recovery_fields,
            )
        return CommandOutcome.failure(
            PublicError(PublicErrorType.HUMAN_INTERVENTION_REQUIRED),
            public_fields={
                **recovery_fields,
                "handoff": {
                    "action": action,
                    "thread": self._submission.thread,
                },
            },
        )

    def _establish_human_protection(
        self,
        page: OwnedPageRef,
        expected_protection: OwnedPageProtection | None,
        *,
        best_effort: bool,
    ) -> None:
        if expected_protection is OwnedPageProtection.HUMAN_INTERVENTION:
            return
        try:
            self._pages.protect_submission(
                page,
                expected_protection=expected_protection,
                protection=OwnedPageProtection.HUMAN_INTERVENTION,
            )
        except BridgeUnavailable as error:
            if not best_effort:
                raise PublicError(PublicErrorType.BROWSER_UNAVAILABLE) from error
        except Exception:
            if not best_effort:
                raise
            # Send is already irreversible. Preserve the required indeterminate
            # outcome even when the additional handoff-protection CAS fails.
            return

    def _human_intervention_outcome(self, action: str) -> CommandOutcome:
        assert self._submission.thread is not None
        handoff = {"action": action, "thread": self._submission.thread}
        return CommandOutcome.failure(
            PublicError(PublicErrorType.HUMAN_INTERVENTION_REQUIRED),
            public_fields={
                "thread": self._submission.thread,
                "handoff": handoff,
            },
        )

    def _indeterminate_outcome(
        self,
        *,
        bridge_disconnected: bool = False,
        handoff_action: str | None = None,
        exit_code: ProcessExitCode = ProcessExitCode.OPERATIONAL_FAILURE,
    ) -> CommandOutcome:
        assert self._submission.thread is not None
        cause = (
            PublicErrorCause(
                PublicErrorCauseType.BRIDGE_DISCONNECTED,
                SubmissionPhase.SEND_MAY_HAVE_OCCURRED_ID_UNKNOWN,
            )
            if bridge_disconnected
            else None
        )
        public_fields = {"thread": self._submission.thread}
        if handoff_action is not None:
            public_fields["handoff"] = {
                "action": handoff_action,
                "thread": self._submission.thread,
            }
        return CommandOutcome.failure(
            PublicError(
                PublicErrorType.SUBMISSION_OUTCOME_INDETERMINATE,
                cause=cause,
            ),
            exit_code=exit_code,
            public_fields=public_fields,
        )

    def _rebind_failure_outcome(
        self,
        *,
        bridge_disconnected: bool = False,
    ) -> CommandOutcome:
        assert self._submission.thread is not None
        assert self._submission.session is not None
        cause = (
            PublicErrorCause(
                PublicErrorCauseType.BRIDGE_DISCONNECTED,
                SubmissionPhase.ID_KNOWN_REBIND_PENDING,
            )
            if bridge_disconnected
            else None
        )
        return CommandOutcome.failure(
            PublicError(PublicErrorType.SESSION_REBIND_FAILED, cause=cause),
            public_fields={
                "session": self._submission.session.to_public_json(),
                "thread": self._submission.thread,
            },
        )

    def _transition(self, phase: SubmissionPhase) -> None:
        self._submission.phase = phase
        if self._phase_observer is not None:
            self._phase_observer(phase)


def create_session_lifecycle() -> SessionLifecycle:
    return OwnedPageSessionLifecycle(create_owned_page_bridge())


def _new_submission_thread() -> str:
    return f"{SUBMISSION_THREAD_PREFIX}{secrets.token_urlsafe(9)}"


def _human_gate_action(state: str) -> str | None:
    if state == "login_required":
        return "complete_login"
    if state == "challenge":
        return "complete_challenge"
    return None
