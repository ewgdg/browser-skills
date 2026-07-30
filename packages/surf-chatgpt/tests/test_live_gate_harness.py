from __future__ import annotations

import json
import subprocess

import pytest

from ._live_chatgpt_gate import GateCommandError, JsonCommandRunner
from surf_chatgpt.session_address import SessionAddress

from .test_live_chatgpt import (
    _focused_niri_window_id,
    _gate_environment,
    _route_category,
    _wait_for_restart_recovery,
)


def test_command_failure_redacts_private_process_content() -> None:
    private_prompt = "private prompt nonce"
    private_response = "private response text"
    private_title = "private visible title"

    def fail_command(
        *args: object, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        del args, kwargs
        return subprocess.CompletedProcess(
            ["surf-chatgpt"],
            1,
            stdout=json.dumps(
                {
                    "ok": False,
                    "error": {
                        "type": "ui_changed",
                        "message": private_response,
                    },
                    "sessions": [{"id": "safe-id", "title": private_title}],
                }
            ),
            stderr=private_prompt,
        )

    runner = JsonCommandRunner({}, run=fail_command)

    with pytest.raises(GateCommandError) as captured:
        runner.chatgpt(
            "discover recent sessions", "session", "recent", stdin=private_prompt
        )

    failure = str(captured.value)
    assert failure == "discover recent sessions failed (exit 1, error ui_changed)"
    assert private_prompt not in failure
    assert private_response not in failure
    assert private_title not in failure


def test_invalid_json_failure_does_not_echo_raw_browser_output() -> None:
    raw_browser_failure = "Target crashed at non-public URL"

    def invalid_command(
        *args: object, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        del args, kwargs
        return subprocess.CompletedProcess(
            ["surf-chatgpt"],
            0,
            stdout=raw_browser_failure,
            stderr="",
        )

    runner = JsonCommandRunner({}, run=invalid_command)

    with pytest.raises(GateCommandError) as captured:
        runner.chatgpt("inspect status", "session", "status", "safe-id")

    assert str(captured.value) == "inspect status returned invalid JSON"
    assert raw_browser_failure not in str(captured.value)


def test_prompt_is_passed_only_through_stdin() -> None:
    private_prompt = "private prompt nonce"
    observed_command: list[str] = []
    observed_stdin: str | None = None

    def successful_command(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal observed_command, observed_stdin
        observed_command = command
        observed_stdin = kwargs.get("input")  # type: ignore[assignment]
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"ok":true,"session":{"id":"safe-id"}}',
            stderr="",
        )

    runner = JsonCommandRunner({}, run=successful_command)
    runner.chatgpt("submit disposable prompt", "ask", "--retain", stdin=private_prompt)

    assert private_prompt not in observed_command
    assert observed_stdin == private_prompt


def test_expected_domain_error_returns_payload_without_printing_it() -> None:
    payload = {
        "ok": False,
        "error": {
            "type": "human_intervention_required",
            "message": "private browser state",
        },
        "handoff": {"action": "complete_login", "thread": "safe-thread"},
    }

    def expected_error(
        *args: object, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        del args, kwargs
        return subprocess.CompletedProcess(
            ["surf-chatgpt"], 1, stdout=json.dumps(payload), stderr=""
        )

    runner = JsonCommandRunner({}, run=expected_error)

    assert (
        runner.chatgpt_error(
            "detect logged-out profile",
            "human_intervention_required",
            "ask",
            stdin="private prompt",
        )
        == payload
    )


def test_outcome_keeps_recovery_metadata_in_memory() -> None:
    payload = {
        "ok": False,
        "error": {"type": "human_intervention_required"},
        "handoff": {"action": "complete_login", "thread": "safe-thread"},
    }

    def domain_outcome(
        *args: object, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        del args, kwargs
        return subprocess.CompletedProcess(
            ["surf-chatgpt"], 1, stdout=json.dumps(payload), stderr="private raw error"
        )

    runner = JsonCommandRunner({}, run=domain_outcome)
    exit_code, observed = runner.chatgpt_outcome("preflight", "session", "recent")

    assert exit_code == 1
    assert observed == payload


def test_live_environment_preserves_the_normal_surf_window_identity(tmp_path) -> None:
    environment = _gate_environment(tmp_path / "state", tmp_path / "profile", 19347)

    assert "SURF_AGENT_PATCHRIGHT_APP_ID" not in environment
    assert "SURF_AGENT_PATCHRIGHT_CLASS" not in environment


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://chatgpt.com/", "root_clean"),
        ("https://chatgpt.com/?model=auto", "root_query"),
        ("https://chatgpt.com/c/safe-id", "canonical_clean"),
        ("https://chatgpt.com/c/safe-id?model=auto", "canonical_query"),
        ("https://chatgpt.com/c/safe-id#latest", "canonical_fragment"),
        ("https://chatgpt.com/share/safe-id", "other_chatgpt"),
        ("https://example.com/c/safe-id", "outside_chatgpt"),
    ],
)
def test_route_category_never_exposes_url_content(url: str, expected: str) -> None:
    assert _route_category(url) == expected


def test_restart_recovery_retries_only_temporary_inspection_failure() -> None:
    outcomes = iter(
        [
            (
                1,
                {
                    "ok": False,
                    "error": {
                        "type": "inspection_failed",
                        "message": "private hydration state",
                    },
                    "session": {"id": "abc123"},
                },
            ),
            (
                0,
                {
                    "ok": True,
                    "session": {"id": "abc123"},
                    "attempt": {"state": "completed"},
                },
            ),
        ]
    )

    class Runner:
        calls = 0

        def chatgpt_outcome(self, *args: str) -> tuple[int, dict[str, object]]:
            del args
            self.calls += 1
            return next(outcomes)

    runner = Runner()
    observed_waits: list[float] = []

    recovered = _wait_for_restart_recovery(
        runner,  # type: ignore[arg-type]
        SessionAddress("abc123"),
        monotonic=lambda: 0.0,
        wait=observed_waits.append,
    )

    assert recovered["attempt"] == {"state": "completed"}
    assert runner.calls == 2
    assert len(observed_waits) == 1


def test_restart_recovery_does_not_retry_rate_limit() -> None:
    class Runner:
        calls = 0

        def chatgpt_outcome(self, *args: str) -> tuple[int, dict[str, object]]:
            del args
            self.calls += 1
            return (
                1,
                {
                    "ok": False,
                    "error": {"type": "rate_limited"},
                    "session": {"id": "abc123"},
                },
            )

    runner = Runner()

    with pytest.raises(GateCommandError, match="error rate_limited"):
        _wait_for_restart_recovery(
            runner,  # type: ignore[arg-type]
            SessionAddress("abc123"),
            monotonic=lambda: 0.0,
            wait=lambda _: None,
        )

    assert runner.calls == 1


def test_niri_focus_probe_returns_only_the_focused_window_id() -> None:
    private_title = "private focused window title"

    def niri_window(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        del args, kwargs
        return subprocess.CompletedProcess(
            ["niri", "msg", "--json", "focused-window"],
            0,
            stdout=json.dumps(
                {
                    "id": 146,
                    "title": private_title,
                    "app_id": "private-app",
                    "is_focused": True,
                }
            ),
            stderr="",
        )

    assert _focused_niri_window_id(run=niri_window) == 146
