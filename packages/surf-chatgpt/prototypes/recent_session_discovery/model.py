"""PROTOTYPE: pure model for recent-session discovery and explicit recovery."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Literal


RECENT_SESSION_LIMIT = 10
JsonObject = dict[str, Any]
DiscoveryMode = Literal["ready", "empty", "ui_changed", "login_required"]


@dataclass(frozen=True)
class SessionCandidate:
    id: str
    title: str


@dataclass(frozen=True)
class ChatHistorySnapshot:
    mode: DiscoveryMode
    pinned: tuple[SessionCandidate, ...] = ()
    chats: tuple[SessionCandidate, ...] = ()


@dataclass(frozen=True)
class PrototypeState:
    snapshot: ChatHistorySnapshot
    submission_metadata_lost: bool = False
    discovered: tuple[SessionCandidate, ...] = ()
    selected_session_id: str | None = None
    preserved_handoff_thread: str | None = None
    user_resolved_gate: bool = False
    discovery_page_open: bool = False
    last_command: str = "—"
    last_exit_code: int = 0
    last_response: JsonObject = field(
        default_factory=lambda: {
            "prototype": "Lose metadata or list the current recent sessions."
        }
    )
    last_note: str = "No command has run yet."


def reset() -> PrototypeState:
    return PrototypeState(snapshot=_ready_snapshot())


def lose_submission_metadata(state: PrototypeState) -> PrototypeState:
    uncaptured = SessionCandidate("uncaptured-001", "Fallback recovery design")
    chats = (uncaptured,) + tuple(
        candidate for candidate in state.snapshot.chats if candidate.id != uncaptured.id
    )
    return replace(
        state,
        snapshot=replace(state.snapshot, mode="ready", chats=chats),
        submission_metadata_lost=True,
        discovered=(),
        selected_session_id=None,
        last_command='surf-chatgpt ask "Design fallback recovery"',
        last_exit_code=130,
        last_response={
            "ok": False,
            "error": {
                "type": "interrupted",
                "message": "The caller exited after ChatGPT accepted the prompt.",
            },
        },
        last_note=(
            "The caller has no session ID. ChatGPT still placed the conversation at "
            "the top of its Chats list."
        ),
    )


def list_recent(state: PrototypeState, *, reuse_handoff: bool = False) -> PrototypeState:
    command = "surf-chatgpt session recent"
    if reuse_handoff and state.preserved_handoff_thread:
        command += f" --thread {state.preserved_handoff_thread}"

    if state.snapshot.mode == "login_required" and not state.user_resolved_gate:
        thread = state.preserved_handoff_thread or "surf-chatgpt-discovery-001"
        return replace(
            state,
            preserved_handoff_thread=thread,
            discovery_page_open=True,
            last_command=command,
            last_exit_code=1,
            last_response={
                "ok": False,
                "error": {
                    "type": "human_intervention_required",
                    "message": "ChatGPT login is required before session discovery.",
                },
                "handoff": {
                    "action": "complete_login",
                    "thread": thread,
                    "retry": ["session", "recent", "--thread", thread],
                },
            },
            last_note=(
                "The page remains open without being focused. Only the user completes "
                "the gate; retry never runs automatically."
            ),
        )

    if state.snapshot.mode == "ui_changed":
        return replace(
            state,
            discovered=(),
            discovery_page_open=False,
            last_command=command,
            last_exit_code=1,
            last_response={
                "ok": False,
                "error": {
                    "type": "ui_changed",
                    "message": "ChatGPT's Chats list could not be identified unambiguously.",
                },
            },
            last_note=(
                "Discovery fails closed instead of mixing pinned, project, or unrelated links "
                "into the candidate list."
            ),
        )

    candidates = () if state.snapshot.mode == "empty" else discover_recent(state.snapshot)
    return replace(
        state,
        discovered=candidates,
        preserved_handoff_thread=None,
        user_resolved_gate=False,
        discovery_page_open=False,
        last_command=command,
        last_exit_code=0,
        last_response={
            "ok": True,
            "sessions": [public_candidate(candidate) for candidate in candidates],
        },
        last_note=(
            "The temporary discovery page closed after reading the first ten canonical "
            "conversation links from ChatGPT's Chats section."
        ),
    )


def discover_recent(snapshot: ChatHistorySnapshot) -> tuple[SessionCandidate, ...]:
    """Return ChatGPT's displayed Chats order; pinned entries are not candidates."""
    seen: set[str] = set()
    candidates: list[SessionCandidate] = []
    for candidate in snapshot.chats:
        if not candidate.id or candidate.id in seen:
            continue
        seen.add(candidate.id)
        candidates.append(candidate)
        if len(candidates) == RECENT_SESSION_LIMIT:
            break
    return tuple(candidates)


def select_candidate(state: PrototypeState, position: int) -> PrototypeState:
    if not state.discovered:
        return replace(state, last_note="Run session recent before choosing a session.")
    if position < 1 or position > len(state.discovered):
        return replace(state, last_note=f"Candidate {position} is not available.")
    candidate = state.discovered[position - 1]
    return replace(
        state,
        selected_session_id=candidate.id,
        last_command=f"surf-chatgpt session result {candidate.id} --wait",
        last_exit_code=0,
        last_response={
            "ok": True,
            "session": {"id": candidate.id},
            "attempt": {"state": "completed"},
            "result": {
                "text": "Recovered through the existing session result command.",
                "partial": False,
            },
        },
        last_note=(
            "The caller explicitly selected this ID. Discovery never guessed which "
            "conversation belonged to the interrupted submission."
        ),
    )


def set_mode(state: PrototypeState, mode: DiscoveryMode) -> PrototypeState:
    return replace(
        state,
        snapshot=replace(state.snapshot, mode=mode),
        discovered=(),
        selected_session_id=None,
        user_resolved_gate=False,
        last_note=f"Next discovery uses the {mode!r} scenario.",
    )


def resolve_gate_as_user(state: PrototypeState) -> PrototypeState:
    if not state.preserved_handoff_thread:
        return replace(state, last_note="No human gate is waiting.")
    return replace(
        state,
        snapshot=replace(state.snapshot, mode="ready"),
        user_resolved_gate=True,
        last_note="User marked the preserved login page ready; retry is now allowed.",
    )


def public_candidate(candidate: SessionCandidate) -> JsonObject:
    return {"id": candidate.id, "title": candidate.title}


def internal_state(state: PrototypeState) -> JsonObject:
    return {
        "submission_metadata_lost": state.submission_metadata_lost,
        "chatgpt_chats_count": len(state.snapshot.chats),
        "chatgpt_pinned_count": len(state.snapshot.pinned),
        "discovered_ids": [candidate.id for candidate in state.discovered],
        "selected_session_id": state.selected_session_id,
        "preserved_handoff_thread": state.preserved_handoff_thread,
        "user_resolved_gate": state.user_resolved_gate,
        "discovery_page_open": state.discovery_page_open,
    }


def _ready_snapshot() -> ChatHistorySnapshot:
    pinned = (SessionCandidate("pinned-999", "Pinned reference conversation"),)
    chats = tuple(
        SessionCandidate(f"recent-{index:03d}", f"Recent conversation {index}")
        for index in range(1, 13)
    )
    return ChatHistorySnapshot(mode="ready", pinned=pinned, chats=chats)
