from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final


class PublicErrorType(StrEnum):
    INVALID_ARGS = "invalid_args"
    EMPTY_PROMPT = "empty_prompt"
    BROWSER_IDENTITY_UNPROVEN = "browser_identity_unproven"
    BROWSER_UNAVAILABLE = "browser_unavailable"
    HUMAN_INTERVENTION_REQUIRED = "human_intervention_required"
    RATE_LIMITED = "rate_limited"
    SUBMISSION_OUTCOME_INDETERMINATE = "submission_outcome_indeterminate"
    SESSION_REBIND_FAILED = "session_rebind_failed"
    SESSION_NOT_FOUND = "session_not_found"
    THREAD_NOT_FOUND = "thread_not_found"
    INSPECTION_FAILED = "inspection_failed"
    UI_CHANGED = "ui_changed"
    MODEL_UNAVAILABLE = "model_unavailable"
    ABANDONMENT_FAILED = "abandonment_failed"
    INTERRUPTED = "interrupted"
    INTERNAL_ERROR = "internal_error"


class PublicErrorCauseType(StrEnum):
    # The accepted specification defines no general cause vocabulary. Keep the
    # demonstrated bridge-disconnect cause closed until a lifecycle ticket needs more.
    BRIDGE_DISCONNECTED = "bridge_disconnected"
    RATE_LIMITED = "rate_limited"


class SubmissionPhase(StrEnum):
    BEFORE_SEND = "before_send"
    SEND_MAY_HAVE_OCCURRED_ID_UNKNOWN = "send_may_have_occurred_id_unknown"
    ID_KNOWN_REBIND_PENDING = "id_known_rebind_pending"
    HANDSHAKE_COMPLETE = "handshake_complete"
    OBSERVING = "observing"


@dataclass(frozen=True)
class _PublicErrorDescription:
    message: str
    hint: str | None = None


_PUBLIC_ERROR_DESCRIPTIONS: Final[dict[PublicErrorType, _PublicErrorDescription]] = {
    PublicErrorType.INVALID_ARGS: _PublicErrorDescription(
        "The command arguments are invalid.",
        "Use --help to inspect the supported command grammar.",
    ),
    PublicErrorType.EMPTY_PROMPT: _PublicErrorDescription(
        "The prompt is empty.",
        "Pass a non-empty prompt argument or provide one on stdin.",
    ),
    PublicErrorType.BROWSER_IDENTITY_UNPROVEN: _PublicErrorDescription(
        "The dedicated browser profile identity could not be proven.",
    ),
    PublicErrorType.BROWSER_UNAVAILABLE: _PublicErrorDescription(
        "The browser bridge or page is unavailable.",
        "Start or repair the dedicated Surf browser bridge, then retry.",
    ),
    PublicErrorType.HUMAN_INTERVENTION_REQUIRED: _PublicErrorDescription(
        "The browser requires user intervention.",
        "Complete the requested browser action manually before retrying.",
    ),
    PublicErrorType.RATE_LIMITED: _PublicErrorDescription(
        "ChatGPT is rate limiting requests.",
        "Wait for the account limit to reset before submitting a new prompt.",
    ),
    PublicErrorType.SUBMISSION_OUTCOME_INDETERMINATE: _PublicErrorDescription(
        "The prompt may have been sent, but no recoverable ChatGPT session ID was observed.",
        "Inspect the preserved thread after resolving any browser gate; do not resubmit automatically.",
    ),
    PublicErrorType.SESSION_REBIND_FAILED: _PublicErrorDescription(
        "The ChatGPT session was assigned, but exact-page rebinding did not complete.",
        "Use the returned session and preserved thread for recovery; do not resubmit automatically.",
    ),
    PublicErrorType.SESSION_NOT_FOUND: _PublicErrorDescription(
        "The ChatGPT session could not be resolved.",
    ),
    PublicErrorType.THREAD_NOT_FOUND: _PublicErrorDescription(
        "The preserved Surf thread could not be resolved.",
    ),
    PublicErrorType.INSPECTION_FAILED: _PublicErrorDescription(
        "The browser page state could not be safely classified.",
        "Inspect the preserved page manually before retrying.",
    ),
    PublicErrorType.UI_CHANGED: _PublicErrorDescription(
        "The required ChatGPT interface could not be identified.",
        "Update surf-chatgpt for the current ChatGPT interface before retrying.",
    ),
    PublicErrorType.MODEL_UNAVAILABLE: _PublicErrorDescription(
        "The requested model or thinking choice could not be affirmed.",
        "Choose an option visible in the current ChatGPT picker.",
    ),
    PublicErrorType.ABANDONMENT_FAILED: _PublicErrorDescription(
        "The retained browser page could not be safely abandoned.",
        "The page remains preserved for manual inspection.",
    ),
    PublicErrorType.INTERRUPTED: _PublicErrorDescription(
        "The operation was interrupted.",
    ),
    PublicErrorType.INTERNAL_ERROR: _PublicErrorDescription(
        "An internal surf-chatgpt error occurred.",
        "Retry once; if the failure persists, update surf-chatgpt.",
    ),
}


_PUBLIC_CAUSE_MESSAGES: Final[dict[PublicErrorCauseType, str]] = {
    PublicErrorCauseType.BRIDGE_DISCONNECTED: "The browser bridge connection ended.",
    PublicErrorCauseType.RATE_LIMITED: "ChatGPT reported a request rate limit.",
}


@dataclass(frozen=True)
class PublicErrorCause:
    type: PublicErrorCauseType
    phase: SubmissionPhase

    def __post_init__(self) -> None:
        if not isinstance(self.type, PublicErrorCauseType):
            raise TypeError("Public error causes require an allow-listed cause type.")
        if not isinstance(self.phase, SubmissionPhase):
            raise TypeError("Public error causes require an allow-listed phase.")

    def to_public_json(self) -> dict[str, str]:
        return {
            "type": self.type.value,
            "phase": self.phase.value,
            "message": _PUBLIC_CAUSE_MESSAGES[self.type],
        }


class PublicError(Exception):
    def __init__(
        self,
        error_type: PublicErrorType,
        *,
        cause: PublicErrorCause | None = None,
    ) -> None:
        if not isinstance(error_type, PublicErrorType):
            raise TypeError("Public errors require an allow-listed error type.")
        if cause is not None and not isinstance(cause, PublicErrorCause):
            raise TypeError("Public errors require an allow-listed cause.")
        self.type = error_type
        self.cause = cause
        super().__init__(_PUBLIC_ERROR_DESCRIPTIONS[error_type].message)

    def to_public_json(self) -> dict[str, Any]:
        description = _PUBLIC_ERROR_DESCRIPTIONS[self.type]
        result: dict[str, Any] = {
            "type": self.type.value,
            "message": description.message,
        }
        if description.hint is not None:
            result["hint"] = description.hint
        if self.cause is not None:
            result["cause"] = self.cause.to_public_json()
        return result
