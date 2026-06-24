# AXI backend details

`surf-agent` sets these AXI defaults internally for the bridge and the startup CLI fallback:

```bash
CHROME_DEVTOOLS_AXI_PORT=9335
CHROME_DEVTOOLS_AXI_BROWSER_URL=http://127.0.0.1:9336
```

`surf-agent` launches dedicated Chrome itself with `--user-data-dir=<skill-dir>/.surf-agent/profiles/chrome`, `--remote-debugging-port=9336`, and `--class=surf-agent`, then points AXI at that browser URL. Thread windows are normal Chrome `--new-window` windows with the same profile and `--class=surf-agent` for window-manager rules. Raw `--app=<url>` remains a possible future mode if a bare app shell is preferable to toolbar/extension UX. The profile lives under `.surf-agent/profiles/chrome/` with other skill-local runtime data.

Optional overrides:

```bash
# AXI binary used only for bridge startup/stop fallback; default: npx -y chrome-devtools-axi
export SURF_AGENT_AXI_BIN="npx -y chrome-devtools-axi"
# Chrome launcher for dedicated windows; auto-detected when possible
export SURF_AGENT_CHROME_BIN="google-chrome"
# Dedicated profile directory; default: <skill-dir>/.surf-agent/profiles/chrome
export SURF_AGENT_CHROME_PROFILE_DIR="./.surf-agent/profiles/chrome"
# Linux window class; default: surf-agent
export SURF_AGENT_CHROME_CLASS="surf-agent"
# Dedicated Chrome remote debugging port; default 9336
export SURF_AGENT_CHROME_DEBUG_PORT=9336
# Hard timeout, seconds; default 15
export SURF_AGENT_AXI_TIMEOUT=15
```

If the bridge or dedicated Chrome debug port is unavailable, commands fail fast with a clear browser-control error. First use of the dedicated profile may require one-time browser setup/login. For setup without automation/debugging, close Surf Agent automation windows and run `uv run surf-agent profile open https://x.com`.

Only use explicit bridge stop when you intend to kill the persistent browser bridge:

```bash
uv run surf-agent bridge-stop
```

After `bridge-stop`, next use restarts the persistent bridge and dedicated debug-port Chrome if needed.
