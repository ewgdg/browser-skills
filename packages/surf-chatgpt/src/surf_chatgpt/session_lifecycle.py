from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol

from surf_agent.errors import BridgeUnavailable
from surf_agent.owned_pages import (
    OwnedPageBridge,
    OwnedPageAttemptState,
    OwnedPageInspectionState,
    OwnedPageProtection,
    ResolvedOwnedPage,
    create_owned_page_bridge,
)

from .contracts import (
    AbandonRequest,
    AskRequest,
    CommandOutcome,
    CurrentSessionRequest,
    HandoffRequest,
    LoginRequest,
    JsonObject,
    ObservationMode,
    ObservationOutcome,
    ObservationRequest,
    ProcessExitCode,
    RecentSessionsRequest,
)
from .errors import PublicError, PublicErrorType, SubmissionPhase
from .session_address import InvalidSessionAddress, SessionAddress
from .submission_lifecycle import SubmissionLifecycle
from .surf_pages import LOGIN_THREAD, ChatGptOwnedPages


OBSERVATION_POLL_INTERVAL_SECONDS = 0.25


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
        self._monotonic = monotonic or time.monotonic
        self._sleeper = sleeper or time.sleep
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
        submission = self._submission.ask(request)
        if request.wait_timeout_seconds is None:
            return submission
        submitted_payload = submission.to_public_json()
        if submitted_payload.get("ok") is not True:
            return submission
        session_value = submitted_payload.get("session")
        if not isinstance(session_value, dict) or not isinstance(
            session_value.get("id"), str
        ):
            raise PublicError(PublicErrorType.INTERNAL_ERROR)
        try:
            session = SessionAddress.parse(session_value["id"])
        except InvalidSessionAddress as error:
            raise PublicError(PublicErrorType.INTERNAL_ERROR) from error
        observation = self.observe(
            ObservationRequest(
                session=session,
                mode=ObservationMode.RESULT_WAIT,
                wait_timeout_seconds=request.wait_timeout_seconds,
                retain=request.retain,
            )
        )
        observed_payload = observation.to_public_json()
        if observed_payload.get("ok") is not True:
            return observation
        fields: JsonObject = {"session": observed_payload["session"]}
        if "selection" in submitted_payload:
            fields["selection"] = submitted_payload["selection"]
        for key, value in observed_payload.items():
            if key not in {"ok", "session"}:
                fields[key] = value
        return CommandOutcome.success(
            fields,
            post_output_cleanup=observation.post_output_cleanup,
        )

    def interruption_outcome(
        self,
        exit_code: ProcessExitCode,
    ) -> CommandOutcome:
        return self._submission.interruption_outcome(exit_code)

    def observe(self, request: ObservationRequest) -> CommandOutcome:
        try:
            resolved = self._resolve_observation_page(request)
            if request.mode is ObservationMode.STATUS:
                attempt = self._pages.classify_attempt(resolved)
                fields: JsonObject = {
                    "session": request.session.to_public_json(),
                    "attempt": {"state": attempt.state.value},
                }
                if attempt.state is OwnedPageAttemptState.FAILED:
                    fields["result"] = None
                return CommandOutcome.success(
                    fields,
                    post_output_cleanup=self._terminal_cleanup(
                        resolved,
                        attempt.state,
                    ),
                )
            if request.mode is ObservationMode.RESULT_ONCE:
                result = self._pages.extract_result(resolved)
                return self._result_outcome(
                    request,
                    resolved,
                    result.state,
                    result.text,
                    generating_outcome=ObservationOutcome.NOT_READY,
                )
            if request.mode is ObservationMode.RESULT_WAIT:
                return self._wait_for_result(request, resolved)
            return self._pending_operation(request)
        except BridgeUnavailable:
            error = PublicError(PublicErrorType.BROWSER_UNAVAILABLE)
            return self._known_session_failure(request.session, error)
        except ValueError:
            error = PublicError(PublicErrorType.INSPECTION_FAILED)
            return self._known_session_failure(request.session, error)
        except PublicError as error:
            return self._known_session_failure(request.session, error)

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

    def _resolve_observation_page(
        self,
        request: ObservationRequest,
    ) -> ResolvedOwnedPage:
        resolved = self._pages.resolve_session(request.session)
        if (
            request.retain
            and resolved.protection is not OwnedPageProtection.EXPLICITLY_RETAINED
        ):
            self._pages.protect_submission(
                resolved.page,
                expected_protection=resolved.protection,
                protection=OwnedPageProtection.EXPLICITLY_RETAINED,
            )
            return ResolvedOwnedPage(
                page=resolved.page,
                protection=OwnedPageProtection.EXPLICITLY_RETAINED,
            )
        return resolved

    def _terminal_cleanup(
        self,
        resolved: ResolvedOwnedPage,
        state: OwnedPageAttemptState,
    ) -> Callable[[], None] | None:
        if (
            state is OwnedPageAttemptState.GENERATING
            or resolved.protection is not None
        ):
            return None
        return lambda: self._pages.close_terminal(resolved, state)

    def _result_fields(
        self,
        request: ObservationRequest,
        state: OwnedPageAttemptState,
        text: str | None,
        *,
        generating_outcome: ObservationOutcome,
    ) -> JsonObject:
        fields: JsonObject = {
            "session": request.session.to_public_json(),
            "attempt": {"state": state.value},
        }
        if state is OwnedPageAttemptState.GENERATING:
            fields["observation"] = {"outcome": generating_outcome.value}
            fields["result"] = None
        elif state is OwnedPageAttemptState.COMPLETED:
            if text is None:
                raise PublicError(PublicErrorType.INSPECTION_FAILED)
            fields["result"] = {"text": text, "partial": False}
        elif state is OwnedPageAttemptState.STOPPED:
            if text is None:
                raise PublicError(PublicErrorType.INSPECTION_FAILED)
            fields["result"] = {"text": text, "partial": True}
        else:
            fields["result"] = None
        return fields

    def _result_outcome(
        self,
        request: ObservationRequest,
        resolved: ResolvedOwnedPage,
        state: OwnedPageAttemptState,
        text: str | None,
        *,
        generating_outcome: ObservationOutcome,
    ) -> CommandOutcome:
        return CommandOutcome.success(
            self._result_fields(
                request,
                state,
                text,
                generating_outcome=generating_outcome,
            ),
            post_output_cleanup=self._terminal_cleanup(resolved, state),
        )

    def _wait_for_result(
        self,
        request: ObservationRequest,
        resolved: ResolvedOwnedPage,
    ) -> CommandOutcome:
        timeout = request.wait_timeout_seconds
        if timeout is None or timeout <= 0:
            raise PublicError(PublicErrorType.INVALID_ARGS)
        deadline = self._monotonic() + timeout
        while True:
            result = self._pages.extract_result(resolved)
            if result.state is not OwnedPageAttemptState.GENERATING:
                return self._result_outcome(
                    request,
                    resolved,
                    result.state,
                    result.text,
                    generating_outcome=ObservationOutcome.TIMED_OUT,
                )
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                return self._result_outcome(
                    request,
                    resolved,
                    result.state,
                    result.text,
                    generating_outcome=ObservationOutcome.TIMED_OUT,
                )
            self._sleeper(min(OBSERVATION_POLL_INTERVAL_SECONDS, remaining))

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
