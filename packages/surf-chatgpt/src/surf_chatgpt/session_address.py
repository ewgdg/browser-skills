from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

CANONICAL_SESSION_URL_PREFIX = "https://chatgpt.com/c/"
SESSION_THREAD_PREFIX = "surf-chatgpt-session-"
CHATGPT_SESSION_ID_PATTERN = (
    r"(?=[A-Za-z0-9._~-]*[A-Za-z0-9])[A-Za-z0-9._~-]+"
)
_SESSION_ID_PATTERN = re.compile(CHATGPT_SESSION_ID_PATTERN, flags=re.ASCII)


class InvalidSessionAddress(ValueError):
    """Raised when a public session reference is not an ID or canonical URL."""

    def __init__(self) -> None:
        super().__init__("Session must be an ID or exact canonical ChatGPT conversation URL.")


@dataclass(frozen=True)
class SessionAddress:
    id: str

    def __post_init__(self) -> None:
        if _SESSION_ID_PATTERN.fullmatch(self.id) is None:
            raise InvalidSessionAddress

    @classmethod
    def parse(cls, value: str) -> SessionAddress:
        if _SESSION_ID_PATTERN.fullmatch(value) is not None:
            return cls(value)

        if value.startswith(CANONICAL_SESSION_URL_PREFIX):
            session_id = value.removeprefix(CANONICAL_SESSION_URL_PREFIX)
            return cls(session_id)

        raise InvalidSessionAddress

    @property
    def canonical_url(self) -> str:
        return f"{CANONICAL_SESSION_URL_PREFIX}{self.id}"

    @property
    def thread(self) -> str:
        digest = hashlib.sha256(self.id.encode()).hexdigest()
        return f"{SESSION_THREAD_PREFIX}{digest}"

    def to_public_json(self) -> dict[str, Any]:
        return {"id": self.id}
