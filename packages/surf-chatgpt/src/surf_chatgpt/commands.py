from __future__ import annotations

import argparse
from typing import IO

from .contracts import (
    AbandonRequest,
    AskRequest,
    CommandOutcome,
    CurrentSessionRequest,
    HandoffRequest,
    LoginRequest,
    ObservationMode,
    ObservationRequest,
    Pace,
    RecentSessionsRequest,
)
from .errors import PublicError, PublicErrorType
from .session_lifecycle import SessionLifecycle, create_session_lifecycle


def execute_command(
    args: argparse.Namespace,
    stdin: IO[str],
    lifecycle: SessionLifecycle | None = None,
) -> CommandOutcome:
    if args.command == "ask":
        request = _ask_request(args, stdin)
        return _lifecycle(lifecycle).ask(request)

    if args.command == "session":
        if args.session_command == "current":
            request = CurrentSessionRequest(thread=_required_text(args.thread))
            return _lifecycle(lifecycle).current(request)
        if args.session_command == "status":
            request = ObservationRequest(
                session=args.session,
                mode=ObservationMode.STATUS,
                retain=args.retain,
            )
            return _lifecycle(lifecycle).observe(request)
        if args.session_command == "result":
            request = ObservationRequest(
                session=args.session,
                mode=(
                    ObservationMode.RESULT_WAIT
                    if args.wait is not None
                    else ObservationMode.RESULT_ONCE
                ),
                wait_timeout_seconds=args.wait,
                retain=args.retain,
            )
            return _lifecycle(lifecycle).observe(request)
        if args.session_command == "handoff":
            request = HandoffRequest(session=args.session)
            return _lifecycle(lifecycle).handoff(request)
        if args.session_command == "recent":
            request = RecentSessionsRequest(thread=_optional_text(args.thread))
            return _lifecycle(lifecycle).recent(request)

    if args.command == "abandon":
        if (args.session is None) == (args.thread is None):
            raise PublicError(PublicErrorType.INVALID_ARGS)
        request = AbandonRequest(
            session=args.session,
            thread=_optional_text(args.thread),
        )
        return _lifecycle(lifecycle).abandon(request)

    if args.command == "login":
        return _lifecycle(lifecycle).login(LoginRequest())

    raise PublicError(PublicErrorType.INVALID_ARGS)


def _ask_request(args: argparse.Namespace, stdin: IO[str]) -> AskRequest:
    prompt = args.prompt if args.prompt is not None else stdin.read()
    if not prompt.strip():
        raise PublicError(PublicErrorType.EMPTY_PROMPT)

    return AskRequest(
        prompt=prompt,
        session=args.session,
        thread=_optional_text(args.thread),
        model=_optional_text(args.model),
        thinking=_optional_text(args.thinking),
        wait_timeout_seconds=args.wait,
        retain=args.retain,
        pace=Pace(args.pace),
        allow_logged_out=args.allow_logged_out,
    )


def _lifecycle(lifecycle: SessionLifecycle | None) -> SessionLifecycle:
    return lifecycle if lifecycle is not None else create_session_lifecycle()


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    return _required_text(value)


def _required_text(value: str) -> str:
    if not value.strip():
        raise PublicError(PublicErrorType.INVALID_ARGS)
    return value
