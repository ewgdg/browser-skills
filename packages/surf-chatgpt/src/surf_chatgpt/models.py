from __future__ import annotations

from dataclasses import dataclass

from .errors import SkillError

THINKING_TO_WEB_LABEL = {
    "low": "Instant",
    "medium": "Medium",
    "high": "High",
    "highest": "highest",
    # Parser aliases; user-facing CLI/docs use low|medium|high|highest.
    "instant": "Instant",
}


@dataclass(frozen=True)
class ModelChoice:
    model_query: str | None = None
    thinking_label: str | None = None
    requested_model: str | None = None
    requested_thinking: str | None = None


def normalize_model_choice(model: str | None, thinking: str | None) -> ModelChoice:
    thinking_label = THINKING_TO_WEB_LABEL.get(thinking) if thinking else None
    parsed = normalize_model_selector(model)

    if thinking_label and parsed.thinking_label and thinking_label != parsed.thinking_label:
        raise SkillError(
            "invalid_args",
            f"--model {model!r} conflicts with --thinking {thinking!r}",
            hint="Use matching values, e.g. --model gpt-5.5:high --thinking high, or pass only --thinking.",
        )

    return ModelChoice(
        model_query=parsed.model_query,
        thinking_label=thinking_label or parsed.thinking_label,
        requested_model=model,
        requested_thinking=thinking,
    )


def normalize_model_selector(value: str | None) -> ModelChoice:
    if value is None:
        return ModelChoice()
    raw = value.strip()
    if not raw:
        return ModelChoice()

    lowered = raw.lower()
    for separator in (":", "/", "#"):
        if separator in lowered:
            prefix, suffix = lowered.rsplit(separator, 1)
            suffix = suffix.strip()
            if suffix in THINKING_TO_WEB_LABEL:
                model_query = raw.rsplit(separator, 1)[0].strip() or None
                return ModelChoice(model_query=model_query, thinking_label=THINKING_TO_WEB_LABEL[suffix], requested_model=value)

    compact = "".join(ch for ch in lowered if ch.isalnum())
    if compact in THINKING_TO_WEB_LABEL:
        return ModelChoice(thinking_label=THINKING_TO_WEB_LABEL[compact], requested_model=value)

    return ModelChoice(model_query=raw, requested_model=value)
