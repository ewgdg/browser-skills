from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass

from surf_agent.errors import BridgeIdentityUnproven, BridgeToolError, BridgeUnavailable
from surf_agent.pacing import Pacer

from .browser_port import BrowserPagePort
from .contracts import (
    AbandonRequest,
    AskRequest,
    CommandOutcome,
    CurrentSessionRequest,
    HandoffRequest,
    JsonObject,
    LoginRequest,
    ObservationMode,
    ObservationOutcome,
    ObservationRequest,
    ProcessExitCode,
    RecentSessionsRequest,
)
from .dom.attempt import (
    STOP_GENERATING_SELECTORS,
    classify_latest_attempt_source,
    extract_latest_result_source,
)
from .dom.readiness import COMPOSER_SELECTORS, current_session_classifier_source
from .dom.recent import RECENT_SESSION_LIMIT, discover_recent_sessions_source
from .dom.submission import (
    SEND_SELECTORS,
    observe_session_assignment_source,
    prepare_submission_source,
    send_submission_source,
)
from .errors import (
    PublicError,
    PublicErrorCause,
    PublicErrorCauseType,
    PublicErrorType,
    SubmissionPhase,
)
from .session_address import InvalidSessionAddress, SessionAddress


CHATGPT_HOME_URL = "https://chatgpt.com/"
SUBMISSION_THREAD_PREFIX = "surf-chatgpt-submit-"
DISCOVERY_THREAD_PREFIX = "surf-chatgpt-discovery-"
LOGIN_THREAD = "surf-chatgpt-login"
SESSION_ASSIGNMENT_TIMEOUT_SECONDS = 30.0
SESSION_ASSIGNMENT_POLL_INTERVAL_SECONDS = 0.1
OBSERVATION_POLL_INTERVAL_SECONDS = 0.25
STOP_CONFIRMATION_TIMEOUT_SECONDS = 10.0
STOP_CONFIRMATION_POLL_INTERVAL_SECONDS = 0.1


@dataclass(frozen=True)
class _SubmissionContext:
    thread: str
    session: SessionAddress | None
    temporary: bool
    retain: bool


