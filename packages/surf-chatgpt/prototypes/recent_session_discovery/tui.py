#!/usr/bin/env python3
"""Interactive prototype for recent-session discovery and fallback recovery."""

from __future__ import annotations

import json

from model import (
    internal_state,
    list_recent,
    lose_submission_metadata,
    reset,
    resolve_gate_as_user,
    select_candidate,
    set_mode,
)


BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"


def render(state) -> None:
    print("\033[2J\033[H", end="")
    print(f"{BOLD}PROTOTYPE — recent-session discovery and fallback recovery{RESET}")
    print(
        f"{DIM}Question: should discovery return ten ordered candidates and require "
        f"an explicit ID choice instead of guessing which interrupted submission to recover?{RESET}\n"
    )
    print(f"{BOLD}Internal prototype state (not public JSON){RESET}")
    print(json.dumps(internal_state(state), indent=2, sort_keys=True))
    print(f"\n{BOLD}Last command{RESET}  {state.last_command}")
    print(f"{BOLD}Exit code{RESET}     {state.last_exit_code}")
    print(f"{BOLD}Public JSON{RESET}")
    print(json.dumps(state.last_response, indent=2, ensure_ascii=False))
    print(f"{DIM}{state.last_note}{RESET}\n")
    print(f"{BOLD}Main path{RESET}")
    print("[d] lose submission metadata + add its conversation to Chats")
    print("[l] session recent      [1] recover candidate 1      [2] recover candidate 2")
    print(f"{BOLD}Pressure cases{RESET}")
    print("[e] empty history + list        [u] ambiguous UI + list")
    print("[g] login gate + list           [h] user resolves gate + retry")
    print("[z] reset                       [q] quit")


def dispatch(state, key: str):
    if key == "d":
        return lose_submission_metadata(state)
    if key == "l":
        return list_recent(set_mode(state, "ready"))
    if key == "1":
        return select_candidate(state, 1)
    if key == "2":
        return select_candidate(state, 2)
    if key == "e":
        return list_recent(set_mode(state, "empty"))
    if key == "u":
        return list_recent(set_mode(state, "ui_changed"))
    if key == "g":
        return list_recent(set_mode(state, "login_required"))
    if key == "h":
        return list_recent(resolve_gate_as_user(state), reuse_handoff=True)
    if key == "z":
        return reset()
    return state


def main() -> int:
    state = reset()
    while True:
        render(state)
        try:
            key = input("\nAction: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if key == "q":
            return 0
        state = dispatch(state, key)


if __name__ == "__main__":
    raise SystemExit(main())
