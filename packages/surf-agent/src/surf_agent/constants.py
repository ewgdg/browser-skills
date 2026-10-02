from __future__ import annotations

from pathlib import Path

DEFAULT_THREAD = "default"
DEFAULT_COMMAND_TIMEOUT_S = 15.0
PATCHRIGHT_BACKEND = "patchright"
DEFAULT_PATCHRIGHT_PORT = "9346"
DEFAULT_PATCHRIGHT_APP_ID = "surf-agent"
# Patchright always launches the Chrome channel, so the destination profile is Chrome's.
PATCHRIGHT_BROWSER_FAMILY = "chrome"
SURF_AGENT_WINDOW_TITLE = "Surf Agent"
CHROME_NEW_WINDOW_TIMEOUT_S = 10.0
SNAPSHOT_DIFF_MAX_RATIO = 0.50
SNAPSHOT_DIFF_MIN_SAVED_CHARS = 250
SNAPSHOT_DIFF_MAX_HUNKS = 8
# Matches the former fixed text-wait timeout so `wait("text")` keeps its behavior.
DEFAULT_WAIT_TIMEOUT_MS = 10_000
# Search order for the default browser: Linux executable names on PATH, then the
# macOS app bundles, system-wide before per-user.
_MACOS_CHROME_BUNDLE = "Google Chrome.app/Contents/MacOS/Google Chrome"
CHROME_EXECUTABLE_CANDIDATES = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "brave-browser",
    "microsoft-edge",
    f"/Applications/{_MACOS_CHROME_BUNDLE}",
    str(Path.home() / "Applications" / _MACOS_CHROME_BUNDLE),
)
