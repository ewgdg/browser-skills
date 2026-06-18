from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from .errors import SkillError, classify_surf_failure, compact_message


Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass
class SurfRunner:
    executable: str = "surf"
    runner: Runner = subprocess.run

    def run_json(self, args: Sequence[str], timeout: int = 30, *, global_args: Sequence[str] | None = None) -> Any:
        command = [self.executable, *(global_args or ()), *args, "--json"]
        try:
            proc = self.runner(
                command,
                text=True,
                capture_output=True,
                timeout=max(timeout, 1),
                check=False,
            )
        except FileNotFoundError as exc:
            raise SkillError("surf_unavailable", "surf executable not found") from exc
        except subprocess.TimeoutExpired as exc:
            raise SkillError("timeout", f"surf command timed out after {timeout}s") from exc

        if proc.returncode != 0:
            raise classify_surf_failure(proc.returncode, proc.stdout, proc.stderr)

        stdout = (proc.stdout or "").strip()
        if not stdout:
            raise SkillError("parse_error", "surf returned empty JSON output")
        try:
            return json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise SkillError("parse_error", f"surf returned non-JSON output: {compact_message(stdout)}") from exc

    def run_json_on_tab(self, tab_id: int, args: Sequence[str], timeout: int = 30) -> Any:
        return self.run_json(args, timeout=timeout, global_args=["--tab-id", str(tab_id)])
