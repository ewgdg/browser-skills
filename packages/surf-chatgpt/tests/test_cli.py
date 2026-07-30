from __future__ import annotations

import contextlib
import io
import json
import signal
import sys
from dataclasses import dataclass, field
from typing import Any

import pytest

from surf_chatgpt import cli
from surf_chatgpt.contracts import (
    AbandonRequest,
    AskRequest,
    CommandOutcome,
    CurrentSessionRequest,
    HandoffRequest,
    LoginRequest,
    ObservationMode,
    ObservationRequest,
    Pace,
    ProcessExitCode,
    RecentSessionsRequest,
)
from surf_chatgpt.errors import PublicError, PublicErrorType
from surf_chatgpt.session_address import SessionAddress


SUCCESS = CommandOutcome.success({"session": {"id": "abc123"}})


@dataclass
class RecordingLifecycle:
    outcome: CommandOutcome = SUCCESS
    calls: list[tuple[str, object]] = field(default_factory=list)

    def _record(self, operation: str, request: object) -> CommandOutcome:
        self.calls.append((operation, request))
        return self.outcome

    def ask(self, request: AskRequest) -> CommandOutcome:
        return self._record("ask", request)

    def interruption_outcome(
        self,
        exit_code: ProcessExitCode,
    ) -> CommandOutcome:
        return CommandOutcome.failure(
            PublicError(PublicErrorType.INTERRUPTED),
            exit_code=exit_code,
        )

    def observe(self, request: ObservationRequest) -> CommandOutcome:
        return self._record("observe", request)

    def current(self, request: CurrentSessionRequest) -> CommandOutcome:
        return self._record("current", request)

    def handoff(self, request: HandoffRequest) -> CommandOutcome:
        return self._record("handoff", request)

    def abandon(self, request: AbandonRequest) -> CommandOutcome:
        return self._record("abandon", request)

    def recent(self, request: RecentSessionsRequest) -> CommandOutcome:
        return self._record("recent", request)

    def login(self, request: LoginRequest) -> CommandOutcome:
        return self._record("login", request)


class RaisingLifecycle(RecordingLifecycle):
    def __init__(self, raised: BaseException) -> None:
        super().__init__()
        self.raised = raised

    def _record(self, operation: str, request: object) -> CommandOutcome:
        raise self.raised


def invoke(
    argv: list[str],
    *,
    lifecycle: RecordingLifecycle | None = None,
    stdin: str = "",
    stdout: io.StringIO | None = None,
) -> tuple[int, dict[str, Any], str, RecordingLifecycle]:
    active_lifecycle = lifecycle or RecordingLifecycle()
    output = stdout or io.StringIO()
    errors = io.StringIO()
    code = cli.main(
        argv,
        stdin=io.StringIO(stdin),
        stdout=output,
        stderr=errors,
        lifecycle=active_lifecycle,
    )
    rendered = output.getvalue()
    payload = json.loads(rendered)
    return code, payload, errors.getvalue(), active_lifecycle


def test_plain_ask_dispatches_one_typed_request_and_one_compact_json_object() -> None:
    code, payload, stderr, lifecycle = invoke(["ask", "hello"])

    assert code == 0
    assert payload == {"ok": True, "session": {"id": "abc123"}}
    assert stderr == ""
    assert lifecycle.calls == [
        (
            "ask",
            AskRequest(prompt="hello", pace=Pace.NATURAL),
        )
    ]

    output = io.StringIO()
    cli.main(["ask", "hello"], stdin=io.StringIO(), stdout=output, lifecycle=RecordingLifecycle())
    assert output.getvalue() == '{"ok":true,"session":{"id":"abc123"}}\n'


def test_ask_uses_stdin_only_when_the_positional_prompt_is_absent() -> None:
    _, _, _, stdin_lifecycle = invoke(["ask"], stdin="from stdin\n")
    _, _, _, positional_lifecycle = invoke(["ask", "argument"], stdin="ignored")

    assert stdin_lifecycle.calls[0][1] == AskRequest(prompt="from stdin\n", pace=Pace.NATURAL)
    assert positional_lifecycle.calls[0][1] == AskRequest(prompt="argument", pace=Pace.NATURAL)


