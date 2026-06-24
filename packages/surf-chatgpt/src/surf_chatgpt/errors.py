from __future__ import annotations

from dataclasses import dataclass


ERROR_HINTS = {
    "empty_prompt": "Pass a prompt argument or pipe focused prompt/context into stdin.",
    "invalid_args": "Use --help and choose either ephemeral mode or one explicit session mode.",
    "login_required": "Open the surf-agent profile and log in to chatgpt.com, then retry.",
    "captcha_or_cloudflare": "Open the surf-agent profile and complete the ChatGPT challenge manually.",
    "ui_changed": "Update surf-agent or this skill; ChatGPT UI selectors likely changed.",
    "timeout": "Retry with --timeout SECONDS, smaller context, or a faster model.",
    "surf_unavailable": "Install surf-agent and ensure it is on PATH.",
    "browser_unavailable": "Start or repair the surf-agent browser bridge/profile.",
    "model_unavailable": "Use a model available in your ChatGPT account or omit --model.",
    "parse_error": "surf-agent returned unexpected output; retry or update surf-agent.",
    "session_not_found": "Use ask --new to create a session, then pass the returned id/url with ask --session ID_OR_URL.",
    "unknown": "Retry with smaller input. If it persists, inspect surf-agent manually.",
}


@dataclass
class SkillError(Exception):
    type: str
    message: str
    hint: str | None = None
    exit_code: int = 1

    def __post_init__(self) -> None:
        super().__init__(self.message)
        if self.hint is None:
            self.hint = ERROR_HINTS.get(self.type, ERROR_HINTS["unknown"])

    def to_dict(self) -> dict[str, str]:
        result = {"type": self.type, "message": self.message}
        if self.hint:
            result["hint"] = self.hint
        return result


def compact_message(text: str, limit: int = 300) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def classify_surf_failure(returncode: int, stdout: str, stderr: str) -> SkillError:
    raw = f"{stdout}\n{stderr}".strip()
    lowered = raw.lower()
    msg = compact_message(raw) or f"surf-agent exited with code {returncode}"

    if "login required" in lowered or "log in" in lowered or "login" in lowered and "chatgpt" in lowered:
        return SkillError("login_required", "ChatGPT login required")
    if "cloudflare" in lowered or "captcha" in lowered or "challenge" in lowered:
        return SkillError("captcha_or_cloudflare", "ChatGPT challenge detected")
    if "response timeout" in lowered or "request timed out" in lowered or "timeout" in lowered:
        return SkillError("timeout", "ChatGPT response timed out")
    if "prompt textarea not ready" in lowered or "element not found" in lowered or "selector" in lowered or "ui" in lowered:
        return SkillError("ui_changed", "ChatGPT UI automation failed")
    if "no remembered browser page" in lowered or "browser bridge" in lowered or "dedicated profile" in lowered or "connection refused" in lowered or "chrome running" in lowered:
        return SkillError("browser_unavailable", "surf-agent browser bridge unavailable")
    if "model" in lowered and ("not found" in lowered or "unavailable" in lowered or "failed" in lowered):
        return SkillError("model_unavailable", "Requested ChatGPT model unavailable")
    return SkillError("unknown", msg)
