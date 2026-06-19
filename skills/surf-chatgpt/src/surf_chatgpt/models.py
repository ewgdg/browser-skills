from __future__ import annotations

from dataclasses import dataclass

from .errors import SkillError

THINKING_TO_WEB_LABEL = {
    "low": "Instant",
    "medium": "Medium",
    "high": "High",
    # Backward-compatible parser aliases; user-facing CLI/docs use low|medium|high.
    "instant": "Instant",
}

# Legacy/top-level model tokens. These are not GPT-5.5 thinking levels.
SURF_MODEL_ALIASES = {
    "instant": "instant",
    "fast": "instant",
    "thinking": "thinking",
    "pro": "pro",
    "gpt53": "instant",
    "gpt54thinking": "thinking",
    "gpt54pro": "pro",
}


@dataclass(frozen=True)
class ModelChoice:
    surf_model_token: str | None = None
    thinking_label: str | None = None
    requested_model: str | None = None
    requested_thinking: str | None = None


def normalize_model_choice(model: str | None, thinking: str | None) -> ModelChoice:
    thinking_label = THINKING_TO_WEB_LABEL.get(thinking) if thinking else None
    model_surf_token: str | None = None
    model_thinking_label: str | None = None

    if model:
        parsed = normalize_model_selector(model)
        model_surf_token = parsed.surf_model_token
        model_thinking_label = parsed.thinking_label

    if thinking_label and model and _is_gpt55_family_selector(model) and not model_thinking_label and not model_surf_token:
        model_thinking_label = thinking_label

    if thinking_label and model_thinking_label and thinking_label != model_thinking_label:
        raise SkillError(
            "invalid_args",
            f"--model {model!r} conflicts with --thinking {thinking!r}",
            hint="Use matching values, e.g. --model gpt5.5:high --thinking high, or pass only --thinking.",
        )
    if thinking_label and model_surf_token:
        raise SkillError(
            "invalid_args",
            f"--model {model!r} is a top-level surf model token and cannot be combined with --thinking {thinking!r}",
            hint="For GPT-5.5 thinking use --thinking low|medium|high or --model gpt5.5:<level>.",
        )

    return ModelChoice(
        surf_model_token=model_surf_token,
        thinking_label=thinking_label or model_thinking_label,
        requested_model=model,
        requested_thinking=thinking,
    )


def normalize_model_selector(value: str | None) -> ModelChoice:
    if value is None:
        return ModelChoice()
    raw = value.strip().lower()
    if not raw:
        return ModelChoice()

    # GPT-5.5 thinking submenu syntax, e.g. gpt5.5:high.
    for separator in (":", "/", "#"):
        if separator in raw:
            suffix = raw.rsplit(separator, 1)[1].strip()
            if suffix in THINKING_TO_WEB_LABEL:
                return ModelChoice(thinking_label=THINKING_TO_WEB_LABEL[suffix], requested_model=value)

    compact = "".join(ch for ch in raw if ch.isalnum())
    if compact in {"low", "medium", "high"}:
        return ModelChoice(thinking_label=THINKING_TO_WEB_LABEL[compact], requested_model=value)
    if compact in SURF_MODEL_ALIASES:
        return ModelChoice(surf_model_token=SURF_MODEL_ALIASES[compact], requested_model=value)

    # Accept strings that end with a thinking level, e.g. gpt55high.
    for suffix, label in (("low", "Instant"), ("instant", "Instant"), ("medium", "Medium"), ("high", "High")):
        if compact.endswith(suffix):
            return ModelChoice(thinking_label=label, requested_model=value)

    if compact in {"gpt55", "gpt5", "chatgpt55", "chatgpt5"}:
        return ModelChoice(requested_model=value)

    raise SkillError(
        "model_unavailable",
        f"unsupported ChatGPT model/thinking selector: {value}",
        hint="Use --thinking low|medium|high or --model gpt5.5:<level>.",
    )


def _is_gpt55_family_selector(value: str) -> bool:
    compact = "".join(ch for ch in value.strip().lower() if ch.isalnum())
    return compact in {"gpt55", "gpt5", "chatgpt55", "chatgpt5"}
