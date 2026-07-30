from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from surf_agent.errors import BridgeUnavailable
from surf_agent.owned_pages import (
    OwnedPageBridge,
    OwnedPageInspectionState,
    OwnedPageProtection,
    create_owned_page_bridge,
)

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
from .errors import PublicError, PublicErrorType, SubmissionPhase
from .session_address import InvalidSessionAddress, SessionAddress
from .submission_lifecycle import SubmissionLifecycle
from .surf_pages import LOGIN_THREAD, ChatGptOwnedPages


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
        self._submission = SubmissionLifecycle(
            self._pages,
            submission_thread_factory=submission_thread_factory,
            monotonic=monotonic,
            sleeper=sleeper,
            phase_observer=phase_observer,
        )

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
        if request.wait_timeout_seconds is not None:
            return self._pending_operation(request)
        return self._submission.ask(request)

    def interruption_outcome(
        self,
        exit_code: ProcessExitCode,
    ) -> CommandOutcome:
        return self._submission.interruption_outcome(exit_code)

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
        try:
            resolved = self._pages.resolve_session(request.session)
            if resolved.protection is not OwnedPageProtection.EXPLICITLY_RETAINED:
                self._pages.protect_submission(
                    resolved.page,
                    expected_protection=resolved.protection,
                    protection=OwnedPageProtection.EXPLICITLY_RETAINED,
                )
        except BridgeUnavailable:
            error = PublicError(PublicErrorType.BROWSER_UNAVAILABLE)
            return self._known_session_failure(request.session, error)
        except PublicError as error:
            return self._known_session_failure(request.session, error)
        return CommandOutcome.success(
            {
                "session": request.session.to_public_json(),
                "handoff": {
                    "action": "inspect_browser",
                    "thread": request.session.thread,
                },
            }
        )

    def abandon(self, request: AbandonRequest) -> CommandOutcome:
        return self._pending_operation(request)

    def recent(self, request: RecentSessionsRequest) -> CommandOutcome:
        return self._pending_operation(request)

    def _pending_operation(self, request: object) -> CommandOutcome:
        _ = request
        raise PublicError(PublicErrorType.UNSUPPORTED_BROWSER_CAPABILITY)

    def _known_session_failure(
        self,
        session: SessionAddress,
        error: PublicError,
    ) -> CommandOutcome:
        return CommandOutcome.failure(
            error,
            public_fields={"session": session.to_public_json()},
        )


def create_session_lifecycle() -> SessionLifecycle:
    return OwnedPageSessionLifecycle(create_owned_page_bridge())