class BrowserSessionLifecycle:
    def __init__(
        self,
        browser: BrowserPagePort,
        *,
        submission_thread_factory: Callable[[], str] | None = None,
        discovery_thread_factory: Callable[[], str] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._browser = browser
        self._submission_thread_factory = (
            submission_thread_factory or _new_submission_thread
        )
        self._discovery_thread_factory = (
            discovery_thread_factory or _new_discovery_thread
        )
        self._monotonic = monotonic
        self._sleeper = sleeper
        self._phase = SubmissionPhase.BEFORE_SEND
        self._thread: str | None = None
        self._session: SessionAddress | None = None

    def login(self, request: LoginRequest) -> CommandOutcome:
        _ = request
        try:
            self._browser.ensure(LOGIN_THREAD, CHATGPT_HOME_URL)
        except Exception as error:
            return self._browser_failure(error)
        return CommandOutcome.success(
            {"handoff": {"action": "complete_login", "thread": LOGIN_THREAD}}
        )

    def ask(self, request: AskRequest) -> CommandOutcome:
        self._phase = SubmissionPhase.BEFORE_SEND
        self._thread = None
        self._session = request.session
        try:
            outcome = self._submit(request)
        except PublicError as error:
            outcome = self._failure(error)
        except Exception as error:
            outcome = self._browser_failure(error)
        if request.wait_timeout_seconds is None:
            return outcome
        payload = outcome.to_public_json()
        if payload.get("ok") is not True or self._session is None:
            return outcome
        observed = self.observe(
            ObservationRequest(
                session=self._session,
                mode=ObservationMode.RESULT_WAIT,
                wait_timeout_seconds=request.wait_timeout_seconds,
                retain=request.retain,
            )
        )
        observed_payload = observed.to_public_json()
        if observed_payload.get("ok") is not True:
            return observed
        fields: JsonObject = {"session": observed_payload["session"]}
        if "selection" in payload:
            fields["selection"] = payload["selection"]
        for key, value in observed_payload.items():
            if key not in {"ok", "session"}:
                fields[key] = value
        return CommandOutcome.success(
            fields,
            post_output_cleanup=observed.post_output_cleanup,
        )

    def _submit(self, request: AskRequest) -> CommandOutcome:
        context = self._submission_context(request)
        self._thread = context.thread
        preparation = self._prepare(request, context.thread)
        failure = self._pre_send_state_outcome(
            preparation["state"],
            context,
        )
        if failure is not None:
            return failure

        selection = _selection(preparation)
        if selection:
            Pacer.for_name(request.pace.value).pause()

        readiness = self._evaluate(
            context.thread,
            prepare_submission_source(
                model_query=None,
                thinking_query=None,
                allow_logged_out=request.allow_logged_out,
            ),
        )
        failure = self._pre_send_state_outcome(readiness["state"], context)
        if failure is not None:
            return failure

        self._browser.fill(
            context.thread,
            ",".join(COMPOSER_SELECTORS),
            request.prompt,
        )
        submission = self._evaluate(
            context.thread,
            send_submission_source(
                request.prompt,
                allow_logged_out=request.allow_logged_out,
                pace=request.pace.value,
            ),
        )
        failure = self._pre_send_state_outcome(submission["state"], context)
        if failure is not None:
            return failure
        if submission["state"] != "submitted":
            raise PublicError(PublicErrorType.INSPECTION_FAILED)

        self._browser.click(
            context.thread,
            ",".join(SEND_SELECTORS),
            on_may_have_dispatched=lambda: self._mark_dispatched(context),
        )
        assigned = self._await_assignment(context)
        if isinstance(assigned, CommandOutcome):
            return assigned
        self._session = assigned
        self._phase = SubmissionPhase.HANDSHAKE_COMPLETE

        fields: JsonObject = {"session": assigned.to_public_json()}
        if selection:
            fields["selection"] = selection
        return CommandOutcome.success(fields)

    def _submission_context(self, request: AskRequest) -> _SubmissionContext:
        if request.session is not None:
            self._ensure_session(request.session)
            return _SubmissionContext(
                request.session.thread,
                request.session,
                temporary=False,
                retain=request.retain,
            )
        if request.thread is not None:
            if self._browser.state(request.thread) is None:
                raise PublicError(PublicErrorType.THREAD_NOT_FOUND)
            return _SubmissionContext(
                request.thread,
                None,
                temporary=False,
                retain=True,
            )
        thread = self._submission_thread_factory()
        self._browser.ensure(thread, CHATGPT_HOME_URL)
        return _SubmissionContext(
            thread,
            None,
            temporary=True,
            retain=request.retain,
        )

    def _prepare(self, request: AskRequest, thread: str) -> dict[str, object]:
        return self._evaluate(
            thread,
            prepare_submission_source(
                model_query=request.model,
                thinking_query=request.thinking,
                allow_logged_out=request.allow_logged_out,
            ),
        )

    def _mark_dispatched(self, context: _SubmissionContext) -> None:
        self._phase = (
            SubmissionPhase.ID_KNOWN_REBIND_PENDING
            if context.session is not None
            else SubmissionPhase.SEND_MAY_HAVE_OCCURRED_ID_UNKNOWN
        )

    def _await_assignment(
        self,
        context: _SubmissionContext,
    ) -> SessionAddress | CommandOutcome:
        deadline = self._monotonic() + SESSION_ASSIGNMENT_TIMEOUT_SECONDS
        while True:
            assignment = self._evaluate(
                context.thread,
                observe_session_assignment_source(),
            )
            session = _session_from_metadata(assignment)
            if session is not None:
                self._session = session
                self._phase = SubmissionPhase.ID_KNOWN_REBIND_PENDING

            state = assignment["state"]
            if state == "session":
                if session is None:
                    return self._indeterminate()
                if context.thread != session.thread:
                    try:
                        self._browser.rename(context.thread, session.thread)
                    except Exception:
                        return self._rebind_failure(session, context.thread)
                    self._thread = session.thread
                return session
            if state == "rate_limited":
                if session is not None:
                    return self._known_failure(
                        session,
                        PublicError(PublicErrorType.RATE_LIMITED),
                    )
                return self._indeterminate(rate_limited=True)
            if state in {"login_required", "challenge"}:
                action = (
                    "complete_login"
                    if state == "login_required"
                    else "complete_challenge"
                )
                if session is not None:
                    return CommandOutcome.failure(
                        PublicError(PublicErrorType.HUMAN_INTERVENTION_REQUIRED),
                        public_fields={
                            "session": session.to_public_json(),
                            "thread": context.thread,
                            "handoff": {
                                "action": action,
                                "thread": context.thread,
                            },
                        },
                    )
                return self._indeterminate(handoff_action=action)
            if state != "not_ready":
                return self._indeterminate()

            remaining = deadline - self._monotonic()
            if remaining <= 0:
                return self._indeterminate()
            self._sleeper(min(SESSION_ASSIGNMENT_POLL_INTERVAL_SECONDS, remaining))

    def _pre_send_state_outcome(
        self,
        state: object,
        context: _SubmissionContext,
    ) -> CommandOutcome | None:
        if state in {"ready", "submitted"}:
            return None
        cleanup = self._temporary_cleanup(context)
        if state == "rate_limited":
            return CommandOutcome.failure(
                PublicError(PublicErrorType.RATE_LIMITED),
                post_output_cleanup=cleanup,
            )
        if state == "model_unavailable":
            return CommandOutcome.failure(
                PublicError(PublicErrorType.MODEL_UNAVAILABLE),
                post_output_cleanup=cleanup,
            )
        if state == "ui_changed":
            return CommandOutcome.failure(
                PublicError(PublicErrorType.UI_CHANGED),
                post_output_cleanup=cleanup,
            )
        if state in {"login_required", "challenge"}:
            action = (
                "complete_login" if state == "login_required" else "complete_challenge"
            )
            return CommandOutcome.failure(
                PublicError(PublicErrorType.HUMAN_INTERVENTION_REQUIRED),
                public_fields={
                    "handoff": {"action": action, "thread": context.thread}
                },
            )
        return CommandOutcome.failure(
            PublicError(PublicErrorType.INSPECTION_FAILED),
            post_output_cleanup=cleanup,
        )

    def observe(self, request: ObservationRequest) -> CommandOutcome:
        try:
            self._ensure_session(request.session)
            if request.mode is ObservationMode.STATUS:
                metadata = self._evaluate(
                    request.session.thread,
                    classify_latest_attempt_source(),
                )
                return self._attempt_outcome(
                    request,
                    metadata,
                    include_result=False,
                    generating_outcome=ObservationOutcome.NOT_READY,
                )
            if request.mode is ObservationMode.RESULT_ONCE:
                metadata = self._evaluate(
                    request.session.thread,
                    extract_latest_result_source(),
                )
                return self._attempt_outcome(
                    request,
                    metadata,
                    include_result=True,
                    generating_outcome=ObservationOutcome.NOT_READY,
                )
            if request.mode is ObservationMode.RESULT_WAIT:
                return self._wait_for_result(request)
            raise PublicError(PublicErrorType.INTERNAL_ERROR)
        except PublicError as error:
            return self._known_failure(request.session, error)
        except Exception as error:
            return self._known_failure(
                request.session,
                _public_browser_error(error),
            )

    def _wait_for_result(self, request: ObservationRequest) -> CommandOutcome:
        timeout = request.wait_timeout_seconds
        if timeout is None or timeout <= 0:
            raise PublicError(PublicErrorType.INVALID_ARGS)
        deadline = self._monotonic() + timeout
        while True:
            metadata = self._evaluate(
                request.session.thread,
                extract_latest_result_source(),
            )
            if metadata["state"] != "generating":
                return self._attempt_outcome(
                    request,
                    metadata,
                    include_result=True,
                    generating_outcome=ObservationOutcome.TIMED_OUT,
                )
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                return self._attempt_outcome(
                    request,
                    metadata,
                    include_result=True,
                    generating_outcome=ObservationOutcome.TIMED_OUT,
                )
            self._sleeper(min(OBSERVATION_POLL_INTERVAL_SECONDS, remaining))

    def _attempt_outcome(
        self,
        request: ObservationRequest,
        metadata: dict[str, object],
        *,
        include_result: bool,
        generating_outcome: ObservationOutcome,
    ) -> CommandOutcome:
        state = metadata["state"]
        if state not in {"generating", "completed", "stopped", "failed", "rate_limited"}:
            raise PublicError(PublicErrorType.INSPECTION_FAILED)
        fields: JsonObject = {
            "session": request.session.to_public_json(),
            "attempt": {"state": state},
        }
        if include_result:
            if state == "generating":
                fields["observation"] = {"outcome": generating_outcome.value}
                fields["result"] = None
            elif state in {"completed", "stopped"}:
                text = metadata.get("text")
                if not isinstance(text, str):
                    raise PublicError(PublicErrorType.INSPECTION_FAILED)
                fields["result"] = {
                    "text": text,
                    "partial": state == "stopped",
                }
            else:
                fields["result"] = None
        elif state in {"failed", "rate_limited"}:
            fields["result"] = None
        cleanup: Callable[[], None] | None = None
        if state != "generating" and not request.retain:
            def close_terminal_page() -> None:
                self._browser.close(request.session.thread)

            cleanup = close_terminal_page
        return CommandOutcome.success(fields, post_output_cleanup=cleanup)

    def current(self, request: CurrentSessionRequest) -> CommandOutcome:
        try:
            state = self._browser.state(request.thread)
            if state is None:
                raise PublicError(PublicErrorType.THREAD_NOT_FOUND)
            metadata = self._evaluate(request.thread, current_session_classifier_source())
            if metadata["state"] in {"pre_session", "human_gate"}:
                return CommandOutcome.success(
                    {
                        "session": None,
                        "observation": {
                            "outcome": ObservationOutcome.NOT_READY.value
                        },
                    }
                )
            if metadata["state"] != "session":
                raise PublicError(PublicErrorType.INSPECTION_FAILED)
            session = SessionAddress.parse(state.url)
            return CommandOutcome.success({"session": session.to_public_json()})
        except PublicError:
            raise
        except Exception as error:
            raise _public_browser_error(error) from error

    def handoff(self, request: HandoffRequest) -> CommandOutcome:
        try:
            self._ensure_session(request.session)
        except Exception as error:
            return self._known_failure(request.session, _public_browser_error(error))
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
        if (request.session is None) == (request.thread is None):
            raise PublicError(PublicErrorType.INVALID_ARGS)
        session = request.session
        thread = session.thread if session is not None else request.thread
        assert thread is not None
        identity: JsonObject = (
            {"session": session.to_public_json()}
            if session is not None
            else {"thread": thread}
        )
        try:
            if self._browser.state(thread) is None:
                error_type = (
                    PublicErrorType.SESSION_NOT_FOUND
                    if session is not None
                    else PublicErrorType.THREAD_NOT_FOUND
                )
                return CommandOutcome.failure(
                    PublicError(error_type),
                    public_fields=identity,
                )
            metadata = self._evaluate(thread, classify_latest_attempt_source())
            attempt_state = metadata["state"]
            if attempt_state == "generating":
                self._browser.click(
                    thread,
                    ",".join(STOP_GENERATING_SELECTORS),
                    on_may_have_dispatched=lambda: None,
                )
                attempt_state = self._await_stopped(thread)
            self._browser.close(thread)
            fields = dict(identity)
            if attempt_state in {
                "completed",
                "stopped",
                "failed",
                "rate_limited",
            }:
                fields["attempt"] = {"state": attempt_state}
            return CommandOutcome.success(fields)
        except Exception:
            return CommandOutcome.failure(
                PublicError(PublicErrorType.ABANDONMENT_FAILED),
                public_fields=identity,
            )

    def _await_stopped(self, thread: str) -> str:
        deadline = self._monotonic() + STOP_CONFIRMATION_TIMEOUT_SECONDS
        while True:
            metadata = self._evaluate(thread, classify_latest_attempt_source())
            state = metadata["state"]
            if state != "generating":
                return str(state)
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise PublicError(PublicErrorType.ABANDONMENT_FAILED)
            self._sleeper(min(STOP_CONFIRMATION_POLL_INTERVAL_SECONDS, remaining))

    def recent(self, request: RecentSessionsRequest) -> CommandOutcome:
        temporary = request.thread is None
        thread = request.thread or self._discovery_thread_factory()
        if request.thread is not None and not request.thread.startswith(
            DISCOVERY_THREAD_PREFIX
        ):
            raise PublicError(PublicErrorType.THREAD_NOT_FOUND)
        try:
            if temporary:
                self._browser.ensure(thread, CHATGPT_HOME_URL)
            elif self._browser.state(thread) is None:
                raise PublicError(PublicErrorType.THREAD_NOT_FOUND)
            metadata = self._evaluate(thread, discover_recent_sessions_source())
            state = metadata["state"]
            if state in {"login_required", "challenge"}:
                action = (
                    "complete_login"
                    if state == "login_required"
                    else "complete_challenge"
                )
                return CommandOutcome.failure(
                    PublicError(PublicErrorType.HUMAN_INTERVENTION_REQUIRED),
                    public_fields={
                        "handoff": {"action": action, "thread": thread}
                    },
                )
            def close_discovery_page() -> None:
                self._browser.close(thread)

            cleanup = close_discovery_page
            if state == "ui_changed":
                return CommandOutcome.failure(
                    PublicError(PublicErrorType.UI_CHANGED),
                    post_output_cleanup=cleanup,
                )
            sessions = _recent_sessions(metadata)
            return CommandOutcome.success(
                {"sessions": sessions},
                post_output_cleanup=cleanup,
            )
        except PublicError as error:
            return CommandOutcome.failure(
                error,
                post_output_cleanup=lambda: self._browser.close(thread),
            )
        except Exception as error:
            return CommandOutcome.failure(
                _public_browser_error(error),
                post_output_cleanup=lambda: self._browser.close(thread),
            )

    def interruption_outcome(self, exit_code: ProcessExitCode) -> CommandOutcome:
        if self._phase is SubmissionPhase.SEND_MAY_HAVE_OCCURRED_ID_UNKNOWN:
            return self._indeterminate(exit_code=exit_code)
        fields: JsonObject = {}
        if self._session is not None:
            fields["session"] = self._session.to_public_json()
        return CommandOutcome.failure(
            PublicError(PublicErrorType.INTERRUPTED),
            exit_code=exit_code,
            public_fields=fields,
        )

    def _evaluate(self, thread: str, source: str) -> dict[str, object]:
        return _metadata(self._browser.evaluate(thread, source))

    def _ensure_session(self, session: SessionAddress) -> None:
        state = self._browser.state(session.thread)
        if state is None or state.url != session.canonical_url:
            self._browser.ensure(session.thread, session.canonical_url)

    def _temporary_cleanup(
        self,
        context: _SubmissionContext,
    ) -> Callable[[], None] | None:
        if not context.temporary or context.retain:
            return None
        return lambda: self._browser.close(context.thread)

    def _failure(self, error: PublicError) -> CommandOutcome:
        if self._session is not None:
            return self._known_failure(self._session, error)
        return CommandOutcome.failure(error)

    def _browser_failure(self, error: Exception) -> CommandOutcome:
        if self._phase is SubmissionPhase.SEND_MAY_HAVE_OCCURRED_ID_UNKNOWN:
            return self._indeterminate(
                bridge_disconnected=isinstance(error, BridgeUnavailable)
            )
        if self._phase is SubmissionPhase.ID_KNOWN_REBIND_PENDING:
            if self._session is not None and self._thread is not None:
                return self._rebind_failure(self._session, self._thread)
        return self._failure(_public_browser_error(error))

    def _known_failure(
        self,
        session: SessionAddress,
        error: PublicError,
    ) -> CommandOutcome:
        return CommandOutcome.failure(
            error,
            public_fields={"session": session.to_public_json()},
        )

    def _rebind_failure(
        self,
        session: SessionAddress,
        thread: str,
    ) -> CommandOutcome:
        return CommandOutcome.failure(
            PublicError(PublicErrorType.SESSION_REBIND_FAILED),
            public_fields={
                "session": session.to_public_json(),
                "thread": thread,
            },
        )

    def _indeterminate(
        self,
        *,
        bridge_disconnected: bool = False,
        rate_limited: bool = False,
        handoff_action: str | None = None,
        exit_code: ProcessExitCode = ProcessExitCode.OPERATIONAL_FAILURE,
    ) -> CommandOutcome:
        cause = None
        if bridge_disconnected:
            cause = PublicErrorCause(
                PublicErrorCauseType.BRIDGE_DISCONNECTED,
                SubmissionPhase.SEND_MAY_HAVE_OCCURRED_ID_UNKNOWN,
            )
        elif rate_limited:
            cause = PublicErrorCause(
                PublicErrorCauseType.RATE_LIMITED,
                SubmissionPhase.SEND_MAY_HAVE_OCCURRED_ID_UNKNOWN,
            )
        fields: JsonObject = {}
        if self._thread is not None:
            fields["thread"] = self._thread
        if handoff_action is not None and self._thread is not None:
            fields["handoff"] = {
                "action": handoff_action,
                "thread": self._thread,
            }
        return CommandOutcome.failure(
            PublicError(
                PublicErrorType.SUBMISSION_OUTCOME_INDETERMINATE,
                cause=cause,
            ),
            exit_code=exit_code,
            public_fields=fields,
        )


def _metadata(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise PublicError(PublicErrorType.INSPECTION_FAILED)
    state = value.get("state")
    if not isinstance(state, str) or not state:
        raise PublicError(PublicErrorType.INSPECTION_FAILED)
    return {str(key): item for key, item in value.items()}


def _selection(metadata: dict[str, object]) -> JsonObject:
    raw = metadata.get("selection", {})
    if not isinstance(raw, dict):
        raise PublicError(PublicErrorType.INSPECTION_FAILED)
    selection: JsonObject = {}
    for key, value in raw.items():
        if (
            not isinstance(key, str)
            or key not in {"model", "thinking"}
            or not isinstance(value, str)
            or not value
        ):
            raise PublicError(PublicErrorType.INSPECTION_FAILED)
        selection[key] = value
    return selection


def _session_from_metadata(metadata: dict[str, object]) -> SessionAddress | None:
    raw = metadata.get("session_id")
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise PublicError(PublicErrorType.INSPECTION_FAILED)
    try:
        return SessionAddress(raw)
    except InvalidSessionAddress as error:
        raise PublicError(PublicErrorType.INSPECTION_FAILED) from error


def _recent_sessions(metadata: dict[str, object]) -> list[JsonObject]:
    if metadata.get("state") != "sessions":
        raise PublicError(PublicErrorType.INSPECTION_FAILED)
    raw_sessions = metadata.get("sessions")
    if not isinstance(raw_sessions, list) or len(raw_sessions) > RECENT_SESSION_LIMIT:
        raise PublicError(PublicErrorType.INSPECTION_FAILED)
    sessions: list[JsonObject] = []
    seen: set[str] = set()
    for raw in raw_sessions:
        if not isinstance(raw, dict):
            raise PublicError(PublicErrorType.INSPECTION_FAILED)
        session_id = raw.get("id")
        title = raw.get("title")
        if (
            not isinstance(session_id, str)
            or not isinstance(title, str)
            or not title
            or session_id in seen
        ):
            raise PublicError(PublicErrorType.INSPECTION_FAILED)
        try:
            SessionAddress(session_id)
        except InvalidSessionAddress as error:
            raise PublicError(PublicErrorType.INSPECTION_FAILED) from error
        seen.add(session_id)
        sessions.append({"id": session_id, "title": title})
    return sessions


def _public_browser_error(error: Exception) -> PublicError:
    if isinstance(error, PublicError):
        return error
    if isinstance(error, BridgeIdentityUnproven):
        return PublicError(PublicErrorType.BROWSER_IDENTITY_UNPROVEN)
    if isinstance(error, BridgeUnavailable):
        return PublicError(PublicErrorType.BROWSER_UNAVAILABLE)
    if isinstance(error, BridgeToolError):
        return PublicError(PublicErrorType.INSPECTION_FAILED)
    return PublicError(PublicErrorType.INSPECTION_FAILED)


def _new_submission_thread() -> str:
    return f"{SUBMISSION_THREAD_PREFIX}{secrets.token_hex(12)}"


def _new_discovery_thread() -> str:
    return f"{DISCOVERY_THREAD_PREFIX}{secrets.token_urlsafe(9)}"
