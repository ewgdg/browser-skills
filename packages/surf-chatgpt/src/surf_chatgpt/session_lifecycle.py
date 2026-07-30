from __future__ import annotations

from typing import Protocol

from .contracts import (
    AbandonRequest,
    AskRequest,
    CommandOutcome,
    CurrentSessionRequest,
    HandoffRequest,
    LoginRequest,
    ObservationRequest,
    RecentSessionsRequest,
)
from .errors import PublicError, PublicErrorType


class SessionLifecycle(Protocol):
    def ask(self, request: AskRequest) -> CommandOutcome: ...

    def observe(self, request: ObservationRequest) -> CommandOutcome: ...

    def current(self, request: CurrentSessionRequest) -> CommandOutcome: ...

    def handoff(self, request: HandoffRequest) -> CommandOutcome: ...

    def abandon(self, request: AbandonRequest) -> CommandOutcome: ...

    def recent(self, request: RecentSessionsRequest) -> CommandOutcome: ...

    def login(self, request: LoginRequest) -> CommandOutcome: ...


def create_session_lifecycle() -> SessionLifecycle:
    # Fail closed until the owned-page lifecycle can prove the required browser
    # identity, ownership, and non-activation guarantees.
    raise PublicError(PublicErrorType.UNSUPPORTED_BROWSER_CAPABILITY)
