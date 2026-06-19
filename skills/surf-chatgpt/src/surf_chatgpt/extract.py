from __future__ import annotations

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
