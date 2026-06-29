from __future__ import annotations

import contextlib
import io
import json
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from .errors import SkillError, classify_surf_failure, compact_message
from .temp_js import unlink_temp_file, write_temp_js


Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass
class SurfRunner:
    command_prefix: Sequence[str] | None = None
    runner: Runner = subprocess.run

    def run_text(self, args: Sequence[str], timeout: int = 30, *, thread: str | None = None) -> str:
        if self.command_prefix is None and self.runner is subprocess.run:
            return run_surf_agent_main(args, thread=thread)

        command = [*self._command_prefix()]
        if thread:
            command.extend(["--thread", thread])
        command.extend(args)
        try:
            proc = self.runner(
                command,
                text=True,
                capture_output=True,
                timeout=max(timeout, 1),
                check=False,
            )
        except FileNotFoundError as exc:
            raise SkillError("surf_unavailable", "surf-agent executable not found") from exc
        except subprocess.TimeoutExpired as exc:
            raise SkillError("timeout", f"surf-agent command timed out after {timeout}s") from exc

        if proc.returncode != 0:
            raise classify_surf_failure(proc.returncode, proc.stdout, proc.stderr)
        return proc.stdout or ""

    def new(self, thread: str, timeout: int = 30) -> str:
        return self.run_text(["new"], timeout=timeout, thread=thread)

    def open(self, thread: str, url: str, timeout: int = 30) -> str:
        return self.run_text(["open", url], timeout=timeout, thread=thread)

    def close(self, thread: str, timeout: int = 10) -> str:
        return self.run_text(["close"], timeout=timeout, thread=thread)

    def wait(self, thread: str, duration_or_text: str, timeout: int = 35) -> str:
        return self.run_text(["wait", duration_or_text], timeout=timeout, thread=thread)

    def eval_file(self, thread: str, path: str, timeout: int = 30) -> Any:
        output = self.run_text(["eval", "--file", path], timeout=timeout, thread=thread)
        return unwrap_eval_text(output)

    def eval_code(self, thread: str, code: str, timeout: int = 30) -> Any:
        path = write_temp_js(code, prefix="surf-chatgpt-eval-")
        try:
            return self.eval_file(thread, path, timeout=timeout)
        finally:
            unlink_temp_file(path)

    def _command_prefix(self) -> list[str]:
        if self.command_prefix is not None:
            return list(self.command_prefix)
        return ["surf-agent"]


def run_surf_agent_main(args: Sequence[str], *, thread: str | None = None) -> str:
    try:
        from surf_agent import run_cli as surf_agent_main
    except ImportError as exc:
        raise SkillError("surf_unavailable", "surf-agent package not installed") from exc

    argv: list[str] = []
    if thread:
        argv.extend(["--thread", thread])
    argv.extend(args)

    stdout = io.StringIO()
    stderr = io.StringIO()
    # Direct API call avoids PATH coupling when surf-chatgpt and surf-agent share one tool env.
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        returncode = surf_agent_main(argv)
    if returncode != 0:
        raise classify_surf_failure(returncode, stdout.getvalue(), stderr.getvalue())
    return stdout.getvalue()


def unwrap_eval_text(output: str) -> Any:
    raw = (output or "").strip()
    if raw.startswith("result:"):
        raw = raw[len("result:") :].strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        if raw.startswith("{") or raw.startswith("[") or raw.startswith('"'):
            raise SkillError("parse_error", f"surf-agent returned non-JSON eval output: {compact_message(raw)}") from exc
        return raw
