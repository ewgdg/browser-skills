from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True)
class ModelChoice:
    model_query: str | None = None
    thinking_query: str | None = None
    requested_model: str | None = None
    requested_thinking: str | None = None


def normalize_model_choice(model: str | None, thinking: str | None) -> ModelChoice:
    return ModelChoice(
        model_query=_clean_query(model),
        thinking_query=_clean_query(thinking),
        requested_model=model,
        requested_thinking=thinking,
    )


def _clean_query(value: str | None) -> str | None:
    if value is None:
        return None
    raw = value.strip()
    return raw or None
