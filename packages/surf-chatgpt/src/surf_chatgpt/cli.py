from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import signal
import sys
import threading
from collections.abc import Iterable, Iterator
from types import FrameType
from typing import IO, Any, NoReturn

from .commands import execute_command
from .contracts import (
    CommandOutcome,
    DEFAULT_OBSERVATION_TIMEOUT_SECONDS,
    ProcessExitCode,
    exit_code_for_error_type,
)
from .errors import PublicError, PublicErrorType
from .session_address import InvalidSessionAddress, SessionAddress
from .session_lifecycle import SessionLifecycle, create_session_lifecycle


_SIGNAL_EXIT_CODES = {
    signal.SIGINT: ProcessExitCode.INTERRUPTED,
    signal.SIGTERM: ProcessExitCode.TERMINATED,
}


class JsonArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)

    def error(self, message: str) -> NoReturn:
        # argparse messages can echo command arguments. The public JSON path uses
        # a fixed content-free error while --help remains human-readable.
        raise PublicError(PublicErrorType.INVALID_ARGS)

    def parse_args(
        self,
        args: Iterable[str] | None = None,
        namespace: argparse.Namespace | None = None,
    ) -> argparse.Namespace:
        source = list(sys.argv[1:] if args is None else args)
        parsed = super().parse_args(_expand_bare_wait(source), namespace)
        if parsed is None:
            raise RuntimeError("argparse returned no namespace")
        return parsed


class _CaughtSignal(BaseException):
    def __init__(self, signal_number: int) -> None:
        self.signal_number = signal.Signals(signal_number)
        self.output_committed = False
        super().__init__(signal_number)


class _DiscardingTextStream(io.TextIOBase):
    def write(self, value: str) -> int:
        return len(value)


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(
        prog="surf-chatgpt",
        description="Submit and resume ChatGPT sessions through the dedicated Surf browser.",
    )
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        parser_class=JsonArgumentParser,
    )

    ask = subparsers.add_parser("ask", help="Submit one prompt and return its durable session.")
    ask_address = ask.add_mutually_exclusive_group()
    ask_address.add_argument("--session", type=_session_address, metavar="ID_OR_URL")
    ask_address.add_argument("--thread", metavar="SURF_THREAD")
    ask.add_argument("--model", metavar="QUERY")
    ask.add_argument("--thinking", metavar="QUERY")
    _add_wait_argument(ask)
    ask.add_argument("--retain", action="store_true")
    ask.add_argument("--pace", choices=("natural", "none"), default="natural")
    ask.add_argument("--allow-logged-out", action="store_true")
    ask.add_argument("prompt", nargs="?", metavar="PROMPT")

    session = subparsers.add_parser("session", help="Inspect or recover a durable session.")
    session_subparsers = session.add_subparsers(
        dest="session_command",
        required=True,
        parser_class=JsonArgumentParser,
    )

    current = session_subparsers.add_parser("current", help="Inspect one exact preserved thread.")
    current.add_argument("--thread", required=True, metavar="SURF_THREAD")

    status = session_subparsers.add_parser("status", help="Classify the latest response attempt.")
    status.add_argument("session", type=_session_address, metavar="SESSION")
    status.add_argument("--retain", action="store_true")

    result = session_subparsers.add_parser("result", help="Read or wait for the latest result.")
    result.add_argument("session", type=_session_address, metavar="SESSION")
    _add_wait_argument(result)
    result.add_argument("--retain", action="store_true")

    handoff = session_subparsers.add_parser("handoff", help="Preserve a session for manual inspection.")
    handoff.add_argument("session", type=_session_address, metavar="SESSION")

    recent = session_subparsers.add_parser("recent", help="List rendered recent ChatGPT sessions.")
    recent.add_argument("--thread", metavar="SURF_THREAD")

    abandon = subparsers.add_parser("abandon", help="Stop if needed and close one ChatGPT page.")
    abandon.add_argument("session", nargs="?", type=_session_address, metavar="SESSION")
    abandon.add_argument("--thread", metavar="SURF_THREAD")

    subparsers.add_parser("login", help="Prepare an unfocused page for manual ChatGPT login.")
    return parser


