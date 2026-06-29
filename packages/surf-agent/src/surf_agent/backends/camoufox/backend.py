from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable

from ...constants import CAMOUFOX_BACKEND
from ...errors import SurfAgentError
from ..local_bridge import LocalBridgeBackend, LocalBridgeClient, stable_local_page_id

CAMOUFOX_STARTUP_HINT = (
    'run `uv tool install "surf-agent[camoufox] @ git+https://github.com/ewgdg/browser-skills.git#subdirectory=packages/surf-agent"`, '
    'then manually run `python -m camoufox fetch`'
)
CAMOUFOX_PACKAGE_HINT = (
    'Run `uv tool install "surf-agent[camoufox] @ git+https://github.com/ewgdg/browser-skills.git#subdirectory=packages/surf-agent"`, '
    'then manually run `python -m camoufox fetch`'
)


def _camoufox_binary_path() -> str:
    """Locate the Camoufox browser binary (lazy import — optional dependency)."""
    try:
        from camoufox.utils import launch_path
    except ImportError:
        raise SurfAgentError(f"Camoufox package not installed. {CAMOUFOX_PACKAGE_HINT}.")
    try:
        return launch_path()
    except Exception as exc:
        raise SurfAgentError(f"Camoufox binary not found: {exc}. Manually run `python -m camoufox fetch`.")


class CamoufoxBridgeClient(LocalBridgeClient):
    def __init__(self, *, timeout_s: float, port: int, profile_dir: Path) -> None:
        super().__init__(
            backend_label="Camoufox",
            module_name="surf_agent.backends.camoufox.bridge",
            timeout_s=timeout_s,
            port=port,
            profile_dir=profile_dir,
            startup_error=CAMOUFOX_STARTUP_HINT,
        )


class CamoufoxBackend(LocalBridgeBackend):
    name = CAMOUFOX_BACKEND
    display_name = "Camoufox"
    client_attr = "camoufox_client"

    def __init__(self, agent: Any, *, client: CamoufoxBridgeClient, welcome_url: Callable[[], str]) -> None:
        super().__init__(agent, client=client, welcome_url=welcome_url)

    def profile_open(self, url: str, *, profile_dir: str, app_id: str) -> int:
        if self.client._health_ok():
            raise SurfAgentError("Camoufox bridge is running; run `surf-agent bridge stop` before `profile open`")
        profile_path = Path(profile_dir)
        profile_path.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(
            [_camoufox_binary_path(), "-profile", str(profile_path), f"--class={app_id}", "--name", app_id, url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return 0

    def bridge_stop(self) -> int:
        output = self.client.stop()
        self._print_output(output)
        _cli().stop_module_bridge_processes(
            "surf_agent.backends.camoufox.bridge",
            port=self.agent.camoufox_port,
            profile_dir=self.agent.camoufox_profile_dir,
        )
        return 0


def _cli() -> Any:
    import surf_agent.cli as cli

    return cli

stable_camoufox_page_id = stable_local_page_id
