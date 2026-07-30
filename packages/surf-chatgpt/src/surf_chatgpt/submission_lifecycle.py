from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass

from surf_agent.errors import BridgeUnavailable
from surf_agent.owned_pages import (
    OwnedPageAssignmentObservation,
    OwnedPageAssignmentState,
    OwnedPageInspectionState,
    OwnedPagePreparationState,
    OwnedPageProtection,
    OwnedPageRef,
    OwnedPageScope,
    OwnedPageSelection,
    OwnedPageSubmissionAlreadyAttempted,
    OwnedPageSubmissionState,
)
from surf_agent.pacing import Pacer

from .contracts import AskRequest, CommandOutcome, ProcessExitCode
from .errors import (
    PublicError,
    PublicErrorCause,
    PublicErrorCauseType,
    PublicErrorType,
    SubmissionPhase,
)
from .session_address import InvalidSessionAddress, SessionAddress
from .surf_pages import ChatGptOwnedPages


SESSION_ASSIGNMENT_TIMEOUT_SECONDS = 30.0
SESSION_ASSIGNMENT_POLL_INTERVAL_SECONDS = 0.1
SUBMISSION_THREAD_PREFIX = "surf-chatgpt-submit-"


@dataclass
class _SubmissionProgress:
    phase: SubmissionPhase = SubmissionPhase.BEFORE_SEND
    thread: str | None = None
    session: SessionAddress | None = None


@dataclass
class _SubmissionContext:
    page: OwnedPageRef
    allowed_scope: OwnedPageScope
    protection: OwnedPageProtection | None
    session: SessionAddress | None

    @property
    def is_follow_up(self) -> bool:
        return self.session is not None

    @property
    def completion_exact_url(self) -> str | None:
        return self.session.canonical_url if self.session is not None else None

    @property
    def after_dispatch_phase(self) -> SubmissionPhase:
        if self.is_follow_up:
            return SubmissionPhase.ID_KNOWN_REBIND_PENDING
        return SubmissionPhase.SEND_MAY_HAVE_OCCURRED_ID_UNKNOWN


