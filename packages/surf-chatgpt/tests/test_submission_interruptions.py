from __future__ import annotations

import json
import signal

import pytest

from ._submission_subprocess_harness import (
    HOME_URL,
    SESSION_ID,
    SESSION_THREAD,
    SESSION_URL,
    SUBMISSION_THREAD,
    SubmissionBarrier,
    SubmissionSubprocessHarness,
)


CAUGHT_SIGNALS = (
    pytest.param(signal.SIGINT, 130, id="sigint"),
    pytest.param(signal.SIGTERM, 143, id="sigterm"),
)
ALL_BARRIERS = tuple(SubmissionBarrier)


@pytest.mark.parametrize(("signal_number", "expected_exit"), CAUGHT_SIGNALS)
@pytest.mark.parametrize("barrier", ALL_BARRIERS)
def test_caught_signal_projects_one_phase_aware_object_and_preserves_recovery(
    barrier: SubmissionBarrier,
    signal_number: signal.Signals,
    expected_exit: int,
) -> None:
    harness = SubmissionSubprocessHarness(barrier)

    result = harness.interrupt(signal_number)

    assert result.returncode == expected_exit
    assert result.stderr == ""
    assert result.json == _caught_signal_payload(barrier)
    assert result.stdout == _compact_line(result.json)
    _assert_durable_state(harness, barrier)
    _assert_recovery_without_another_send(harness, barrier)


@pytest.mark.parametrize("barrier", ALL_BARRIERS)
def test_sigkill_leaves_only_durable_side_effects_and_recoverable_binding(
    barrier: SubmissionBarrier,
) -> None:
    harness = SubmissionSubprocessHarness(barrier)

    result = harness.interrupt(signal.SIGKILL)

    assert result.returncode == -signal.SIGKILL
    assert result.stdout == ""
    assert result.stderr == ""
    _assert_durable_state(harness, barrier)
    _assert_recovery_without_another_send(harness, barrier)


@pytest.mark.parametrize("barrier", ALL_BARRIERS)
def test_bridge_disconnect_at_each_barrier_projects_recoverable_state(
    barrier: SubmissionBarrier,
) -> None:
    harness = SubmissionSubprocessHarness(barrier)

    result = harness.disconnect_bridge()

    expected_exit = 0 if barrier is SubmissionBarrier.HANDSHAKE_COMPLETE else 1
    assert result.returncode == expected_exit
    assert result.stderr == ""
    assert result.json == _bridge_disconnect_payload(barrier)
    assert result.stdout == _compact_line(result.json)
    _assert_durable_state(harness, barrier)
    _assert_recovery_without_another_send(harness, barrier)


def _caught_signal_payload(barrier: SubmissionBarrier) -> dict[str, object]:
    if barrier is SubmissionBarrier.SEND_MAY_HAVE_OCCURRED_ID_UNKNOWN:
        return {
            "ok": False,
            "error": {
                "type": "submission_outcome_indeterminate",
                "message": (
                    "The prompt may have been sent, but no recoverable ChatGPT "
                    "session ID was observed."
                ),
                "hint": (
                    "Inspect the preserved thread after resolving any browser gate; "
                    "do not resubmit automatically."
                ),
            },
            "thread": SUBMISSION_THREAD,
        }

    payload: dict[str, object] = {
        "ok": False,
        "error": {
            "type": "interrupted",
            "message": "The operation was interrupted.",
        },
    }
    if barrier in {
        SubmissionBarrier.ID_KNOWN_REBIND_PENDING,
        SubmissionBarrier.HANDSHAKE_COMPLETE,
    }:
        payload["session"] = {"id": SESSION_ID}
    return payload


def _bridge_disconnect_payload(barrier: SubmissionBarrier) -> dict[str, object]:
    if barrier is SubmissionBarrier.HANDSHAKE_COMPLETE:
        return {"ok": True, "session": {"id": SESSION_ID}}

    if barrier is SubmissionBarrier.BEFORE_SEND:
        return {
            "ok": False,
            "error": {
                "type": "browser_unavailable",
                "message": "The browser bridge or owned page is unavailable.",
                "hint": "Start or repair the dedicated Surf browser bridge, then retry.",
            },
        }

    if barrier is SubmissionBarrier.ID_KNOWN_REBIND_PENDING:
        return {
            "ok": False,
            "error": {
                "type": "session_rebind_failed",
                "message": (
                    "The ChatGPT session was assigned, but exact-page rebinding "
                    "did not complete."
                ),
                "hint": (
                    "Use the returned session and preserved thread for recovery; "
                    "do not resubmit automatically."
                ),
                "cause": {
                    "type": "bridge_disconnected",
                    "phase": "id_known_rebind_pending",
                    "message": "The browser bridge connection ended.",
                },
            },
            "session": {"id": SESSION_ID},
            "thread": SUBMISSION_THREAD,
        }

    return {
        "ok": False,
        "error": {
            "type": "submission_outcome_indeterminate",
            "message": (
                "The prompt may have been sent, but no recoverable ChatGPT "
                "session ID was observed."
            ),
            "hint": (
                "Inspect the preserved thread after resolving any browser gate; "
                "do not resubmit automatically."
            ),
            "cause": {
                "type": "bridge_disconnected",
                "phase": "send_may_have_occurred_id_unknown",
                "message": "The browser bridge connection ended.",
            },
        },
        "thread": SUBMISSION_THREAD,
    }


def _assert_durable_state(
    harness: SubmissionSubprocessHarness,
    barrier: SubmissionBarrier,
) -> None:
    if barrier is SubmissionBarrier.BEFORE_SEND:
        assert harness.state.send_count == 0
        assert harness.state.bindings == {SUBMISSION_THREAD: HOME_URL}
    elif barrier is SubmissionBarrier.SEND_MAY_HAVE_OCCURRED_ID_UNKNOWN:
        assert harness.state.send_count == 1
        assert harness.state.bindings == {SUBMISSION_THREAD: HOME_URL}
    elif barrier is SubmissionBarrier.ID_KNOWN_REBIND_PENDING:
        assert harness.state.send_count == 1
        assert harness.state.bindings == {SUBMISSION_THREAD: SESSION_URL}
    else:
        assert harness.state.send_count == 1
        assert harness.state.bindings == {SESSION_THREAD: SESSION_URL}


def _assert_recovery_without_another_send(
    harness: SubmissionSubprocessHarness,
    barrier: SubmissionBarrier,
) -> None:
    send_count = harness.state.send_count

    recovered = harness.recover_current()

    assert recovered.returncode == 0
    assert recovered.stderr == ""
    if barrier in {
        SubmissionBarrier.BEFORE_SEND,
        SubmissionBarrier.SEND_MAY_HAVE_OCCURRED_ID_UNKNOWN,
    }:
        assert recovered.json == {
            "ok": True,
            "session": None,
            "observation": {"outcome": "not_ready"},
        }
    else:
        assert recovered.json == {"ok": True, "session": {"id": SESSION_ID}}
    assert recovered.stdout == _compact_line(recovered.json)
    assert harness.state.send_count == send_count


def _compact_line(payload: dict[str, object]) -> str:
    return json.dumps(payload, separators=(",", ":")) + "\n"
