"""PROTOTYPE: pure state model for the resumable surf-chatgpt CLI interface."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Literal


AttemptState = Literal["generating", "completed", "stopped", "failed"]
GateAction = Literal["complete_login", "complete_challenge"]
JsonObject = dict[str, Any]


@dataclass(frozen=True)
class Attempt:
    state: AttemptState
    text: str | None = None


@dataclass(frozen=True)
class Session:
    id: str
    url: str
    thread: str
    attempt: Attempt
    page_token: str | None
    explicitly_retained: bool = False


@dataclass(frozen=True)
class PreSessionHandoff:
    action: GateAction
    thread: str
    page_token: str
    resolved_by_user: bool = False


@dataclass(frozen=True)
class PrototypeState:
    bridge_generation: int = 1
    next_session_number: int = 1
    next_page_number: int = 1
    session: Session | None = None
    handoff: PreSessionHandoff | None = None
    next_gate: GateAction | None = None
    inspection_failure: bool = False
    last_command: str = "—"
    last_exit_code: int = 0
    last_response: JsonObject = field(
        default_factory=lambda: {
            "prototype": "Choose an action to exercise the proposed interface."
        }
    )
    last_note: str = "No command has run yet."


def public_session(session: Session) -> JsonObject:
    return {"id": session.id}


def internal_state(state: PrototypeState) -> JsonObject:
    session = state.session
    handoff = state.handoff
    return {
        "bridge_generation": state.bridge_generation,
        "session": (
            {
                "id": session.id,
                "thread": session.thread,
                "attempt": session.attempt.state,
                "page_token": session.page_token,
                "explicitly_retained": session.explicitly_retained,
            }
            if session
            else None
        ),
        "pre_session_handoff": (
            {
                "action": handoff.action,
                "thread": handoff.thread,
                "page_token": handoff.page_token,
                "resolved_by_user": handoff.resolved_by_user,
            }
            if handoff
            else None
        ),
        "next_gate": state.next_gate,
        "inspection_failure": state.inspection_failure,
    }


def run_ask(
    state: PrototypeState,
    command: str,
    *,
    prompt: str,
    session_reference: str | None = None,
    thread: str | None = None,
    wait_outcome: Literal["completed", "timed_out"] | None = None,
    retain: bool = False,
    model: str | None = None,
    thinking: str | None = None,
) -> PrototypeState:
    if not prompt.strip():
        return _failure(
            state,
            command,
            "empty_prompt",
            "Prompt is empty.",
            exit_code=2,
        )
    if session_reference and thread:
        return _failure(
            state,
            command,
            "invalid_args",
            "--session and --thread are mutually exclusive.",
            exit_code=2,
        )

    working = state
    if thread:
        handoff = state.handoff
        if handoff is None or handoff.thread != thread:
            return _failure(
                state,
                command,
                "submission_failed",
                "The Surf thread is not a preserved pre-session handoff.",
            )
        if not handoff.resolved_by_user:
            return _human_gate_failure(state, command, handoff)
    elif session_reference:
        working = _resolve_session(state, session_reference, command)
        if _failed_while_resolving(working, command):
            return working
    elif state.next_gate:
        page_token, with_page = _allocate_page_token(state)
        handoff = PreSessionHandoff(
            action=state.next_gate,
            thread=f"surf-chatgpt-handoff-{state.next_page_number:03d}",
            page_token=page_token,
        )
        with_handoff = replace(with_page, handoff=handoff, next_gate=None)
        return _human_gate_failure(with_handoff, command, handoff)

    if session_reference:
        assert working.session is not None
        session = replace(
            working.session,
            attempt=Attempt("generating"),
            explicitly_retained=retain,
        )
    else:
        page_token = thread and state.handoff and state.handoff.page_token
        if not page_token:
            page_token, working = _allocate_page_token(working)
        session_id = f"abc{working.next_session_number:03d}"
        session = Session(
            id=session_id,
            url=f"https://chatgpt.com/c/{session_id}",
            thread=f"surf-chatgpt-session-{session_id}",
            attempt=Attempt("generating"),
            page_token=page_token,
            explicitly_retained=retain,
        )
        working = replace(
            working,
            next_session_number=working.next_session_number + 1,
            handoff=None,
        )

    response: JsonObject = {"ok": True, "session": public_session(session)}
    selection = {
        key: value
        for key, value in {
            "model": _selected_model(model),
            "thinking": _selected_thinking(thinking),
        }.items()
        if value is not None
    }
    if selection:
        response["selection"] = selection

    note = "Submission returned after durable session assignment and thread rebinding."
    if wait_outcome == "completed":
        session = replace(
            session,
            attempt=Attempt(
                "completed", "Prototype answer returned after observation."
            ),
        )
        response.update(_terminal_result(session.attempt))
        session = _cleanup_terminal_page(session, retain=retain)
        note = "--wait observed completion and returned the result contract."
    elif wait_outcome == "timed_out":
        response.update(
            {
                "attempt": {"state": "generating"},
                "observation": {"outcome": "timed_out"},
                "result": None,
            }
        )
        note = "--wait timed out successfully; generation and page ownership continue."

    return _success(replace(working, session=session), command, response, note)


def run_status(
    state: PrototypeState, command: str, *, retain: bool = False
) -> PrototypeState:
    working = _require_and_resolve_session(state, command)
    if _failed_while_resolving(working, command):
        return working
    assert working.session is not None
    if working.inspection_failure:
        return _inspection_failure(working, command)

    session = replace(working.session, explicitly_retained=retain)
    response = {
        "ok": True,
        "session": public_session(session),
        "attempt": {"state": session.attempt.state},
    }
    observed_session = _cleanup_terminal_page(session, retain=retain)
    return _success(
        replace(working, session=observed_session),
        command,
        response,
        "Status reports only affirmatively observed state; terminal cleanup follows capture.",
    )


def run_result(
    state: PrototypeState,
    command: str,
    *,
    wait: bool = False,
    retain: bool = False,
) -> PrototypeState:
    working = _require_and_resolve_session(state, command)
    if _failed_while_resolving(working, command):
        return working
    assert working.session is not None
    if working.inspection_failure:
        return _inspection_failure(working, command)

    session = replace(working.session, explicitly_retained=retain)
    response: JsonObject = {
        "ok": True,
        "session": public_session(session),
        "attempt": {"state": session.attempt.state},
    }
    if session.attempt.state == "generating":
        response["observation"] = {"outcome": "timed_out" if wait else "not_ready"}
        response["result"] = None
        note = (
            "The observation deadline expired; this is a successful, repeatable outcome."
            if wait
            else "The result is not ready; no state was consumed."
        )
    else:
        response.update(_terminal_result(session.attempt))
        session = _cleanup_terminal_page(session, retain=retain)
        note = "Terminal result captured; default cleanup occurs after capture."

    return _success(replace(working, session=session), command, response, note)


def run_session_handoff(state: PrototypeState, command: str) -> PrototypeState:
    working = _require_and_resolve_session(state, command)
    if _failed_while_resolving(working, command):
        return working
    assert working.session is not None
    response = {
        "ok": True,
        "session": public_session(working.session),
        "handoff": {
            "action": "inspect_browser",
            "thread": working.session.thread,
        },
    }
    return _success(
        working,
        command,
        response,
        "No focus action ran. The agent may show the thread; only the user may run Surf focus.",
    )


def run_abandon(state: PrototypeState, command: str) -> PrototypeState:
    working = _require_and_resolve_session(state, command)
    if _failed_while_resolving(working, command):
        return working
    assert working.session is not None
    if working.inspection_failure:
        return _failure(
            working,
            command,
            "abandonment_failed",
            "Could not affirm that stopping and closing the page is safe.",
            session=working.session,
            hint="The page remains preserved; inspect it manually before retrying.",
        )

    attempt = working.session.attempt
    if attempt.state == "generating":
        attempt = Attempt("stopped", "Visible partial response at abandonment.")
    session = replace(
        working.session,
        attempt=attempt,
        page_token=None,
        explicitly_retained=False,
    )
    response = {
        "ok": True,
        "session": public_session(session),
        "attempt": {"state": attempt.state},
    }
    return _success(
        replace(working, session=session),
        command,
        response,
        "Explicit abandonment affirmed any stop before releasing the retained page.",
    )


def simulate_attempt(
    state: PrototypeState, attempt_state: AttemptState
) -> PrototypeState:
    if state.session is None:
        return replace(state, last_note="Create a session before changing its attempt.")
    text = {
        "completed": "Prototype completed answer.",
        "stopped": "Prototype partial answer.",
    }.get(attempt_state)
    return replace(
        state,
        session=replace(state.session, attempt=Attempt(attempt_state, text)),
        last_note=f"PROTOTYPE CONTROL: latest attempt is now {attempt_state}.",
    )


def arm_gate(state: PrototypeState, action: GateAction) -> PrototypeState:
    return replace(
        state,
        next_gate=action,
        last_note=f"PROTOTYPE CONTROL: next new ask will require {action}.",
    )


def resolve_handoff_as_user(state: PrototypeState) -> PrototypeState:
    if state.handoff is None:
        return replace(state, last_note="No pre-session handoff is waiting.")
    return replace(
        state,
        handoff=replace(state.handoff, resolved_by_user=True),
        last_note=(
            "PROTOTYPE CONTROL: the user completed the gate manually. "
            "The agent still must not resend automatically."
        ),
    )


def restart_bridge(state: PrototypeState) -> PrototypeState:
    session = state.session
    if session:
        session = replace(session, page_token=None, explicitly_retained=False)
    return replace(
        state,
        bridge_generation=state.bridge_generation + 1,
        session=session,
        handoff=None,
        last_note=(
            "PROTOTYPE CONTROL: exact page identity ended; the durable session remains."
        ),
    )


def toggle_inspection_failure(state: PrototypeState) -> PrototypeState:
    enabled = not state.inspection_failure
    return replace(
        state,
        inspection_failure=enabled,
        last_note=f"PROTOTYPE CONTROL: inspection failure is {'on' if enabled else 'off'}.",
    )


def capacity_failure(state: PrototypeState, command: str) -> PrototypeState:
    retained: list[JsonObject] = []
    if state.session:
        retained.append(
            {
                "session": state.session.id,
                "thread": state.session.thread,
                "reason": state.session.attempt.state,
            }
        )
    response = {
        "ok": False,
        "error": {
            "type": "capacity_exceeded",
            "message": "The browser bridge already owns 10 protected surf-chatgpt pages.",
            "hint": "Explicitly abandon one listed session before submitting another.",
        },
        "capacity": {"limit": 10, "retained": retained},
    }
    return _record(
        state,
        command,
        response,
        exit_code=1,
        note="Capacity failure reports only the retained identifiers needed for action.",
    )


def reset() -> PrototypeState:
    return PrototypeState()


def _require_and_resolve_session(state: PrototypeState, command: str) -> PrototypeState:
    if state.session is None:
        return _failure(
            state,
            command,
            "session_not_found",
            "No prototype ChatGPT session exists.",
        )
    return _resolve_session(state, state.session.id, command)


def _resolve_session(
    state: PrototypeState, reference: str, command: str
) -> PrototypeState:
    session = state.session
    if session is None or reference not in {session.id, session.url}:
        return _failure(
            state,
            command,
            "session_not_found",
            f"ChatGPT session {reference!r} was not found.",
        )
    if session.page_token is not None:
        return state
    page_token, working = _allocate_page_token(state)
    return replace(working, session=replace(session, page_token=page_token))


def _allocate_page_token(state: PrototypeState) -> tuple[str, PrototypeState]:
    token = f"page-b{state.bridge_generation}-{state.next_page_number:03d}"
    return token, replace(state, next_page_number=state.next_page_number + 1)


def _failed_while_resolving(state: PrototypeState, command: str) -> bool:
    return state.last_command == command and state.last_response.get("ok") is False


def _terminal_result(attempt: Attempt) -> JsonObject:
    if attempt.state == "completed":
        result: JsonObject | None = {"text": attempt.text or "", "partial": False}
    elif attempt.state == "stopped":
        result = {"text": attempt.text or "", "partial": True}
    else:
        result = None
    return {"attempt": {"state": attempt.state}, "result": result}


def _cleanup_terminal_page(session: Session, *, retain: bool) -> Session:
    if session.attempt.state == "generating" or retain:
        return session
    return replace(session, page_token=None, explicitly_retained=False)


def _selected_model(query: str | None) -> str | None:
    if query is None:
        return None
    return "GPT-5.6" if query == "latest" else query


def _selected_thinking(query: str | None) -> str | None:
    if query is None:
        return None
    return "Pro" if query == "highest" else query


def _human_gate_failure(
    state: PrototypeState, command: str, handoff: PreSessionHandoff
) -> PrototypeState:
    action_label = handoff.action.replace("_", " ")
    response = {
        "ok": False,
        "error": {
            "type": "human_intervention_required",
            "message": f"The user must {action_label} in the preserved page.",
        },
        "handoff": {"action": handoff.action, "thread": handoff.thread},
    }
    return _record(
        state,
        command,
        response,
        exit_code=1,
        note="The prompt was not sent. No focus command ran and no retry is automatic.",
    )


def _inspection_failure(state: PrototypeState, command: str) -> PrototypeState:
    assert state.session is not None
    return _failure(
        state,
        command,
        "inspection_failed",
        "Could not affirm the latest response state.",
        session=state.session,
        hint="The session remains recoverable; retry status or hand it to the user.",
        handoff={"action": "inspect_browser", "thread": state.session.thread},
    )


def _failure(
    state: PrototypeState,
    command: str,
    error_type: str,
    message: str,
    *,
    exit_code: int = 1,
    session: Session | None = None,
    hint: str | None = None,
    handoff: JsonObject | None = None,
) -> PrototypeState:
    error: JsonObject = {"type": error_type, "message": message}
    if hint:
        error["hint"] = hint
    response: JsonObject = {"ok": False, "error": error}
    if session:
        response["session"] = public_session(session)
    if handoff:
        response["handoff"] = handoff
    return _record(
        state,
        command,
        response,
        exit_code=exit_code,
        note="The command failed without discarding recoverable identity.",
    )


def _success(
    state: PrototypeState,
    command: str,
    response: JsonObject,
    note: str,
) -> PrototypeState:
    return _record(state, command, response, exit_code=0, note=note)


def _record(
    state: PrototypeState,
    command: str,
    response: JsonObject,
    *,
    exit_code: int,
    note: str,
) -> PrototypeState:
    return replace(
        state,
        last_command=command,
        last_exit_code=exit_code,
        last_response=response,
        last_note=note,
    )
