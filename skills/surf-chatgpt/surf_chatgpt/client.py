from __future__ import annotations

from dataclasses import dataclass

from . import SOURCE_LABEL
from .browser_chatgpt import ReusableAskOptions, ask_reusable_session
from .errors import SkillError
from .extract import enforce_budget
from .prompts import PromptContract, build_prompt
from .surf import SurfRunner


@dataclass(frozen=True)
class AskOptions:
    mode: str = "answer"
    session_policy: str = "ephemeral"
    session_url: str | None = None
    model: str | None = None
    thinking_label: str | None = None
    requested_model: str | None = None
    requested_thinking: str | None = None
    timeout: int = 2700
    max_chars: int = 6000
    max_words: int | None = None
    start_new: bool = False


def ask_chatgpt(user_prompt: str, options: AskOptions, *, surf: SurfRunner | None = None) -> dict:
    contracted_prompt = build_prompt(
        user_prompt,
        PromptContract(
            mode=options.mode,
            session_policy=options.session_policy,
            thread_name=None,
            max_chars=options.max_chars,
            max_words=options.max_words,
        ),
    )

    if options.model:
        raise SkillError(
            "invalid_args",
            "top-level --model values are not supported by the controlled browser path; use --thinking low|medium|high or --model gpt5.5:<level>",
        )

    runner = surf or SurfRunner()
    raw = ask_reusable_session(
        contracted_prompt,
        ReusableAskOptions(
            mode=options.mode,
            session_policy=options.session_policy,
            session_url=options.session_url,
            start_new=options.start_new,
            timeout=options.timeout,
            thinking_label=options.thinking_label,
        ),
        surf=runner,
    )
    cleaned = enforce_budget(str(raw.get("response", "")), options.max_chars, options.max_words)

    return {
        "ok": True,
        "source": SOURCE_LABEL,
        "mode": options.mode,
        "session": raw.get("session") or {"policy": "ephemeral", "id": None, "url": None, "reused": False},
        "model": raw.get("model", options.model or "current"),
        "requested_model": options.requested_model,
        "requested_thinking": options.requested_thinking,
        "selected_thinking": options.thinking_label,
        "message_id": raw.get("messageId"),
        "answer": cleaned.answer,
        "truncated": cleaned.truncated,
        "chars": cleaned.chars,
        "words": cleaned.words,
        "took_ms": raw.get("tookMs"),
        "warnings": cleaned.warnings,
    }

