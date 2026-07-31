from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from typing import TypeAlias

from .errors import (
    PublicError,
    PublicErrorCause,
    PublicErrorCauseType,
    PublicErrorType,
    SubmissionPhase,
)
from .session_address import InvalidSessionAddress, SessionAddress


DEFAULT_OBSERVATION_TIMEOUT_SECONDS = 2700.0

JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]
PostOutputCleanup: TypeAlias = Callable[[], None]


class ProcessExitCode(IntEnum):
    DOMAIN_OUTCOME = 0
    OPERATIONAL_FAILURE = 1
    INVALID_INPUT = 2
    INTERRUPTED = 130
    TERMINATED = 143


_INPUT_ERROR_TYPES = {
    PublicErrorType.INVALID_ARGS,
    PublicErrorType.EMPTY_PROMPT,
}


@dataclass(frozen=True)
class _ErrorExitSemantics:
    default: ProcessExitCode
    allowed: frozenset[ProcessExitCode]


_OPERATIONAL_ERROR_EXIT_SEMANTICS = _ErrorExitSemantics(
    default=ProcessExitCode.OPERATIONAL_FAILURE,
    allowed=frozenset({ProcessExitCode.OPERATIONAL_FAILURE}),
)
_ERROR_EXIT_SEMANTICS = {
    **{
        error_type: _ErrorExitSemantics(
            default=ProcessExitCode.INVALID_INPUT,
            allowed=frozenset({ProcessExitCode.INVALID_INPUT}),
        )
        for error_type in _INPUT_ERROR_TYPES
    },
    PublicErrorType.INTERRUPTED: _ErrorExitSemantics(
        default=ProcessExitCode.INTERRUPTED,
        allowed=frozenset(
            {
                ProcessExitCode.INTERRUPTED,
                ProcessExitCode.TERMINATED,
            }
        ),
    ),
    PublicErrorType.SUBMISSION_OUTCOME_INDETERMINATE: _ErrorExitSemantics(
        default=ProcessExitCode.OPERATIONAL_FAILURE,
        allowed=frozenset(
            {
                ProcessExitCode.OPERATIONAL_FAILURE,
                ProcessExitCode.INTERRUPTED,
                ProcessExitCode.TERMINATED,
            }
        ),
    ),
}


def _error_exit_semantics(error_type: PublicErrorType) -> _ErrorExitSemantics:
    return _ERROR_EXIT_SEMANTICS.get(
        error_type,
        _OPERATIONAL_ERROR_EXIT_SEMANTICS,
    )


def allowed_exit_codes_for_error_type(
    error_type: PublicErrorType,
) -> set[ProcessExitCode]:
    return set(_error_exit_semantics(error_type).allowed)


def exit_code_for_error_type(error_type: PublicErrorType) -> ProcessExitCode:
    return _error_exit_semantics(error_type).default


class Pace(StrEnum):
    NATURAL = "natural"
    NONE = "none"


class ObservationMode(StrEnum):
    STATUS = "status"
    RESULT_ONCE = "result_once"
    RESULT_WAIT = "result_wait"


class AttemptState(StrEnum):
    GENERATING = "generating"
    COMPLETED = "completed"
    STOPPED = "stopped"
    FAILED = "failed"
    RATE_LIMITED = "rate_limited"


class ObservationOutcome(StrEnum):
    NOT_READY = "not_ready"
    TIMED_OUT = "timed_out"


class HandoffAction(StrEnum):
    COMPLETE_LOGIN = "complete_login"
    COMPLETE_CHALLENGE = "complete_challenge"
    INSPECT_BROWSER = "inspect_browser"


@dataclass(frozen=True)
class AskRequest:
    prompt: str
    session: SessionAddress | None = None
    thread: str | None = None
    model: str | None = None
    thinking: str | None = None
    wait_timeout_seconds: float | None = None
    retain: bool = False
    pace: Pace = Pace.NATURAL
    allow_logged_out: bool = False


@dataclass(frozen=True)
class ObservationRequest:
    session: SessionAddress
    mode: ObservationMode
    wait_timeout_seconds: float | None = None
    retain: bool = False


@dataclass(frozen=True)
class CurrentSessionRequest:
    thread: str


@dataclass(frozen=True)
class HandoffRequest:
    session: SessionAddress


@dataclass(frozen=True)
class AbandonRequest:
    session: SessionAddress | None = None
    thread: str | None = None


@dataclass(frozen=True)
class RecentSessionsRequest:
    thread: str | None = None


@dataclass(frozen=True)
class LoginRequest:
    pass


