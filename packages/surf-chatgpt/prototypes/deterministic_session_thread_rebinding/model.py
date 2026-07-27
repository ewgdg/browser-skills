"""PROTOTYPE: pure state model for deterministic Surf-thread rebinding."""

from __future__ import annotations

from dataclasses import dataclass, replace


TEMPORARY_THREAD_PREFIX = "surf-chatgpt-submit"
SESSION_THREAD_PREFIX = "surf-chatgpt-session"
CHATGPT_SESSION_URL_PREFIX = "https://chatgpt.com/c"


@dataclass(frozen=True)
class Page:
    token: str
    url: str


@dataclass(frozen=True)
class PendingSubmission:
    temporary_thread: str
    expected_session_id: str
    submitted_page_token: str


@dataclass(frozen=True)
class SessionHandle:
    session_id: str
    url: str
    submitted_page_token: str


@dataclass(frozen=True)
class PrototypeState:
    bridge_generation: int = 1
    next_submission_number: int = 1
    next_page_number: int = 1
    pages: tuple[tuple[str, Page], ...] = ()
    pending_submission: PendingSubmission | None = None
    caller_session: SessionHandle | None = None
    last_outcome: str = "Ready. Start a submission."


class TransitionRejected(ValueError):
    pass


def start_submission(state: PrototypeState) -> PrototypeState:
    if state.pending_submission is not None:
        raise TransitionRejected("Finish the pending submission first.")

    sequence = state.next_submission_number
    temporary_thread = f"{TEMPORARY_THREAD_PREFIX}-{sequence:03d}"
    expected_session_id = f"abc{sequence:03d}"
    page, state_with_page_number = _new_page(state, "https://chatgpt.com/")
    pages = _pages(state_with_page_number)
    pages[temporary_thread] = page
    pending = PendingSubmission(temporary_thread, expected_session_id, page.token)
    return replace(
        state_with_page_number,
        next_submission_number=sequence + 1,
        pages=_freeze_pages(pages),
        pending_submission=pending,
        caller_session=None,
        last_outcome=f"Prompt submitted on {temporary_thread}; waiting for ChatGPT to assign /c/<id>.",
    )


def accept_and_rebind(state: PrototypeState) -> PrototypeState:
    pending = _require_pending(state)
    session_url = session_url_for(pending.expected_session_id)
    pages = _pages(state)
    submitted_page = pages.get(pending.temporary_thread)
    if submitted_page is None:
        raise TransitionRejected("The temporary submitted page no longer exists.")

    # ChatGPT's navigation to /c/<id> happens before the submission handshake returns.
    pages[pending.temporary_thread] = replace(submitted_page, url=session_url)
    navigated_state = replace(state, pages=_freeze_pages(pages))
    handle = SessionHandle(pending.expected_session_id, session_url, pending.submitted_page_token)
    try:
        rebound_state, rebind_outcome = rebind_thread(
            navigated_state,
            source_thread=pending.temporary_thread,
            destination_thread=session_thread_for(pending.expected_session_id),
            expected_url=session_url,
        )
    except TransitionRejected as exc:
        return replace(
            navigated_state,
            caller_session=handle,
            last_outcome=f"REBIND REJECTED — {exc} Session handle preserved for retry or recovery.",
        )
    return replace(
        rebound_state,
        pending_submission=None,
        caller_session=handle,
        last_outcome=f"Submission accepted; {rebind_outcome}. Session handle returned to caller.",
    )


def rebind_thread(
    state: PrototypeState,
    *,
    source_thread: str,
    destination_thread: str,
    expected_url: str,
) -> tuple[PrototypeState, str]:
    """Atomically move one Page object between registry keys, or change nothing."""
    pages = _pages(state)
    source_page = pages.get(source_thread)
    destination_page = pages.get(destination_thread)

    if source_page is None:
        if destination_page is not None and destination_page.url == expected_url:
            return state, f"rebind already completed at {destination_thread}"
        raise TransitionRejected("Rebind source is missing and the destination cannot prove prior success.")
    if source_page.url != expected_url:
        raise TransitionRejected("Rebind source is not on the expected ChatGPT session URL.")
    if destination_page is not None:
        raise TransitionRejected("Destination thread is occupied; registry was left unchanged.")

    del pages[source_thread]
    pages[destination_thread] = source_page
    return replace(state, pages=_freeze_pages(pages)), f"same page rebound to {destination_thread}"


