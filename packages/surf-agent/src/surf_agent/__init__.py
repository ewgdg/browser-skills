"""Agent-scoped surf wrapper."""

__all__ = ["__version__", "Snapshot", "Thread", "run_cli"]
__version__ = "0.1.0"


def __getattr__(name: str):
    if name in {"Snapshot", "Thread"}:
        from .thread import Snapshot, Thread

        return {"Snapshot": Snapshot, "Thread": Thread}[name]
    raise AttributeError(name)


def run_cli(argv: list[str] | None = None) -> int:
    from .cli import main

    return main(argv)
