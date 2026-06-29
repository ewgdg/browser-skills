from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from typing import Any, Callable

from ...constants import PATCHRIGHT_BACKEND
from ...errors import SurfAgentError
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

    def profile_open(self, url: str, *, profile_dir: str, app_id: str, window_class: str) -> int:
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
