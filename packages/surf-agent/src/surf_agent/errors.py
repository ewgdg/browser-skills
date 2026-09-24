from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    """Stable failure categories; callers branch on these, never on message text."""

    STALE_REF = "stale_ref"
    NOT_FOUND = "not_found"
    NOT_VISIBLE = "not_visible"
    NOT_ENABLED = "not_enabled"
    NOT_EDITABLE = "not_editable"
    INTERCEPTED = "intercepted"
    ACTION_TIMEOUT = "action_timeout"
    WAIT_TIMEOUT = "wait_timeout"
    PAGE_CLOSED = "page_closed"
    BRIDGE_UNAVAILABLE = "bridge_unavailable"
    OUTCOME_UNKNOWN = "outcome_unknown"
    UNSUPPORTED = "unsupported"


class SurfAgentError(RuntimeError):
    def __init__(self, message: str, exit_code: int = 1, *, code: ErrorCode | None = None) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.code = code


class BridgeUnavailable(SurfAgentError):
    def __init__(self, message: str, *, code: ErrorCode = ErrorCode.BRIDGE_UNAVAILABLE) -> None:
        super().__init__(message, code=code)


class BridgeIdentityUnproven(SurfAgentError):
    pass


class BridgeToolError(SurfAgentError):
    def __init__(self, *, backend_label: str, tool_name: str, detail: str, code: ErrorCode | None = None) -> None:
        self.detail = detail
        super().__init__(f"{backend_label} bridge tool {tool_name} failed: {detail}", code=code)