@dataclass(frozen=True, init=False)
class CommandOutcome:
    _public_value: JsonObject
    exit_code: ProcessExitCode
    post_output_cleanup: PostOutputCleanup | None

    def __init__(
        self,
        public_value: Mapping[str, JsonValue],
        *,
        exit_code: ProcessExitCode | int = ProcessExitCode.DOMAIN_OUTCOME,
        post_output_cleanup: PostOutputCleanup | None = None,
    ) -> None:
        copied_value = _copy_json_object(public_value)
        _validate_public_contract(copied_value)
        try:
            normalized_exit_code = ProcessExitCode(exit_code)
        except ValueError as invalid_exit_code:
            raise ValueError("Command outcomes require a specified public exit code.") from invalid_exit_code
        if normalized_exit_code not in _allowed_exit_codes(copied_value):
            raise ValueError("Command outcome exit code does not match its public result semantics.")
        if post_output_cleanup is not None and not callable(post_output_cleanup):
            raise TypeError("Post-output cleanup must be callable.")

        object.__setattr__(self, "_public_value", copied_value)
        object.__setattr__(self, "exit_code", normalized_exit_code)
        object.__setattr__(self, "post_output_cleanup", post_output_cleanup)

    @classmethod
    def success(
        cls,
        public_fields: Mapping[str, JsonValue],
        *,
        post_output_cleanup: PostOutputCleanup | None = None,
    ) -> CommandOutcome:
        if "ok" in public_fields:
            raise ValueError("Success fields must not replace the ok discriminator.")
        return cls(
            {"ok": True, **public_fields},
            post_output_cleanup=post_output_cleanup,
        )

    @classmethod
    def failure(
        cls,
        error: PublicError,
        *,
        exit_code: ProcessExitCode | int = ProcessExitCode.OPERATIONAL_FAILURE,
        public_fields: Mapping[str, JsonValue] | None = None,
        post_output_cleanup: PostOutputCleanup | None = None,
    ) -> CommandOutcome:
        fields = dict(public_fields or {})
        if {"ok", "error"} & fields.keys():
            raise ValueError("Failure fields must not replace public discriminators.")
        return cls(
            {"ok": False, "error": error.to_public_json(), **fields},
            exit_code=exit_code,
            post_output_cleanup=post_output_cleanup,
        )

    def to_public_json(self) -> JsonObject:
        return _copy_json_object(self._public_value)


def _copy_json_object(value: Mapping[str, JsonValue]) -> JsonObject:
    if not isinstance(value, Mapping):
        raise TypeError("Command outcomes require a JSON object.")
    copied: JsonObject = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise TypeError("JSON object keys must be strings.")
        copied[key] = _copy_json_value(item)
    return copied


def _copy_json_value(value: JsonValue) -> JsonValue:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TypeError("JSON numbers must be finite.")
        return value
    if isinstance(value, list):
        return [_copy_json_value(item) for item in value]
    if isinstance(value, Mapping):
        return _copy_json_object(value)
    raise TypeError("Command outcomes may contain only JSON values.")


def _validate_public_contract(value: JsonObject) -> None:
    if value.get("ok") is True:
        _validate_success_contract(value)
        return
    if value.get("ok") is False:
        _validate_failure_contract(value)
        return
    raise ValueError("Command outcomes require a boolean ok field.")


def _allowed_exit_codes(value: JsonObject) -> set[ProcessExitCode]:
    if value["ok"] is True:
        return {ProcessExitCode.DOMAIN_OUTCOME}

    error = _require_object(value["error"], "error")
    error_type = PublicErrorType(_require_string(error["type"], "error type"))
    return allowed_exit_codes_for_error_type(error_type)


def _validate_success_contract(value: JsonObject) -> None:
    _require_allowed_keys(
        value,
        {
            "ok",
            "session",
            "sessions",
            "selection",
            "attempt",
            "observation",
            "result",
            "handoff",
            "thread",
        },
    )
    if "session" in value:
        _validate_session(value["session"], allow_none=True)
    if "sessions" in value:
        sessions = _require_list(value["sessions"], "sessions")
        for session in sessions:
            session_object = _require_object(session, "recent session")
            _require_exact_keys(session_object, {"id", "title"})
            _validate_session({"id": session_object["id"]}, allow_none=False)
            _require_string(session_object["title"], "recent session title")
    if "selection" in value:
        selection = _require_object(value["selection"], "selection")
        _require_allowed_keys(selection, {"model", "thinking"})
        if not selection:
            raise ValueError("Selection must contain a requested picker dimension.")
        for label in selection.values():
            _require_string(label, "selection label")
    if "attempt" in value:
        attempt = _require_object(value["attempt"], "attempt")
        _require_exact_keys(attempt, {"state"})
        _require_enum_value(attempt["state"], AttemptState, "attempt state")
    if "observation" in value:
        observation = _require_object(value["observation"], "observation")
        _require_exact_keys(observation, {"outcome"})
        _require_enum_value(
            observation["outcome"],
            ObservationOutcome,
            "observation outcome",
        )
    if "result" in value and value["result"] is not None:
        result = _require_object(value["result"], "result")
        _require_exact_keys(result, {"text", "partial"})
        _require_string(result["text"], "result text", allow_empty=True)
        if not isinstance(result["partial"], bool):
            raise ValueError("Result partial must be boolean.")
    if "handoff" in value:
        _validate_handoff(value["handoff"])
    if "thread" in value:
        _require_string(value["thread"], "thread")
    _validate_attempt_relationships(value)


