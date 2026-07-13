# AXI backend details

`surf-agent` sets these AXI defaults internally for the bridge and the startup CLI fallback:

```bash
CHROME_DEVTOOLS_AXI_PORT=9335
CHROME_DEVTOOLS_AXI_BROWSER_URL=http://127.0.0.1:9336
```

`surf-agent` launches dedicated Chrome itself with a platform user-data profile, `--remote-debugging-port=9336`, and `--class=surf-agent`, then points AXI at that browser URL. Thread windows are normal Chrome `--new-window` windows with the same profile and `--class=surf-agent` for window-manager rules. Raw `--app=<url>` remains a possible future mode if a bare app shell is preferable to toolbar/extension UX. Set `SURF_AGENT_HOME` to keep config, threads, and profiles under one chosen directory; run `surf-agent profile show` to inspect actual paths.

Optional overrides:

```bash
# AXI binary used only for bridge startup/stop fallback; default: npx -y chrome-devtools-axi
export SURF_AGENT_AXI_BIN="npx -y chrome-devtools-axi"
# Chrome launcher for dedicated windows; auto-detected when possible
export SURF_AGENT_CHROME_BIN="google-chrome"
# Dedicated profile directory; defaults to platform user data dir or $SURF_AGENT_HOME/profiles/chrome
export SURF_AGENT_CHROME_PROFILE_DIR="$HOME/.local/share/surf-agent/profiles/chrome"
# Linux window class; default: surf-agent
export SURF_AGENT_CHROME_CLASS="surf-agent"
# Dedicated Chrome remote debugging port; default 9336
export SURF_AGENT_CHROME_DEBUG_PORT=9336
# Hard timeout, seconds; default 15
export SURF_AGENT_AXI_TIMEOUT=15
```

If the bridge or dedicated Chrome debug port is unavailable, commands fail fast with a clear browser-control error. First use of the dedicated profile may require one-time browser setup/login. For setup without automation/debugging, close Surf Agent automation windows and run `surf-agent profile open https://x.com`.

Only use explicit bridge stop when you intend to kill the persistent browser bridge and its automation-owned dedicated Chrome process:

```bash
surf-agent bridge stop
```

After `bridge stop`, next use restarts the persistent bridge and dedicated debug-port Chrome if needed.


## Live cookie refresh

After explicit `profile cookie-source set` consent, AXI refreshes selected cookies before it starts Surf's inactive dedicated Chrome profile when the source fingerprint changed. The source Chrome may remain running; Surf uses SQLite online backup. The source must be the same Chrome family and OS user with equal `Local State.os_crypt` metadata. Imports are upsert-only, so source-absent cookies are not deleted from Surf.

`CHROME_DEVTOOLS_AXI_AUTO_CONNECT=1` and an explicit `CHROME_DEVTOOLS_AXI_BROWSER_URL` make Surf's destination identity unprovable. When cookie import is configured, startup fails closed under either override; remove the override or disable/reset cookie import. When the final user-visible page closes, AXI re-lists pages after two seconds and stops the idle bridge only if none remain.
