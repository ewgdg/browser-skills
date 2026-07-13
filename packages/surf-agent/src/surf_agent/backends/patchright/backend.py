from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path
from typing import Any, Callable

from ...constants import PATCHRIGHT_BACKEND
from ...errors import SurfAgentError
from ...chrome_lifecycle import browser_executable_family
from ..local_bridge import LocalBridgeBackend, LocalBridgeClient, stable_local_page_id

PATCHRIGHT_INSTALL_HINT = (
    'run `uv tool install "surf-agent[patchright] @ git+https://github.com/ewgdg/browser-skills.git#subdirectory=packages/surf-agent"`, '
    "install Google Chrome yourself, and set SURF_AGENT_CHROME_BIN if Chrome is not on PATH"
)


class PatchrightBridgeClient(LocalBridgeClient):
    def __init__(self, *, timeout_s: float, port: int, profile_dir: Path) -> None:
        super().__init__(
            backend_label="Patchright",
            module_name="surf_agent.backends.patchright.bridge",
            timeout_s=timeout_s,
            port=port,
            profile_dir=profile_dir,
            startup_error=PATCHRIGHT_INSTALL_HINT,
            timeout_hint="; restart it with `surf-agent bridge stop` if it stays wedged",
        )


class PatchrightBackend(LocalBridgeBackend):
    name = PATCHRIGHT_BACKEND
    display_name = "Patchright"
    client_attr = "patchright_client"

    def __init__(self, agent: Any, *, client: PatchrightBridgeClient, welcome_url: Callable[[], str]) -> None:
        super().__init__(agent, client=client, welcome_url=welcome_url)

    def close_matching(self, pattern: str) -> int:
        pattern = pattern.strip()
        if not pattern:
            raise SurfAgentError("close-matching requires a thread glob pattern", exit_code=2)

        output = self.client.call_tool_if_running("close-matching", {"pattern": pattern})
        if output is None:
            self._print_output(json.dumps({"pattern": pattern, "closed": [], "failed": []}, sort_keys=True) + "\n")
            return 0
        try:
            result = json.loads(output)
        except json.JSONDecodeError as exc:
            raise SurfAgentError("Patchright bridge close-matching returned invalid JSON") from exc
        if not isinstance(result, dict) or not isinstance(result.get("failed"), list):
            raise SurfAgentError("Patchright bridge close-matching returned invalid JSON")
        self._print_output(output)
        return 1 if result["failed"] else 0

    def profile_open(self, url: str, *, profile_dir: str, app_id: str, window_class: str) -> int:
        if browser_executable_family(self.agent.chrome_bin) != "chrome":
            raise SurfAgentError("Patchright profile open requires a provable Google Chrome executable")
        with self.agent._patchright_startup_guard():
            if self.client._health_ok():
                raise SurfAgentError("automated Surf Agent Patchright bridge is running; run `surf-agent bridge stop` before `profile open`")
            if not self.agent.chrome_bin:
                raise SurfAgentError("could not find Chrome executable for profile open; set SURF_AGENT_CHROME_BIN")
            profile_path = Path(profile_dir)
            profile_path.mkdir(parents=True, exist_ok=True)
            command = [*shlex.split(self.agent.chrome_bin), f"--class={window_class}", f"--user-data-dir={profile_path}", "--new-window", f"--name={app_id}", url]
            subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return 0

    def bridge_stop(self) -> int:
        output = self.client.stop()
        self._print_output(output)
        _cli().stop_patchright_runtime(self.agent.patchright_profile_dir, port=self.agent.patchright_port)
        return 0


def _cli() -> Any:
    import surf_agent.cli as cli

    return cli

stable_patchright_page_id = stable_local_page_id