def main(
    argv: list[str] | None = None,
    *,
    stdin: IO[str] | None = None,
    stdout: IO[str] | None = None,
    stderr: IO[str] | None = None,
    lifecycle: SessionLifecycle | None = None,
) -> int:
    input_stream = sys.stdin if stdin is None else stdin
    output_stream = sys.stdout if stdout is None else stdout
    # Non-help invocations intentionally never write diagnostics to stderr.
    _ = stderr
    active_lifecycle = lifecycle

    try:
        with _catch_process_signals():
            try:
                args = build_parser().parse_args(argv)
                if active_lifecycle is None:
                    active_lifecycle = create_session_lifecycle()
                with _discard_python_diagnostics():
                    outcome = execute_command(args, input_stream, active_lifecycle)
            except _CaughtSignal:
                raise
            except PublicError as error:
                outcome = _error_outcome(error)
            except Exception:
                outcome = CommandOutcome.failure(PublicError(PublicErrorType.INTERNAL_ERROR))

            if not _emit_and_flush(outcome, output_stream):
                return ProcessExitCode.OPERATIONAL_FAILURE
    except _CaughtSignal as caught:
        if caught.output_committed:
            return outcome.exit_code
        outcome = _interruption_outcome(caught.signal_number, active_lifecycle)
        if not _emit_and_flush(outcome, output_stream):
            return ProcessExitCode.OPERATIONAL_FAILURE
        return outcome.exit_code
    except KeyboardInterrupt:
        outcome = _interruption_outcome(signal.SIGINT, active_lifecycle)
        if not _emit_and_flush(outcome, output_stream):
            return ProcessExitCode.OPERATIONAL_FAILURE
        return outcome.exit_code

    with _discard_python_diagnostics():
        _run_cleanup(outcome)
    return outcome.exit_code


def _add_wait_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--wait",
        nargs="?",
        type=_positive_seconds,
        const=DEFAULT_OBSERVATION_TIMEOUT_SECONDS,
        metavar="SECONDS",
    )


def _expand_bare_wait(argv: list[str]) -> list[str]:
    expanded: list[str] = []
    options_ended = False
    for argument in argv:
        if argument == "--":
            options_ended = True
        if argument == "--wait" and not options_ended:
            expanded.append(f"--wait={DEFAULT_OBSERVATION_TIMEOUT_SECONDS:g}")
        else:
            expanded.append(argument)
    return expanded


def _positive_seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("wait must be a positive number") from error
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("wait must be a positive number")
    return seconds


def _session_address(value: str) -> SessionAddress:
    try:
        return SessionAddress.parse(value)
    except InvalidSessionAddress as error:
        raise argparse.ArgumentTypeError("invalid session address") from error


def _error_outcome(error: PublicError) -> CommandOutcome:
    return CommandOutcome.failure(
        error,
        exit_code=exit_code_for_error_type(error.type),
    )


def _interruption_outcome(
    signal_number: signal.Signals,
    lifecycle: SessionLifecycle | None = None,
) -> CommandOutcome:
    exit_code = _SIGNAL_EXIT_CODES[signal_number]
    if lifecycle is not None:
        return lifecycle.interruption_outcome(exit_code)
    return CommandOutcome.failure(
        PublicError(PublicErrorType.INTERRUPTED),
        exit_code=exit_code,
    )


def _emit_and_flush(outcome: CommandOutcome, stdout: IO[str]) -> bool:
    checkpoint = _output_checkpoint(stdout)
    output_started = False
    output_committed = False
    try:
        serialized = json.dumps(
            outcome.to_public_json(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        output_guard = (
            _defer_process_signals()
            if checkpoint is None
            else contextlib.nullcontext()
        )
        with output_guard:
            output_started = True
            stdout.write(f"{serialized}\n")
            stdout.flush()
            output_committed = True
    except _CaughtSignal as caught:
        if output_committed:
            caught.output_committed = True
        elif output_started:
            caught.output_committed = not _rollback_output(stdout, checkpoint)
        raise
    except Exception:
        return False
    return True


def _output_checkpoint(stdout: IO[str]) -> int | None:
    try:
        return stdout.tell()
    except (AttributeError, OSError):
        return None


def _rollback_output(stdout: IO[str], checkpoint: int | None) -> bool:
    if checkpoint is None:
        return False
    try:
        stdout.seek(checkpoint)
        stdout.truncate()
    except (AttributeError, OSError):
        return False
    return True


@contextlib.contextmanager
def _defer_process_signals() -> Iterator[None]:
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    deferred_signals = {signal.SIGINT, signal.SIGTERM}
    previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, deferred_signals)
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)


def _run_cleanup(outcome: CommandOutcome) -> None:
    cleanup = outcome.post_output_cleanup
    if cleanup is None:
        return
    try:
        cleanup()
    except Exception:
        pass


@contextlib.contextmanager
def _discard_python_diagnostics() -> Iterator[None]:
    with (
        contextlib.redirect_stdout(_DiscardingTextStream()),
        contextlib.redirect_stderr(_DiscardingTextStream()),
    ):
        yield


@contextlib.contextmanager
def _catch_process_signals() -> Iterator[None]:
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    caught_signals = (signal.SIGINT, signal.SIGTERM)
    previous_handlers = {
        signal_number: signal.getsignal(signal_number)
        for signal_number in caught_signals
    }

    def raise_caught_signal(signal_number: int, frame: FrameType | None) -> None:
        raise _CaughtSignal(signal_number)

    try:
        for signal_number in caught_signals:
            signal.signal(signal_number, raise_caught_signal)
        yield
    finally:
        for signal_number, previous_handler in previous_handlers.items():
            signal.signal(signal_number, previous_handler)


if __name__ == "__main__":
    raise SystemExit(main())