def _validate_attempt_relationships(value: JsonObject) -> None:
    if "attempt" not in value:
        if "result" in value:
            raise ValueError("Result requires an affirmed attempt state.")
        return
    attempt = _require_object(value["attempt"], "attempt")
    state = AttemptState(_require_string(attempt["state"], "attempt state"))
    if "observation" in value:
        if state is not AttemptState.GENERATING or value.get("result") is not None:
            raise ValueError("Observation outcomes require a generating attempt.")
    if "result" not in value:
        return
    result = value["result"]
    if result is None:
        if state not in {
            AttemptState.GENERATING,
            AttemptState.FAILED,
            AttemptState.RATE_LIMITED,
        }:
            raise ValueError("Terminal answer states require their result text.")
        return
    result_object = _require_object(result, "result")
    partial = result_object["partial"]
    if state is AttemptState.COMPLETED and partial is False:
        return
    if state is AttemptState.STOPPED and partial is True:
        return
    raise ValueError("Result partiality does not match the attempt state.")


def _validate_failure_contract(value: JsonObject) -> None:
    _require_allowed_keys(value, {"ok", "error", "session", "thread", "handoff"})
    if "error" not in value:
        raise ValueError("Failure outcomes require a safe public error.")
    _validate_public_error(value["error"])
    if "session" in value:
        _validate_session(value["session"], allow_none=False)
    if "thread" in value:
        _require_string(value["thread"], "thread")
    if "handoff" in value:
        _validate_handoff(value["handoff"])


def _validate_public_error(value: JsonValue) -> None:
    error = _require_object(value, "error")
    _require_allowed_keys(error, {"type", "message", "hint", "cause"})
    try:
        error_type = PublicErrorType(_require_string(error.get("type"), "error type"))
    except ValueError as invalid_type:
        raise ValueError("Error type is not allow-listed.") from invalid_type

    cause: PublicErrorCause | None = None
    if "cause" in error:
        cause_value = _require_object(error["cause"], "error cause")
        _require_exact_keys(cause_value, {"type", "phase", "message"})
        try:
            cause = PublicErrorCause(
                PublicErrorCauseType(
                    _require_string(cause_value["type"], "error cause type")
                ),
                SubmissionPhase(
                    _require_string(cause_value["phase"], "error cause phase")
                ),
            )
        except ValueError as invalid_cause:
            raise ValueError("Error cause is not allow-listed.") from invalid_cause

    if error != PublicError(error_type, cause=cause).to_public_json():
        raise ValueError("Error must use the fixed safe public projection.")


def _validate_session(value: JsonValue, *, allow_none: bool) -> None:
    if value is None and allow_none:
        return
    session = _require_object(value, "session")
    _require_exact_keys(session, {"id"})
    session_id = _require_string(session["id"], "session id")
    try:
        SessionAddress(session_id)
    except InvalidSessionAddress as invalid_session:
        raise ValueError("Session object contains an invalid ID.") from invalid_session


def _validate_handoff(value: JsonValue) -> None:
    handoff = _require_object(value, "handoff")
    _require_exact_keys(handoff, {"action", "thread"})
    _require_enum_value(handoff["action"], HandoffAction, "handoff action")
    _require_string(handoff["thread"], "handoff thread")


def _require_object(value: JsonValue, name: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{name.capitalize()} must be a JSON object.")
    return value


def _require_list(value: JsonValue, name: str) -> list[JsonValue]:
    if not isinstance(value, list):
        raise ValueError(f"{name.capitalize()} must be a JSON array.")
    return value


def _require_string(value: JsonValue, name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise ValueError(f"{name.capitalize()} must be a string.")
    return value


def _require_enum_value(
    value: JsonValue,
    enum_type: type[StrEnum],
    name: str,
) -> None:
    raw_value = _require_string(value, name)
    try:
        enum_type(raw_value)
    except ValueError as invalid_value:
        raise ValueError(f"{name.capitalize()} is not allow-listed.") from invalid_value


def _require_exact_keys(value: JsonObject, expected: set[str]) -> None:
    if set(value) != expected:
        raise ValueError("Public JSON object has missing or unexpected fields.")


def _require_allowed_keys(value: JsonObject, allowed: set[str]) -> None:
    if not set(value) <= allowed:
        raise ValueError("Public JSON object has unexpected fields.")
