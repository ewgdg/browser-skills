from __future__ import annotations

from typing import Protocol

from surf_agent.owned_pages import (
    OwnedPageBridge,
    OwnedPageInspectionState,
    create_owned_page_bridge,
)

from .contracts import (
    AbandonRequest,
    AskRequest,
    CommandOutcome,
    CurrentSessionRequest,
    HandoffRequest,
    LoginRequest,
    ObservationOutcome,
    ObservationRequest,
    RecentSessionsRequest,
)
from .errors import PublicError, PublicErrorType
from .session_address import InvalidSessionAddress, SessionAddress
from .surf_pages import LOGIN_THREAD, ChatGptOwnedPages


class SessionLifecycle(Protocol):
    def ask(self, request: AskRequest) -> CommandOutcome: ...

    def observe(self, request: ObservationRequest) -> CommandOutcome: ...

    def current(self, request: CurrentSessionRequest) -> CommandOutcome: ...

    def handoff(self, request: HandoffRequest) -> CommandOutcome: ...

    def abandon(self, request: AbandonRequest) -> CommandOutcome: ...

    def recent(self, request: RecentSessionsRequest) -> CommandOutcome: ...

    def login(self, request: LoginRequest) -> CommandOutcome: ...


class OwnedPageSessionLifecycle:
    def __init__(self, bridge: OwnedPageBridge) -> None:
        self._pages = ChatGptOwnedPages(bridge)

    def login(self, request: LoginRequest) -> CommandOutcome:
        _ = request
        self._pages.prepare_login()
        return CommandOutcome.success(
            {
                "handoff": {
                    "action": "complete_login",
                    "thread": LOGIN_THREAD,
                }
            }
        )

    # Downstream tickets implement these lifecycle operations. Keeping them on
    # this one seam prevents issue 14 from introducing a parallel partial API.
    def ask(self, request: AskRequest) -> CommandOutcome:
        return self._pending_operation(request)

    def observe(self, request: ObservationRequest) -> CommandOutcome:
        return self._pending_operation(request)

    def current(self, request: CurrentSessionRequest) -> CommandOutcome:
        inspection = self._pages.inspect_thread(request.thread)
        if inspection.state in {
            OwnedPageInspectionState.PRE_SESSION,
            OwnedPageInspectionState.HUMAN_GATE,
        }:
            return CommandOutcome.success(
                {
                    "session": None,
                    "observation": {"outcome": ObservationOutcome.NOT_READY.value},
                }
            )
        if inspection.state is not OwnedPageInspectionState.SESSION:
            raise PublicError(PublicErrorType.INSPECTION_FAILED)
        try:
            session = SessionAddress.parse(inspection.page.exact_url)
        except InvalidSessionAddress as error:
            raise PublicError(PublicErrorType.INSPECTION_FAILED) from error
        return CommandOutcome.success({"session": session.to_public_json()})

    def handoff(self, request: HandoffRequest) -> CommandOutcome:
        return self._pending_operation(request)

    def abandon(self, request: AbandonRequest) -> CommandOutcome:
        return self._pending_operation(request)

    def recent(self, request: RecentSessionsRequest) -> CommandOutcome:
        return self._pending_operation(request)

    def _pending_operation(self, request: object) -> CommandOutcome:
        _ = request
        raise PublicError(PublicErrorType.UNSUPPORTED_BROWSER_CAPABILITY)


def create_session_lifecycle() -> SessionLifecycle:
    return OwnedPageSessionLifecycle(create_owned_page_bridge())
