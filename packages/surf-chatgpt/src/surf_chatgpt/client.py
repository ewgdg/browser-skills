from __future__ import annotations

from dataclasses import dataclass

from . import SOURCE_LABEL
from .browser_chatgpt import ReusableAskOptions, ask_reusable_session, select_reusable_model_choice
from .extract import clean_response
from .surf import SurfRunner


@dataclass(frozen=True)
class AskOptions:
    session_policy: str = "ephemeral"
    session_url: str | None = None
    thread: str | None = None
    keep_open: bool = False
    model_query: str | None = None
    thinking_query: str | None = None
    requested_model: str | None = None
    requested_thinking: str | None = None
    timeout: int = 2700
    start_new: bool = False
    allow_logged_out: bool = False
    pace: str = "natural"


@dataclass(frozen=True)
class ModelSelectOptions:
    model_query: str | None = None
    thinking_query: str | None = None
    requested_model: str | None = None
    requested_thinking: str | None = None
    thread: str | None = None


def ask_chatgpt(user_prompt: str, options: AskOptions, *, surf: SurfRunner | None = None) -> dict:
    runner = surf or SurfRunner()
    raw = ask_reusable_session(
        user_prompt,
        ReusableAskOptions(
            session_policy=options.session_policy,
            session_url=options.session_url,
            thread=options.thread,
            keep_open=options.keep_open,
            model_query=options.model_query,
            start_new=options.start_new,
            timeout=options.timeout,
            thinking_query=options.thinking_query,
            allow_logged_out=options.allow_logged_out,
            pace=options.pace,
        ),
        surf=runner,
    )
    answer = clean_response(str(raw.get("response", "")))

    return {
        "ok": True,
        "source": SOURCE_LABEL,
        "session": raw.get("session") or {"policy": "ephemeral", "id": None, "url": None, "reused": False},
        "model": raw.get("model", options.model_query or "current"),
        "requested_model": options.requested_model,
        "requested_thinking": options.requested_thinking,
        "selected_thinking": raw.get("thinking", options.thinking_query),
        "message_id": raw.get("messageId"),
        "answer": answer,
        "took_ms": raw.get("tookMs"),
        "warnings": raw.get("warnings") or [],
    }


def select_chatgpt_model(options: ModelSelectOptions, *, surf: SurfRunner | None = None) -> dict:
    raw = select_reusable_model_choice(
        model_query=options.model_query,
        thinking_query=options.thinking_query,
        thread=options.thread,
        surf=surf,
    )
    return {
        "ok": True,
        "source": SOURCE_LABEL,
        "requested_model": options.requested_model,
        "requested_thinking": options.requested_thinking,
        "selected_model": raw.get("model"),
        "selected_thinking": raw.get("thinking"),
        "active_picker": raw.get("activePicker"),
        "verified": raw.get("verified", False),
        "session": raw.get("session"),
    }
