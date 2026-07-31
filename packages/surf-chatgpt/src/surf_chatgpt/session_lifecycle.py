from __future__ import annotations

from typing import Protocol

from .browser_lifecycle import BrowserSessionLifecycle
from .browser_port import BridgeBrowserPagePort
from .contracts import (
    AbandonRequest,
    AskRequest,
    CommandOutcome,
    CurrentSessionRequest,
    HandoffRequest,
    LoginRequest,
    ObservationRequest,
    ProcessExitCode,
    RecentSessionsRequest,
)


class SessionLifecycle(Protocol):
    def ask(self, request: AskRequest) -> CommandOutcome: ...

    def interruption_outcome(
        self,
        exit_code: ProcessExitCode,
    ) -> CommandOutcome: ...

    def observe(self, request: ObservationRequest) -> CommandOutcome: ...

    def current(self, request: CurrentSessionRequest) -> CommandOutcome: ...

    def handoff(self, request: HandoffRequest) -> CommandOutcome: ...

    def abandon(self, request: AbandonRequest) -> CommandOutcome: ...

    def recent(self, request: RecentSessionsRequest) -> CommandOutcome: ...

    def login(self, request: LoginRequest) -> CommandOutcome: ...


def create_session_lifecycle() -> SessionLifecycle:
    from surf_agent.cli import SurfAgent

    browser = BridgeBrowserPagePort(SurfAgent().patchright_client)
    return BrowserSessionLifecycle(browser)