def observe_session(state: PrototypeState) -> PrototypeState:
    handle = _require_session_handle(state)
    thread = session_thread_for(handle.session_id)
    pages = _pages(state)
    page = pages.get(thread)

    if page is not None:
        if page.url != handle.url:
            raise TransitionRejected("Deterministic thread is occupied by a different conversation.")
        guarantee = "exact submitted page reused" if page.token == handle.submitted_page_token else "recovered page reused"
        return replace(state, last_outcome=f"Observer attached through {thread}; {guarantee}.")

    recovered_page, state_with_page_number = _new_page(state, handle.url)
    pages = _pages(state_with_page_number)
    pages[thread] = recovered_page
    return replace(
        state_with_page_number,
        pages=_freeze_pages(pages),
        last_outcome=f"Observer reopened {handle.url} through {thread}; conversation recovered on a new page.",
    )


def exit_short_lived_caller(state: PrototypeState) -> PrototypeState:
    return replace(state, last_outcome="CLI caller exited; bridge-owned page registry is unchanged.")


def restart_bridge(state: PrototypeState) -> PrototypeState:
    return replace(
        state,
        bridge_generation=state.bridge_generation + 1,
        pages=(),
        pending_submission=None,
        last_outcome="Browser bridge restarted; page identity was lost, caller-held session handle remains usable.",
    )


def inject_destination_conflict(state: PrototypeState) -> PrototypeState:
    pending = _require_pending(state)
    thread = session_thread_for(pending.expected_session_id)
    pages = _pages(state)
    if thread in pages:
        raise TransitionRejected("The destination conflict already exists.")
    conflict_page, state_with_page_number = _new_page(state, "https://chatgpt.com/c/different-session")
    pages = _pages(state_with_page_number)
    pages[thread] = conflict_page
    return replace(
        state_with_page_number,
        pages=_freeze_pages(pages),
        last_outcome=f"Injected an unrelated page at {thread}; accepting must fail without replacing either page.",
    )


def remove_destination_conflict(state: PrototypeState) -> PrototypeState:
    pending = _require_pending(state)
    thread = session_thread_for(pending.expected_session_id)
    pages = _pages(state)
    if thread not in pages:
        raise TransitionRejected("There is no destination conflict to remove.")
    del pages[thread]
    return replace(state, pages=_freeze_pages(pages), last_outcome=f"Removed the injected conflict at {thread}.")


def session_thread_for(session_id: str) -> str:
    return f"{SESSION_THREAD_PREFIX}-{session_id}"


def session_url_for(session_id: str) -> str:
    return f"{CHATGPT_SESSION_URL_PREFIX}/{session_id}"


def _new_page(state: PrototypeState, url: str) -> tuple[Page, PrototypeState]:
    token = f"g{state.bridge_generation}-page-{state.next_page_number:03d}"
    return Page(token, url), replace(state, next_page_number=state.next_page_number + 1)


def _pages(state: PrototypeState) -> dict[str, Page]:
    return dict(state.pages)


def _freeze_pages(pages: dict[str, Page]) -> tuple[tuple[str, Page], ...]:
    return tuple(sorted(pages.items()))


def _require_pending(state: PrototypeState) -> PendingSubmission:
    if state.pending_submission is None:
        raise TransitionRejected("No submission is pending.")
    return state.pending_submission


def _require_session_handle(state: PrototypeState) -> SessionHandle:
    if state.caller_session is None:
        raise TransitionRejected("No caller-held ChatGPT session handle exists.")
    return state.caller_session