class SubmissionLifecycle:
    """Own the one-send handshake for new and durable-session prompts."""

    def __init__(
        self,
        pages: ChatGptOwnedPages,
        *,
        submission_thread_factory: Callable[[], str] | None = None,
        monotonic: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
        phase_observer: Callable[[SubmissionPhase], None] | None = None,
    ) -> None:
        self._pages = pages
        self._submission_thread_factory = (
            submission_thread_factory or _new_submission_thread
        )
        self._monotonic = monotonic or time.monotonic
        self._sleeper = sleeper or time.sleep
        self._phase_observer = phase_observer
        self._progress = _SubmissionProgress()

    def ask(self, request: AskRequest) -> CommandOutcome:
        self._progress = _SubmissionProgress()
        context = self._resolve_context(request)
        if isinstance(context, CommandOutcome):
            return context

        selection = self._prepare_context(request, context)
        if isinstance(selection, CommandOutcome):
            return selection

        send_failure = self._send_once(request, context)
        if send_failure is not None:
            return send_failure

        assigned_session = self._await_assignment(context)
        if isinstance(assigned_session, CommandOutcome):
            return assigned_session

        return self._finish_handshake(context, assigned_session, selection)

    def interruption_outcome(
        self,
        exit_code: ProcessExitCode,
    ) -> CommandOutcome:
        if self._progress.phase is SubmissionPhase.SEND_MAY_HAVE_OCCURRED_ID_UNKNOWN:
            return self._indeterminate_outcome(exit_code=exit_code)
        public_fields = {}
        if self._progress.session is not None:
            public_fields["session"] = self._progress.session.to_public_json()
        return CommandOutcome.failure(
            PublicError(PublicErrorType.INTERRUPTED),
            exit_code=exit_code,
            public_fields=public_fields,
        )

    def _resolve_context(
        self,
        request: AskRequest,
    ) -> _SubmissionContext | CommandOutcome:
        if request.session is not None:
            self._progress.thread = request.session.thread
            self._progress.session = request.session
            try:
                resolved = self._pages.resolve_session(request.session)
            except PublicError as error:
                return self._known_public_failure(request.session, error)
            return _SubmissionContext(
                resolved.page,
                OwnedPageScope.CHATGPT,
                resolved.protection,
                request.session,
            )

        if request.thread is None:
            thread = self._submission_thread_factory()
            protection = (
                OwnedPageProtection.EXPLICITLY_RETAINED if request.retain else None
            )
            try:
                page = self._pages.allocate_submission(thread, protection=protection)
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
            protection = OwnedPageProtection.HUMAN_INTERVENTION

        self._progress.thread = thread
        return _SubmissionContext(
            page,
            OwnedPageScope.CHATGPT_PRE_SESSION,
            protection,
            None,
        )

    def _prepare_context(
        self,
        request: AskRequest,
        context: _SubmissionContext,
    ) -> tuple[OwnedPageSelection, ...] | CommandOutcome:
        try:
            preparation = self._pages.prepare_submission(
                context.page,
                allowed_scope=context.allowed_scope,
                expected_protection=context.protection,
                model_query=request.model,
                thinking_query=request.thinking,
                allow_logged_out=request.allow_logged_out,
            )
        except OwnedPageSubmissionAlreadyAttempted:
            if context.is_follow_up:
                return self._known_session_failure(PublicErrorType.INSPECTION_FAILED)
            return self._indeterminate_outcome()
        except BridgeUnavailable as error:
            if context.is_follow_up:
                return self._known_session_failure(PublicErrorType.BROWSER_UNAVAILABLE)
            raise PublicError(PublicErrorType.BROWSER_UNAVAILABLE) from error
        except PublicError as error:
            if context.session is not None:
                return self._known_public_failure(context.session, error)
            raise

        if preparation.state is not OwnedPagePreparationState.READY:
            return self._before_send_preparation_failure(
                preparation.state,
                preparation.page,
                context.protection,
            )
        context.page = preparation.page

        desired_protection = context.protection
        if context.protection is not OwnedPageProtection.EXPLICITLY_RETAINED:
            desired_protection = (
                OwnedPageProtection.EXPLICITLY_RETAINED if request.retain else None
            )
        if desired_protection is not context.protection:
            try:
                self._pages.protect_submission(
                    context.page,
                    expected_protection=context.protection,
                    protection=desired_protection,
                )
            except BridgeUnavailable as error:
                if context.is_follow_up:
                    return self._known_session_failure(
                        PublicErrorType.BROWSER_UNAVAILABLE
                    )
                raise PublicError(PublicErrorType.BROWSER_UNAVAILABLE) from error
            except PublicError as error:
                if context.session is not None:
                    return self._known_public_failure(context.session, error)
                raise
            context.protection = desired_protection

        if preparation.selection:
            Pacer.for_name(request.pace.value).pause()
        return preparation.selection

    def _send_once(
        self,
        request: AskRequest,
        context: _SubmissionContext,
    ) -> CommandOutcome | None:
        self._transition(SubmissionPhase.BEFORE_SEND)
        try:
            submission = self._pages.submit_prompt(
                context.page,
                allowed_scope=context.allowed_scope,
                expected_protection=context.protection,
                prompt=request.prompt,
                allow_logged_out=request.allow_logged_out,
                pace=request.pace,
                on_send_may_have_occurred=lambda: self._transition(
                    context.after_dispatch_phase
                ),
            )
        except BridgeUnavailable as error:
            if context.is_follow_up:
                return self._known_session_failure(PublicErrorType.BROWSER_UNAVAILABLE)
            if self._progress.phase is SubmissionPhase.BEFORE_SEND:
                raise PublicError(PublicErrorType.BROWSER_UNAVAILABLE) from error
            return self._indeterminate_outcome(bridge_disconnected=True)
        except PublicError as error:
            if context.session is not None:
                return self._known_public_failure(context.session, error)
            if self._progress.phase is SubmissionPhase.BEFORE_SEND:
                raise
            return self._indeterminate_outcome()
        except ValueError:
            if context.is_follow_up:
                return self._known_session_failure(PublicErrorType.INSPECTION_FAILED)
            if self._progress.phase is SubmissionPhase.BEFORE_SEND:
                raise
            return self._indeterminate_outcome()

        context.page = submission.page
        if submission.state is OwnedPageSubmissionState.SUBMITTED:
            return None
        return self._after_send_state_outcome(
            submission.state,
            context.page,
            context.protection,
        )

    def _await_assignment(
        self,
        context: _SubmissionContext,
    ) -> SessionAddress | CommandOutcome:
        deadline = self._monotonic() + SESSION_ASSIGNMENT_TIMEOUT_SECONDS
        while self._monotonic() < deadline:
            assignment = self._observe_assignment(context)
            if isinstance(assignment, CommandOutcome):
                return assignment
            context.page = assignment.page

            observed_at = self._monotonic()
            if observed_at >= deadline:
                return self._assignment_timeout(context)

            session = self._session_from_assignment(assignment.session_id, context)
            if isinstance(session, CommandOutcome):
                return session
            if assignment.state is OwnedPageAssignmentState.RATE_LIMITED:
                if session is None:
                    return self._after_send_state_outcome(
                        assignment.state,
                        context.page,
                        context.protection,
                    )
                return self._finish_rate_limited_handshake(context, session)
            if assignment.state is OwnedPageAssignmentState.SESSION:
                assert session is not None
                return session
            if session is not None:
                self._progress.session = session
                self._transition(SubmissionPhase.ID_KNOWN_REBIND_PENDING)
                return self._known_session_gate_outcome(
                    assignment.state,
                    context.page,
                    context.protection,
                )
            if assignment.state is not OwnedPageAssignmentState.NOT_READY:
                return self._after_send_state_outcome(
                    assignment.state,
                    context.page,
                    context.protection,
                )
            self._sleeper(
                min(
                    SESSION_ASSIGNMENT_POLL_INTERVAL_SECONDS,
                    deadline - observed_at,
                )
            )
        return self._assignment_timeout(context)

    def _observe_assignment(
        self,
        context: _SubmissionContext,
    ) -> OwnedPageAssignmentObservation | CommandOutcome:
        try:
            return self._pages.observe_assignment(
                context.page,
                # Dispatch is the authorized transition from a pre-session route to
                # the newly assigned canonical conversation on the same owned page.
                allowed_scope=OwnedPageScope.CHATGPT,
                expected_protection=context.protection,
                completion_exact_url=context.completion_exact_url,
            )
        except BridgeUnavailable:
            if context.is_follow_up:
                return self._known_session_failure(PublicErrorType.BROWSER_UNAVAILABLE)
            return self._indeterminate_outcome(bridge_disconnected=True)
        except PublicError as error:
            if context.session is not None:
                return self._known_public_failure(context.session, error)
            return self._indeterminate_outcome()
        except ValueError:
            if context.is_follow_up:
                return self._known_session_failure(PublicErrorType.INSPECTION_FAILED)
            return self._indeterminate_outcome()

    def _session_from_assignment(
        self,
        session_id: str | None,
        context: _SubmissionContext,
    ) -> SessionAddress | CommandOutcome | None:
        if session_id is None:
            return None
        try:
            session = SessionAddress(session_id)
        except InvalidSessionAddress:
            if context.is_follow_up:
                return self._known_session_failure(PublicErrorType.OWNERSHIP_CONFLICT)
            return self._indeterminate_outcome()
        if context.session is not None and session != context.session:
            return self._known_session_failure(PublicErrorType.OWNERSHIP_CONFLICT)
        return session

    def _assignment_timeout(self, context: _SubmissionContext) -> CommandOutcome:
        if context.is_follow_up:
            return self._known_session_failure(PublicErrorType.INSPECTION_FAILED)
        return self._indeterminate_outcome()

    def _finish_handshake(
        self,
        context: _SubmissionContext,
        session: SessionAddress,
        selection: tuple[OwnedPageSelection, ...],
    ) -> CommandOutcome:
        self._progress.session = session
        if context.is_follow_up:
            self._transition(SubmissionPhase.HANDSHAKE_COMPLETE)
            return self._submission_success(session, selection)

        self._transition(SubmissionPhase.ID_KNOWN_REBIND_PENDING)
        try:
            self._pages.rebind_submission(
                context.page,
                session,
                expected_protection=context.protection,
            )
        except BridgeUnavailable:
            return self._rebind_failure_outcome(bridge_disconnected=True)
        except (PublicError, ValueError):
            return self._rebind_failure_outcome()

        self._transition(SubmissionPhase.HANDSHAKE_COMPLETE)
        return self._submission_success(session, selection)

    def _finish_rate_limited_handshake(
        self,
        context: _SubmissionContext,
        session: SessionAddress,
    ) -> CommandOutcome:
        self._progress.session = session
        if not context.is_follow_up:
            self._transition(SubmissionPhase.ID_KNOWN_REBIND_PENDING)
            try:
                self._pages.rebind_submission(
                    context.page,
                    session,
                    expected_protection=context.protection,
                )
            except BridgeUnavailable:
                return self._rebind_failure_outcome(bridge_disconnected=True)
            except (PublicError, ValueError):
                return self._rebind_failure_outcome()
        self._transition(SubmissionPhase.HANDSHAKE_COMPLETE)
        return self._known_session_failure(PublicErrorType.RATE_LIMITED)

    def _before_send_preparation_failure(
        self,
        state: OwnedPagePreparationState,
        page: OwnedPageRef,
        expected_protection: OwnedPageProtection | None,
    ) -> CommandOutcome:
        if state is OwnedPagePreparationState.MODEL_UNAVAILABLE:
            raise PublicError(PublicErrorType.MODEL_UNAVAILABLE)
        if state is OwnedPagePreparationState.RATE_LIMITED:
            public_fields = (
                {"thread": page.thread}
                if expected_protection is not None
                else None
            )
            cleanup = (
                None
                if expected_protection is not None
                else lambda: self._pages.close_pre_session(
                    page,
                    expected_protection=None,
                )
            )
            return CommandOutcome.failure(
                PublicError(PublicErrorType.RATE_LIMITED),
                public_fields=public_fields,
                post_output_cleanup=cleanup,
            )
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
        if (
            state is OwnedPageSubmissionState.RATE_LIMITED
            or state is OwnedPageAssignmentState.RATE_LIMITED
        ):
            if self._progress.session is not None:
                return self._known_session_failure(PublicErrorType.RATE_LIMITED)
            return self._indeterminate_outcome(rate_limited=True)
        action = _human_gate_action(state.value)
        if self._progress.session is not None:
            if action is None:
                return self._known_session_failure(PublicErrorType.INSPECTION_FAILED)
            return self._known_session_gate_outcome(
                state,
                page,
                expected_protection,
            )
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
        state: OwnedPageAssignmentState | OwnedPageSubmissionState,
        page: OwnedPageRef,
        expected_protection: OwnedPageProtection | None,
    ) -> CommandOutcome:
        action = _human_gate_action(state.value)
        if action is None:
            return self._rebind_failure_outcome()
        assert self._progress.thread is not None
        assert self._progress.session is not None
        recovery_fields = {
            "session": self._progress.session.to_public_json(),
            "thread": self._progress.thread,
        }
        try:
            self._establish_human_protection(
                page,
                expected_protection,
                best_effort=False,
            )
        except PublicError as error:
            return CommandOutcome.failure(error, public_fields=recovery_fields)
        return CommandOutcome.failure(
            PublicError(PublicErrorType.HUMAN_INTERVENTION_REQUIRED),
            public_fields={
                **recovery_fields,
                "handoff": {
                    "action": action,
                    "thread": self._progress.thread,
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
        except (PublicError, ValueError):
            if not best_effort:
                raise
        # Send is already irreversible. A typed best-effort protection failure
        # cannot replace the required phase-aware submission outcome.

    def _human_intervention_outcome(self, action: str) -> CommandOutcome:
        assert self._progress.thread is not None
        handoff = {"action": action, "thread": self._progress.thread}
        if self._progress.session is not None:
            return CommandOutcome.failure(
                PublicError(PublicErrorType.HUMAN_INTERVENTION_REQUIRED),
                public_fields={
                    "session": self._progress.session.to_public_json(),
                    "handoff": handoff,
                },
            )
        return CommandOutcome.failure(
            PublicError(PublicErrorType.HUMAN_INTERVENTION_REQUIRED),
            public_fields={
                "thread": self._progress.thread,
                "handoff": handoff,
            },
        )

    def _indeterminate_outcome(
        self,
        *,
        bridge_disconnected: bool = False,
        rate_limited: bool = False,
        handoff_action: str | None = None,
        exit_code: ProcessExitCode = ProcessExitCode.OPERATIONAL_FAILURE,
    ) -> CommandOutcome:
        assert self._progress.thread is not None
        cause_type = None
        if bridge_disconnected:
            cause_type = PublicErrorCauseType.BRIDGE_DISCONNECTED
        elif rate_limited:
            cause_type = PublicErrorCauseType.RATE_LIMITED
        cause = (
            PublicErrorCause(
                cause_type,
                SubmissionPhase.SEND_MAY_HAVE_OCCURRED_ID_UNKNOWN,
            )
            if cause_type is not None
            else None
        )
        public_fields = {"thread": self._progress.thread}
        if handoff_action is not None:
            public_fields["handoff"] = {
                "action": handoff_action,
                "thread": self._progress.thread,
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
        assert self._progress.thread is not None
        assert self._progress.session is not None
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
                "session": self._progress.session.to_public_json(),
                "thread": self._progress.thread,
            },
        )

    def _known_session_failure(
        self,
        error_type: PublicErrorType,
    ) -> CommandOutcome:
        assert self._progress.session is not None
        return CommandOutcome.failure(
            PublicError(error_type),
            public_fields={"session": self._progress.session.to_public_json()},
        )

    def _known_public_failure(
        self,
        session: SessionAddress,
        error: PublicError,
    ) -> CommandOutcome:
        return CommandOutcome.failure(
            error,
            public_fields={"session": session.to_public_json()},
        )

    def _submission_success(
        self,
        session: SessionAddress,
        selection: tuple[OwnedPageSelection, ...],
    ) -> CommandOutcome:
        fields = {"session": session.to_public_json()}
        if selection:
            fields["selection"] = {
                selected.dimension.value: selected.label
                for selected in selection
            }
        return CommandOutcome.success(fields)

    def _transition(self, phase: SubmissionPhase) -> None:
        self._progress.phase = phase
        if self._phase_observer is not None:
            self._phase_observer(phase)


def _new_submission_thread() -> str:
    return f"{SUBMISSION_THREAD_PREFIX}{secrets.token_urlsafe(9)}"


def _human_gate_action(state: str) -> str | None:
    if state == "login_required":
        return "complete_login"
    if state == "challenge":
        return "complete_challenge"
    return None