def test_ask_normalizes_addressing_and_all_specified_options_before_dispatch() -> None:
    _, _, _, lifecycle = invoke(
        [
            "ask",
            "--session",
            "https://chatgpt.com/c/ABC_123-x",
            "--model",
            "latest",
            "--thinking",
            "pro",
            "--wait=12.5",
            "--retain",
            "--pace",
            "none",
            "--allow-logged-out",
            "follow up",
        ]
    )

    request = lifecycle.calls[0][1]
    assert isinstance(request, AskRequest)
    assert request.session is not None
    assert request.session.id == "ABC_123-x"
    assert request.session.to_public_json() == {"id": "ABC_123-x"}
    assert request.session.canonical_url == "https://chatgpt.com/c/ABC_123-x"
    assert request.session.thread == "surf-chatgpt-session-ABC_123-x"
    assert request.model == "latest"
    assert request.thinking == "pro"
    assert request.wait_timeout_seconds == 12.5
    assert request.retain is True
    assert request.pace is Pace.NONE
    assert request.allow_logged_out is True


def test_bare_wait_preserves_the_following_prompt_and_uses_the_default_timeout() -> None:
    _, _, _, lifecycle = invoke(["ask", "--wait", "prompt"])

    assert lifecycle.calls == [
        (
            "ask",
            AskRequest(
                prompt="prompt",
                wait_timeout_seconds=2700.0,
                pace=Pace.NATURAL,
            ),
        )
    ]


@pytest.mark.parametrize(
    "argv",
    [
        ["ask", "--session", "abc", "--thread", "preserved", "prompt"],
        ["ask", "--wait=0", "prompt"],
        ["ask", "--wait=-1", "prompt"],
        ["ask", "--wait=nan", "prompt"],
        ["ask", "--wait=inf", "prompt"],
        ["ask", "--model", "", "prompt"],
        ["ask", "--thread", "", "prompt"],
        ["ask", "--unknown-option", "prompt"],
        ["unknown-command"],
    ],
)
def test_invalid_grammar_returns_one_safe_exit_2_object(argv: list[str]) -> None:
    lifecycle = RecordingLifecycle()
    code, payload, stderr, _ = invoke(argv, lifecycle=lifecycle)

    assert code == 2
    assert payload["ok"] is False
    assert payload["error"]["type"] == "invalid_args"
    assert stderr == ""
    assert lifecycle.calls == []


@pytest.mark.parametrize("stdin", ["", " \n\t"])
def test_empty_prompt_returns_empty_prompt_and_exit_2(stdin: str) -> None:
    code, payload, stderr, lifecycle = invoke(["ask"], stdin=stdin)

    assert code == 2
    assert payload["error"]["type"] == "empty_prompt"
    assert stderr == ""
    assert lifecycle.calls == []


@pytest.mark.parametrize(
    ("argv", "operation", "expected_request"),
    [
        (
            ["session", "current", "--thread", "preserved"],
            "current",
            CurrentSessionRequest(thread="preserved"),
        ),
        (
            ["session", "status", "https://chatgpt.com/c/abc", "--retain"],
            "observe",
            ObservationRequest(
                session=SessionAddress.parse("abc"),
                mode=ObservationMode.STATUS,
                retain=True,
            ),
        ),
        (
            ["session", "result", "abc"],
            "observe",
            ObservationRequest(
                session=SessionAddress.parse("abc"),
                mode=ObservationMode.RESULT_ONCE,
            ),
        ),
        (
            ["session", "result", "abc", "--wait"],
            "observe",
            ObservationRequest(
                session=SessionAddress.parse("abc"),
                mode=ObservationMode.RESULT_WAIT,
                wait_timeout_seconds=2700.0,
            ),
        ),
        (
            ["session", "handoff", "abc"],
            "handoff",
            HandoffRequest(session=SessionAddress.parse("abc")),
        ),
        (
            ["session", "recent"],
            "recent",
            RecentSessionsRequest(),
        ),
        (
            ["session", "recent", "--thread", "discovery"],
            "recent",
            RecentSessionsRequest(thread="discovery"),
        ),
        (
            ["abandon", "abc"],
            "abandon",
            AbandonRequest(session=SessionAddress.parse("abc")),
        ),
        (
            ["abandon", "--thread", "login"],
            "abandon",
            AbandonRequest(thread="login"),
        ),
        (["login"], "login", LoginRequest()),
    ],
)
def test_session_abandon_and_login_grammar_dispatch_through_one_lifecycle_seam(
    argv: list[str],
    operation: str,
    expected_request: object,
) -> None:
    _, _, _, lifecycle = invoke(argv)

    actual_operation, actual_request = lifecycle.calls[0]
    assert actual_operation == operation
    assert actual_request == expected_request


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["session"],
        ["session", "current"],
        ["session", "status", ""],
        ["abandon"],
        ["abandon", "abc", "--thread", "preserved"],
    ],
)
def test_missing_empty_or_ambiguous_required_input_exits_2(argv: list[str]) -> None:
    code, payload, _, lifecycle = invoke(argv)

    assert code == 2
    assert payload["error"]["type"] == "invalid_args"
    assert lifecycle.calls == []


