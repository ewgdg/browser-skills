from __future__ import annotations


class SurfAgentError(RuntimeError):
    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class BridgeUnavailable(SurfAgentError):
    pass


class BridgeToolError(SurfAgentError):
    def __init__(self, *, backend_label: str, tool_name: str, detail: str) -> None:
        self.detail = detail
        super().__init__(f"{backend_label} bridge tool {tool_name} failed: {detail}")
