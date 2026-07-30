from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Mapping, Sequence
from typing import Any

DEFAULT_COMMAND_TIMEOUT_SECONDS = 60.0


class GateCommandError(AssertionError):
    """Report only content-free live-gate command context."""


CommandRun = Callable[..., subprocess.CompletedProcess[str]]


class JsonCommandRunner:
    def __init__(
        self,
        environment: Mapping[str, str],
        *,
        run: CommandRun = subprocess.run,
    ) -> None:
        self._environment = dict(environment)
        self._run = run

    def chatgpt(
        self,
        operation: str,
        *arguments: str,
        stdin: str | None = None,
        timeout: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        __tracebackhide__ = True
        return self._json_command(
            operation,
            ("surf-chatgpt", *arguments),
            stdin=stdin,
            timeout=timeout,
        )

    def chatgpt_error(
        self,
        operation: str,
        expected_error_type: str,
        *arguments: str,
        stdin: str | None = None,
        timeout: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        __tracebackhide__ = True
        completed, payload = self._run_json(
            operation,
            ("surf-chatgpt", *arguments),
            stdin=stdin,
            timeout=timeout,
        )
        actual_error_type = _safe_error_type(payload)
        if completed.returncode == 0 or actual_error_type != expected_error_type:
            suffix = (
                f", error {actual_error_type}" if actual_error_type is not None else ""
            )
            raise GateCommandError(
                f"{operation} returned an unexpected outcome "
                f"(exit {completed.returncode}{suffix})"
            )
        return payload

    def chatgpt_outcome(
        self,
        operation: str,
        *arguments: str,
        stdin: str | None = None,
        timeout: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    ) -> tuple[int, dict[str, Any]]:
        __tracebackhide__ = True
        completed, payload = self._run_json(
            operation,
            ("surf-chatgpt", *arguments),
            stdin=stdin,
            timeout=timeout,
        )
        return completed.returncode, payload

    def agent_json(
        self,
        operation: str,
        *arguments: str,
        timeout: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        __tracebackhide__ = True
        return self._json_command(
            operation,
            ("surf-agent", *arguments),
            timeout=timeout,
        )

    def agent(
        self,
        operation: str,
        *arguments: str,
        timeout: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    ) -> None:
        __tracebackhide__ = True
        completed = self._invoke(arguments, timeout=timeout)
        if completed.returncode != 0:
            raise GateCommandError(f"{operation} failed (exit {completed.returncode})")

    def _json_command(
        self,
        operation: str,
        command: Sequence[str],
        *,
        stdin: str | None = None,
        timeout: float,
    ) -> dict[str, Any]:
        __tracebackhide__ = True
        completed, payload = self._run_json(
            operation,
            command,
            stdin=stdin,
            timeout=timeout,
        )
        if completed.returncode != 0:
            error_type = _safe_error_type(payload)
            suffix = f", error {error_type}" if error_type is not None else ""
            raise GateCommandError(
                f"{operation} failed (exit {completed.returncode}{suffix})"
            )
        return payload

    def _run_json(
        self,
        operation: str,
        command: Sequence[str],
        *,
        stdin: str | None,
        timeout: float,
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
        __tracebackhide__ = True
        completed = self._run(
            list(command),
            input=stdin,
            capture_output=True,
            text=True,
            env=self._environment,
            timeout=timeout,
            check=False,
        )
        try:
            payload = json.loads(completed.stdout)
        except (json.JSONDecodeError, TypeError):
            raise GateCommandError(f"{operation} returned invalid JSON") from None
        if not isinstance(payload, dict):
            raise GateCommandError(f"{operation} returned invalid JSON")
        return completed, payload

    def _invoke(
        self,
        arguments: Sequence[str],
        *,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        __tracebackhide__ = True
        return self._run(
            ["surf-agent", *arguments],
            capture_output=True,
            text=True,
            env=self._environment,
            timeout=timeout,
            check=False,
        )


def _safe_error_type(payload: Mapping[str, Any]) -> str | None:
    error = payload.get("error")
    if not isinstance(error, dict):
        return None
    error_type = error.get("type")
    if not isinstance(error_type, str):
        return None
    if not error_type or any(
        character not in "abcdefghijklmnopqrstuvwxyz_" for character in error_type
    ):
        return None
    return error_type
