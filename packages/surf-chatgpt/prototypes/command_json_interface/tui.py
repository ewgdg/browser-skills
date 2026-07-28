#!/usr/bin/env python3
"""Interactive shell for the resumable surf-chatgpt interface prototype."""

from __future__ import annotations

import json

from model import (
    PrototypeState,
    arm_gate,
    capacity_failure,
    internal_state,
    reset,
    resolve_handoff_as_user,
    restart_bridge,
    run_abandon,
    run_ask,
    run_result,
    run_session_handoff,
    run_status,
    simulate_attempt,
    toggle_inspection_failure,
)


BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"


def render(state: PrototypeState) -> None:
    print("\033[2J\033[H", end="")
    print(f"{BOLD}PROTOTYPE — resumable surf-chatgpt command/JSON interface{RESET}")
    print(
        f"{DIM}Question: does one submit command plus session observation make the "
        f"lifecycle obvious without leaking page-management internals?{RESET}\n"
    )

    print(f"{BOLD}Internal prototype state (not public JSON){RESET}")
    print(json.dumps(internal_state(state), indent=2, sort_keys=True))

    print(f"\n{BOLD}Last command{RESET}  {state.last_command}")
    print(f"{BOLD}Exit code{RESET}     {state.last_exit_code}")
    print(f"{BOLD}Public JSON{RESET}")
    print(json.dumps(state.last_response, indent=2, sort_keys=True))
    print(f"{DIM}{state.last_note}{RESET}\n")

    print(f"{BOLD}Commands{RESET}")
    print("[a] ask         [w] ask --wait       [f] follow-up --session")
    print("[o] status      [r] result           [t] result --wait=30")
    print("[v] result --retain                   [h] session handoff")
    print("[d] abandon     [k] capacity error")
    print(f"{BOLD}Prototype controls{RESET}")
    print("[c] complete    [x] stop             [e] fail latest attempt")
    print("[l] next ask needs login             [j] next ask needs challenge")
    print("[u] user resolved gate               [p] retry via --thread")
    print("[b] restart bridge                   [i] toggle inspection failure")
    print("[z] reset       [q] quit")


def dispatch(state: PrototypeState, key: str) -> PrototypeState:
    if key == "a":
        return run_ask(
            state,
            'surf-chatgpt ask "Explain the interface" --model latest --thinking highest',
            prompt="Explain the interface",
            model="latest",
            thinking="highest",
        )
    if key == "w":
        return run_ask(
            state,
            'surf-chatgpt ask "Explain the interface" --wait',
            prompt="Explain the interface",
            wait_outcome="completed",
        )
    if key == "f":
        if state.session is None:
            return _note(state, "Create a session before submitting a follow-up.")
        command = f'surf-chatgpt ask --session {state.session.id} "What changed?"'
        return run_ask(
            state,
            command,
            prompt="What changed?",
            session_reference=state.session.id,
        )
    if key == "o":
        if state.session is None:
            return run_status(state, "surf-chatgpt session status missing")
        return run_status(state, f"surf-chatgpt session status {state.session.id}")
    if key == "r":
        if state.session is None:
            return run_result(state, "surf-chatgpt session result missing")
        return run_result(state, f"surf-chatgpt session result {state.session.id}")
    if key == "t":
        if state.session is None:
            return run_result(
                state, "surf-chatgpt session result missing --wait=30", wait=True
            )
        return run_result(
            state,
            f"surf-chatgpt session result {state.session.id} --wait=30",
            wait=True,
        )
    if key == "v":
        if state.session is None:
            return run_result(
                state, "surf-chatgpt session result missing --retain", retain=True
            )
        return run_result(
            state,
            f"surf-chatgpt session result {state.session.id} --retain",
            retain=True,
        )
    if key == "h":
        if state.session is None:
            return run_session_handoff(state, "surf-chatgpt session handoff missing")
        return run_session_handoff(
            state, f"surf-chatgpt session handoff {state.session.id}"
        )
    if key == "d":
        if state.session is None:
            return run_abandon(state, "surf-chatgpt session abandon missing")
        return run_abandon(state, f"surf-chatgpt session abandon {state.session.id}")
    if key == "k":
        return capacity_failure(state, 'surf-chatgpt ask "One more conversation"')
    if key == "c":
        return simulate_attempt(state, "completed")
    if key == "x":
        return simulate_attempt(state, "stopped")
    if key == "e":
        return simulate_attempt(state, "failed")
    if key == "l":
        return arm_gate(state, "complete_login")
    if key == "j":
        return arm_gate(state, "complete_challenge")
    if key == "u":
        return resolve_handoff_as_user(state)
    if key == "p":
        if state.handoff is None:
            return _note(state, "No preserved pre-session handoff exists.")
        command = (
            f'surf-chatgpt ask --thread {state.handoff.thread} "Explain the interface"'
        )
        return run_ask(
            state,
            command,
            prompt="Explain the interface",
            thread=state.handoff.thread,
        )
    if key == "b":
        return restart_bridge(state)
    if key == "i":
        return toggle_inspection_failure(state)
    if key == "z":
        return reset()
    return _note(state, f"Unknown key: {key!r}")


def _note(state: PrototypeState, message: str) -> PrototypeState:
    from dataclasses import replace

    return replace(state, last_note=message)


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
