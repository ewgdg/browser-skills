from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Callable

from .errors import PublicError


class ProcessExitCode(IntEnum):
    SUCCESS = 0
    OPERATIONAL_FAILURE = 1
    INVALID_INPUT = 2


@dataclass(frozen=True)
class SearchRequest:
    query: str
    start_page: int = 1
    page_count: int = 1
    thread: str | None = None


@dataclass(frozen=True)
class CommandOutcome:
    _public_value: dict[str, Any]
    exit_code: ProcessExitCode = ProcessExitCode.SUCCESS
    post_output_cleanup: Callable[[], None] | None = None

    @classmethod
    def success(
        cls,
        public_fields: Mapping[str, Any],
        *,
        post_output_cleanup: Callable[[], None] | None = None,
    ) -> CommandOutcome:
        return cls(
            {"ok": True, **dict(public_fields)},
            post_output_cleanup=post_output_cleanup,
        )

    @classmethod
    def failure(
        cls,
        error: PublicError,
        *,
        exit_code: ProcessExitCode = ProcessExitCode.OPERATIONAL_FAILURE,
        public_fields: Mapping[str, Any] | None = None,
        post_output_cleanup: Callable[[], None] | None = None,
    ) -> CommandOutcome:
        return cls(
            {
                "ok": False,
                "error": error.to_public_json(),
                **dict(public_fields or {}),
            },
            exit_code=exit_code,
            post_output_cleanup=post_output_cleanup,
        )

    def to_public_json(self) -> dict[str, Any]:
        return dict(self._public_value)
