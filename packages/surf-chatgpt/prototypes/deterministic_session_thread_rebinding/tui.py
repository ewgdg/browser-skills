"""PROTOTYPE TUI for driving deterministic session-thread rebinding by hand."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, replace
from typing import Callable

from model import (
    PrototypeState,
    TransitionRejected,
    accept_and_rebind,
    exit_short_lived_caller,
    inject_destination_conflict,
    observe_session,
    remove_destination_conflict,
    restart_bridge,
    start_submission,
)

BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"
CLEAR = "\x1b[2J\x1b[H"

Action = Callable[[PrototypeState], PrototypeState]

ACTIONS: dict[str, tuple[str, Action]] = {
    "s": ("start submission", start_submission),
    "a": ("ChatGPT accepts + atomic rebind", accept_and_rebind),
    "o": ("observe caller-held session", observe_session),
    "x": ("exit short-lived CLI caller", exit_short_lived_caller),
    "b": ("restart browser bridge", restart_bridge),
    "c": ("inject destination collision", inject_destination_conflict),
    "d": ("delete injected collision", remove_destination_conflict),
}


def render(state: PrototypeState) -> None:
    if sys.stdout.isatty():
        print(CLEAR, end="")
    print(f"{BOLD}PROTOTYPE — deterministic session-thread rebinding{RESET}")
    print(f"{DIM}Exact page reuse before restart; conversation recovery after restart.{RESET}\n")
    print(f"{BOLD}Full state{RESET}")
    print(json.dumps(asdict(state), indent=2, sort_keys=True))
    print(f"\n{BOLD}Last outcome{RESET}\n{state.last_outcome}")
    shortcuts = "  ".join(f"{BOLD}[{key}]{RESET} {DIM}{label}{RESET}" for key, (label, _) in ACTIONS.items())
    print(f"\n{shortcuts}  {BOLD}[q]{RESET} {DIM}quit{RESET}")


def main() -> int:
    state = PrototypeState()
    while True:
        render(state)
        try:
            choice = input("\n> ").strip().lower()
        except EOFError:
            return 0
        if choice == "q":
            return 0
        action_entry = ACTIONS.get(choice)
        if action_entry is None:
            state = replace(state, last_outcome=f"Unknown action: {choice!r}")
            continue
        try:
            state = action_entry[1](state)
        except TransitionRejected as exc:
            state = replace(state, last_outcome=f"REJECTED — {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
