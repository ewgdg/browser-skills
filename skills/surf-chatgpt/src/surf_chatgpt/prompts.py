from __future__ import annotations

from dataclasses import dataclass


MODES = ("answer", "critique", "redteam", "plan-review")

MODE_INSTRUCTIONS = {
    "answer": "Answer directly. Prefer concrete facts, caveats only when decision-relevant.",
    "critique": "Critique the proposal. Focus on flaws, missing constraints, simpler alternatives, and highest-value fixes.",
    "redteam": "Red-team the idea. Look for failure modes, abuse cases, brittle assumptions, and ways this breaks under pressure.",
    "plan-review": "Review the plan for implementability. Check sequencing, hidden decisions, testability, scope creep, and stop conditions.",
}


@dataclass(frozen=True)
class PromptContract:
    mode: str
    session_policy: str
    max_chars: int
    max_words: int | None = None
    thread_name: str | None = None


def build_prompt(user_prompt: str, contract: PromptContract) -> str:
    if contract.mode not in MODE_INSTRUCTIONS:
        raise ValueError(f"unsupported mode: {contract.mode}")
    clean_prompt = user_prompt.strip()
    if not clean_prompt:
        raise ValueError("empty prompt")

    budget = f"Target <= {contract.max_chars} chars"
    if contract.max_words:
        budget += f" and <= {contract.max_words} words"

    if contract.session_policy in {"ephemeral", "new"}:
        context_rule = "Treat this as a fresh request. Use only the context below; do not rely on prior memories or earlier conversation."
    else:
        context_rule = (
            "This is a follow-up in this conversation. You may use earlier context only when relevant; "
            "the context below wins on conflict. Say briefly if earlier context materially affected the answer."
        )

    return "\n".join(
        [
            "Please help with the request below.",
            context_rule,
            "Do not ask for or reveal secrets, credentials, tokens, cookies, or private keys.",
            MODE_INSTRUCTIONS[contract.mode],
            f"Response format: compact Markdown only; no preamble; {budget}; include only useful answer.",
            "",
            "Request:",
            clean_prompt,
        ]
    )