@pytest.mark.parametrize(
    "reference",
    [
        "abc.def",
        "http://chatgpt.com/c/abc",
        "https://www.chatgpt.com/c/abc",
        "https://chatgpt.com/c/abc?model=pro",
    ],
)
def test_malformed_session_input_fails_before_lifecycle_work(reference: str) -> None:
    lifecycle = RecordingLifecycle()
    code, payload, _, _ = invoke(
        ["session", "status", reference],
        lifecycle=lifecycle,
    )

    assert code == 2
    assert payload["error"]["type"] == "invalid_args"
    assert lifecycle.calls == []


def test_parser_errors_do_not_echo_command_argument_content() -> None:
    _, payload, stderr, _ = invoke(["not-a-command", "private-prompt-content"])

    rendered = json.dumps(payload)
    assert "not-a-command" not in rendered
    assert "private-prompt-content" not in rendered
    assert stderr == ""


def test_operational_and_unexpected_failures_are_safe_single_objects() -> None:
    public_failure = RaisingLifecycle(PublicError(PublicErrorType.BROWSER_UNAVAILABLE))
    code, payload, stderr, _ = invoke(["login"], lifecycle=public_failure)
    assert code == 1
    assert payload["error"]["type"] == "browser_unavailable"
    assert stderr == ""

    unexpected = RaisingLifecycle(RuntimeError("raw secret browser diagnostic"))
    code, payload, stderr, _ = invoke(["login"], lifecycle=unexpected)
    assert code == 1
    assert payload["error"]["type"] == "internal_error"
    assert "raw secret browser diagnostic" not in json.dumps(payload)
    assert stderr == ""


def test_axi_default_lifecycle_fails_before_owned_page_browser_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SURF_AGENT_BACKEND", "axi")
    output = io.StringIO()
    errors = io.StringIO()

    code = cli.main(["login"], stdout=output, stderr=errors)

    assert code == 1
    assert json.loads(output.getvalue())["error"]["type"] == "unsupported_browser_capability"
    assert errors.getvalue() == ""


class FlushRecordingStream(io.StringIO):
    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self.events = events

    def flush(self) -> None:
        self.events.append("flush")
        super().flush()


@pytest.mark.parametrize("cleanup_raises", [False, True])
def test_private_cleanup_runs_only_after_json_flush_and_cannot_replace_output(
    cleanup_raises: bool,
) -> None:
    events: list[str] = []

    def cleanup() -> None:
        assert events == ["flush"]
        events.append("cleanup")
        if cleanup_raises:
            raise RuntimeError("private cleanup detail")

    outcome = CommandOutcome.success(
        {"session": {"id": "abc123"}},
        post_output_cleanup=cleanup,
    )
    output = FlushRecordingStream(events)

    code, payload, stderr, _ = invoke(
        ["login"],
        lifecycle=RecordingLifecycle(outcome=outcome),
        stdout=output,
    )

    assert code == 0
    assert payload == {"ok": True, "session": {"id": "abc123"}}
    assert stderr == ""
    assert events == ["flush", "cleanup"]


def test_lifecycle_and_cleanup_diagnostics_cannot_bypass_the_json_output_path() -> None:
    class NoisyLifecycle(RecordingLifecycle):
        def login(self, request: LoginRequest) -> CommandOutcome:
            print("private lifecycle stdout")
            print("private lifecycle stderr", file=sys.stderr)

            def noisy_cleanup() -> None:
                print("private cleanup stdout")
                print("private cleanup stderr", file=sys.stderr)

            return CommandOutcome.success(
                {"handoff": {"action": "complete_login", "thread": "login"}},
                post_output_cleanup=noisy_cleanup,
            )

    code, payload, stderr, _ = invoke(["login"], lifecycle=NoisyLifecycle())

    assert code == 0
    assert payload == {
        "ok": True,
        "handoff": {"action": "complete_login", "thread": "login"},
    }
    assert stderr == ""


