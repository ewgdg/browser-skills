"""Agent-scoped surf wrapper."""

__all__ = ["__version__", "Browser", "Snapshot", "Thread", "SurfAgentError"]
__version__ = "0.1.0"


def __getattr__(name: str):
    if name == "Browser":
        from .browser import Browser
        return Browser
    if name == "SurfAgentError":
        from .errors import SurfAgentError
        return SurfAgentError
    if name in {"Snapshot", "Thread"}:
        from .thread import Snapshot, Thread

        return {"Snapshot": Snapshot, "Thread": Thread}[name]
    raise AttributeError(name)
