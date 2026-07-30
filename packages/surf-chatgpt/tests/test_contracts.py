from __future__ import annotations

import pytest

from surf_chatgpt.contracts import CommandOutcome, ProcessExitCode
from surf_chatgpt.errors import PublicError, PublicErrorType


def test_success_outcome_projects_one_json_object_without_private_cleanup() -> None:
    def cleanup() -> None:
        pass

    outcome = CommandOutcome.success(
        {"session": {"id": "abc123"}},
        post_output_cleanup=cleanup,
    )

    assert outcome.to_public_json() == {"ok": True, "session": {"id": "abc123"}}
    assert outcome.exit_code == 0
    assert outcome.post_output_cleanup is cleanup
    assert "post_output_cleanup" not in outcome.to_public_json()


def test_failure_outcome_uses_safe_error_projection_and_exit_code() -> None:
    outcome = CommandOutcome.failure(
        PublicError(PublicErrorType.BROWSER_UNAVAILABLE),
        exit_code=1,
    )

    assert outcome.to_public_json() == {
        "ok": False,
        "error": {
            "type": "browser_unavailable",
            "message": "The browser bridge or owned page is unavailable.",
            "hint": "Start or repair the dedicated Surf browser bridge, then retry.",
        },
    }


@pytest.mark.parametrize("value", [{"bad": object()}, {1: "non-string-key"}])
def test_outcomes_reject_values_that_are_not_json_objects(value: object) -> None:
    with pytest.raises(TypeError):
        CommandOutcome.success(value)  # type: ignore[arg-type]


def test_outcome_exit_code_must_match_its_ok_value() -> None:
    with pytest.raises(ValueError):
        CommandOutcome({"ok": True}, exit_code=1)

    with pytest.raises(ValueError):
        CommandOutcome({"ok": False}, exit_code=0)


def test_outcomes_reject_non_public_session_fields() -> None:
    with pytest.raises(ValueError):
        CommandOutcome.success(
            {
                "session": {
                    "id": "abc123",
                    "url": "https://chatgpt.com/c/abc123",
                }
            }
        )


def test_public_session_id_cannot_contain_a_canonical_url() -> None:
    with pytest.raises(ValueError):
        CommandOutcome.success(
            {"session": {"id": "https://chatgpt.com/c/abc123"}}
        )


def test_outcomes_reject_errors_that_bypass_safe_projection() -> None:
    with pytest.raises(ValueError):
        CommandOutcome(
            {
                "ok": False,
                "error": {
                    "type": "browser_unavailable",
                    "message": "raw browser output",
                },
            },
            exit_code=1,
        )


@pytest.mark.parametrize(
    ("error_type", "wrong_exit_code"),
    [
        (PublicErrorType.BROWSER_UNAVAILABLE, ProcessExitCode.INVALID_INPUT),
        (PublicErrorType.INVALID_ARGS, ProcessExitCode.OPERATIONAL_FAILURE),
        (PublicErrorType.INTERRUPTED, ProcessExitCode.OPERATIONAL_FAILURE),
    ],
)
def test_failure_exit_code_must_match_the_public_error_semantics(
    error_type: PublicErrorType,
    wrong_exit_code: ProcessExitCode,
) -> None:
    with pytest.raises(ValueError):
        CommandOutcome.failure(
            PublicError(error_type),
            exit_code=wrong_exit_code,
        )


@pytest.mark.parametrize(
    "signal_exit_code",
    [ProcessExitCode.INTERRUPTED, ProcessExitCode.TERMINATED],
)
def test_interrupted_error_accepts_only_the_two_signal_exit_codes(
    signal_exit_code: ProcessExitCode,
) -> None:
    outcome = CommandOutcome.failure(
        PublicError(PublicErrorType.INTERRUPTED),
        exit_code=signal_exit_code,
    )

    assert outcome.exit_code is signal_exit_code


@pytest.mark.parametrize(
    "signal_exit_code",
    [ProcessExitCode.INTERRUPTED, ProcessExitCode.TERMINATED],
)
def test_indeterminate_submission_can_lead_a_signal_outcome(
    signal_exit_code: ProcessExitCode,
) -> None:
    outcome = CommandOutcome.failure(
        PublicError(PublicErrorType.SUBMISSION_OUTCOME_INDETERMINATE),
        exit_code=signal_exit_code,
    )

    assert outcome.exit_code is signal_exit_code


@pytest.mark.parametrize(
    "fields",
    [
        {
            "session": {"id": "abc123"},
            "attempt": {"state": "generating"},
            "result": {"text": "unstable", "partial": False},
        },
        {
            "session": {"id": "abc123"},
            "attempt": {"state": "completed"},
            "result": {"text": "Answer", "partial": True},
        },
        {
            "session": {"id": "abc123"},
            "attempt": {"state": "stopped"},
            "result": {"text": "Partial", "partial": False},
        },
        {
            "session": {"id": "abc123"},
            "attempt": {"state": "failed"},
            "observation": {"outcome": "timed_out"},
            "result": None,
        },
    ],
)
def test_attempt_result_and_observation_fields_must_form_an_exact_schema(
    fields: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        CommandOutcome.success(fields)  # type: ignore[arg-type]