@pytest.mark.parametrize(
    ("signal_number", "expected_exit"),
    [(signal.SIGINT, 130), (signal.SIGTERM, 143)],
)
def test_caught_process_signals_emit_one_interruption_object(
    signal_number: signal.Signals,
    expected_exit: int,
) -> None:
    class SignallingLifecycle(RecordingLifecycle):
        def login(self, request: LoginRequest) -> CommandOutcome:
            signal.raise_signal(signal_number)
            raise AssertionError("signal handler returned")

    code, payload, stderr, _ = invoke(["login"], lifecycle=SignallingLifecycle())

    assert code == expected_exit
    assert payload["ok"] is False
    assert payload["error"]["type"] == "interrupted"
    assert stderr == ""


def test_signal_raised_while_writing_is_still_projected_as_one_interruption_object() -> None:
    class SignallingOutput(io.StringIO):
        should_signal = True

        def write(self, value: str) -> int:
            if self.should_signal:
                self.should_signal = False
                signal.raise_signal(signal.SIGTERM)
            return super().write(value)

    output = SignallingOutput()
    code = cli.main(["login"], stdout=output, lifecycle=RecordingLifecycle())

    assert code == 143
    assert json.loads(output.getvalue())["error"]["type"] == "interrupted"
    assert output.getvalue().count("\n") == 1


def test_signal_during_flush_never_appends_a_second_json_object() -> None:
    class FlushSignallingOutput(io.StringIO):
        should_signal = True

        def flush(self) -> None:
            if self.should_signal:
                self.should_signal = False
                signal.raise_signal(signal.SIGTERM)
            super().flush()

    output = FlushSignallingOutput()
    code = cli.main(["login"], stdout=output, lifecycle=RecordingLifecycle())

    assert code == 143
    assert json.loads(output.getvalue())["error"]["type"] == "interrupted"
    assert output.getvalue().count("\n") == 1


def test_non_seekable_output_defers_signals_until_the_success_object_is_committed() -> None:
    class NonSeekableSignallingOutput(io.StringIO):
        should_signal = True

        def tell(self) -> int:
            raise io.UnsupportedOperation("not seekable")

        def flush(self) -> None:
            if self.should_signal:
                self.should_signal = False
                signal.raise_signal(signal.SIGTERM)
            super().flush()

    output = NonSeekableSignallingOutput()
    code = cli.main(["login"], stdout=output, lifecycle=RecordingLifecycle())

    assert code == 0
    assert json.loads(output.getvalue()) == {
        "ok": True,
        "session": {"id": "abc123"},
    }
    assert output.getvalue().count("\n") == 1


def test_failed_output_rollback_is_treated_as_an_already_committed_object() -> None:
    class UnrollbackableSignallingOutput(io.StringIO):
        should_signal = True

        def seek(self, offset: int, whence: int = 0) -> int:
            raise io.UnsupportedOperation("rollback unavailable")

        def flush(self) -> None:
            if self.should_signal:
                self.should_signal = False
                signal.raise_signal(signal.SIGTERM)
            super().flush()

    output = UnrollbackableSignallingOutput()
    code = cli.main(["login"], stdout=output, lifecycle=RecordingLifecycle())

    assert code == 0
    assert json.loads(output.getvalue()) == {
        "ok": True,
        "session": {"id": "abc123"},
    }
    assert output.getvalue().count("\n") == 1


def test_signal_before_output_write_emits_the_interruption_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_dumps = json.dumps
    should_signal = True

    def signal_before_serializing(*args: object, **kwargs: object) -> str:
        nonlocal should_signal
        if should_signal:
            should_signal = False
            signal.raise_signal(signal.SIGTERM)
        return real_dumps(*args, **kwargs)

    monkeypatch.setattr(cli.json, "dumps", signal_before_serializing)
    output = io.StringIO()

    code = cli.main(["login"], stdout=output, lifecycle=RecordingLifecycle())

    assert code == 143
    assert json.loads(output.getvalue())["error"]["type"] == "interrupted"
    assert output.getvalue().count("\n") == 1


def test_help_remains_human_readable_and_emits_no_json() -> None:
    output = io.StringIO()

    with contextlib.redirect_stdout(output), pytest.raises(SystemExit) as raised:
        cli.main(["--help"])

    assert raised.value.code == 0
    assert output.getvalue().startswith("usage: surf-chatgpt")
    assert not output.getvalue().lstrip().startswith("{")
