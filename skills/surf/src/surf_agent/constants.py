from __future__ import annotations

from pathlib import Path

DEFAULT_THREAD = "default"
DEFAULT_AXI_BIN = "npx -y chrome-devtools-axi"
DEFAULT_AXI_TIMEOUT_S = 15.0
DEFAULT_AXI_PORT = "9335"
DEFAULT_CHROME_DEBUG_PORT = "9336"
DEFAULT_CHROME_CLASS = "surf-agent"
DEFAULT_BACKEND = "axi"
CAMOUFOX_BACKEND = "camoufox"
DEFAULT_CAMOUFOX_PORT = "9345"
DEFAULT_CAMOUFOX_APP_ID = "surf-agent"
CAMOUFOX_SETUP_STEPS = (
    ("sync",),
    ("set", "official/prerelease"),
    ("fetch",),
)
PATCHRIGHT_BACKEND = "patchright"
DEFAULT_PATCHRIGHT_PORT = "9346"
DEFAULT_PATCHRIGHT_APP_ID = "surf-agent"
PATCHRIGHT_SETUP_STEPS = (("install", "chrome"),)
AXI_STATE_DIR = Path.home() / ".chrome-devtools-axi"
AXI_BRIDGE_PID_FILE = AXI_STATE_DIR / "bridge.pid"
SURF_AGENT_WINDOW_TITLE = "Surf Agent"
CHROME_NEW_WINDOW_TIMEOUT_S = 10.0
SNAPSHOT_DIFF_MAX_RATIO = 0.50
SNAPSHOT_DIFF_MIN_SAVED_CHARS = 250
SNAPSHOT_DIFF_MAX_HUNKS = 8
