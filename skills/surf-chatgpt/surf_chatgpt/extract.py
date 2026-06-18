from __future__ import annotations

from dataclasses import dataclass
import re


UI_NOISE_LINES = {
    "copy",
    "copied",
    "good response",
    "bad response",
    "share",
    "regenerate",
    "regenerate response",
    "read aloud",
    "report content",
    "continue generating",
    "chatgpt can make mistakes. check important info.",
}


@dataclass(frozen=True)
class CleanedAnswer:
    answer: str
    truncated: bool
    chars: int
    words: int
    warnings: list[str]


def clean_response(raw: str) -> str:
    text = (raw or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    cleaned: list[str] = []
    in_code = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            cleaned.append(line.rstrip())
            continue
        if not in_code and stripped.lower() in UI_NOISE_LINES:
            continue
        cleaned.append(line.rstrip() if in_code else stripped)

    return _normalize_non_code("\n".join(cleaned)).strip()


def _normalize_non_code(text: str) -> str:
    parts = re.split(r"(```.*?```)", text, flags=re.S)
    output: list[str] = []
    for part in parts:
        if part.startswith("```"):
            output.append(part.strip("\n"))
        else:
            compact = re.sub(r"[ \t]+", " ", part)
            compact = re.sub(r"\n{3,}", "\n\n", compact)
            output.append(compact.strip())
    return "\n\n".join(p for p in output if p)


def enforce_budget(text: str, max_chars: int, max_words: int | None = None) -> CleanedAnswer:
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    cleaned = clean_response(text)
    warnings: list[str] = []
    truncated = False

    if max_words is not None:
        if max_words <= 0:
            raise ValueError("max_words must be positive")
        words = cleaned.split()
        if len(words) > max_words:
            cleaned = " ".join(words[:max_words]).rstrip() + "…"
            truncated = True
            warnings.append(f"truncated_to_{max_words}_words")

    if len(cleaned) > max_chars:
        cleaned = cleaned[: max_chars - 1].rstrip() + "…"
        truncated = True
        warnings.append(f"truncated_to_{max_chars}_chars")

    return CleanedAnswer(
        answer=cleaned,
        truncated=truncated,
        chars=len(cleaned),
        words=len(cleaned.split()) if cleaned else 0,
        warnings=warnings,
    )
