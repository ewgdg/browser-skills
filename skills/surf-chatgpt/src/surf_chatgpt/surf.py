from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from .errors import SkillError, classify_surf_failure, compact_message


Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass
class SurfRunner:
    command_prefix: Sequence[str] | None = None
    runner: Runner = subprocess.run

    def run_text(self, args: Sequence[str], timeout: int = 30, *, thread: str | None = None) -> str:
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
        path = _write_temp_js(code)
        try:
            return self.eval_file(thread, path, timeout=timeout)
        finally:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    def _command_prefix(self) -> list[str]:
        if self.command_prefix is not None:
            return list(self.command_prefix)
        return default_surf_agent_command()


def default_surf_agent_command() -> list[str]:
    if shutil.which("surf-agent"):
        return ["surf-agent"]
    sibling = Path(__file__).resolve().parents[3] / "surf"
    if (sibling / "pyproject.toml").exists():
        return ["uv", "--project", str(sibling), "run", "surf-agent"]
    return ["surf-agent"]


def _write_temp_js(code: str) -> str:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".js", prefix="surf-chatgpt-eval-", dir="/tmp", delete=False) as handle:
        handle.write(code)
        handle.write("\n")
        return handle.name


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
