"""Agent-scoped surf wrapper."""

__all__ = ["__version__", "run_cli"]
__version__ = "0.1.0"


def run_cli(argv: list[str] | None = None) -> int:
    from .cli import main

    return main(argv)
