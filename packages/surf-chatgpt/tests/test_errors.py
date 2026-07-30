from __future__ import annotations

import pytest

from surf_chatgpt.errors import (
    PublicError,
    PublicErrorCause,
    PublicErrorCauseType,
    PublicErrorType,
    SubmissionPhase,
)


def test_public_error_types_are_exactly_the_specification_allow_list() -> None:
    assert {error_type.value for error_type in PublicErrorType} == {
        "invalid_args",
        "empty_prompt",
        "unsupported_browser_capability",
        "browser_identity_unproven",
        "browser_unavailable",
        "capacity_exceeded",
        "human_intervention_required",
        "rate_limited",
        "submission_outcome_indeterminate",
        "session_rebind_failed",
        "session_not_found",
        "thread_not_found",
        "ownership_conflict",
        "ambiguous_session_page",
        "inspection_failed",
        "ui_changed",
        "model_unavailable",
        "abandonment_failed",
        "interrupted",
        "internal_error",
    }


def test_public_error_and_cause_project_only_allow_listed_fields() -> None:
    error = PublicError(
        PublicErrorType.SUBMISSION_OUTCOME_INDETERMINATE,
        cause=PublicErrorCause(
            PublicErrorCauseType.BRIDGE_DISCONNECTED,
            SubmissionPhase.SEND_MAY_HAVE_OCCURRED_ID_UNKNOWN,
        ),
    )

    assert error.to_public_json() == {
        "type": "submission_outcome_indeterminate",
        "message": "The prompt may have been sent, but no recoverable ChatGPT session ID was observed.",
        "hint": "Inspect the preserved thread after resolving any browser gate; do not resubmit automatically.",
        "cause": {
            "type": "bridge_disconnected",
            "phase": "send_may_have_occurred_id_unknown",
            "message": "The browser bridge connection ended.",
        },
    }


def test_rate_limit_errors_have_fixed_non_retrying_public_projections() -> None:
    assert PublicError(PublicErrorType.RATE_LIMITED).to_public_json() == {
        "type": "rate_limited",
        "message": "ChatGPT is rate limiting requests.",
        "hint": "Wait for the account limit to reset before submitting a new prompt.",
    }

    error = PublicError(
        PublicErrorType.SUBMISSION_OUTCOME_INDETERMINATE,
        cause=PublicErrorCause(
            PublicErrorCauseType.RATE_LIMITED,
            SubmissionPhase.SEND_MAY_HAVE_OCCURRED_ID_UNKNOWN,
        ),
    )
    assert error.to_public_json()["cause"] == {
        "type": "rate_limited",
        "phase": "send_may_have_occurred_id_unknown",
        "message": "ChatGPT reported a request rate limit.",
    }


def test_public_errors_do_not_accept_raw_messages_or_unstable_types() -> None:
    with pytest.raises(TypeError):
        PublicError("browser_unavailable")  # type: ignore[arg-type]

    with pytest.raises(TypeError):
        PublicError(PublicErrorType.BROWSER_UNAVAILABLE, "raw browser output")  # type: ignore[arg-type]
