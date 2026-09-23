# AXI backend

Read this when enabling AXI or diagnosing its bridge, debug port, or profile settings. AXI is an explicitly selected alternative to Patchright. Select it as [backend selection](backends.md) describes, then run `Browser().setup()`; selection is complete when `Browser().backend()` reports `axi`.

Surf launches dedicated Chrome with a platform user-data profile, remote debugging, and normal windows with toolbar and extension controls. `SURF_AGENT_HOME` collects config, thread records, and profiles under one directory.

## Overrides

```bash
# Bridge startup/stop helper; default shown
export SURF_AGENT_AXI_BIN="npx -y chrome-devtools-axi"
# Chrome executable, normally auto-detected
export SURF_AGENT_CHROME_BIN="google-chrome"
# Dedicated profile override
export SURF_AGENT_CHROME_PROFILE_DIR="$HOME/.local/share/surf-agent/profiles/chrome"
# Linux window class
export SURF_AGENT_CHROME_CLASS="surf-agent"
# Dedicated Chrome debugging port
export SURF_AGENT_CHROME_DEBUG_PORT=9336
# Hard timeout in seconds
export SURF_AGENT_AXI_TIMEOUT=15
```

Internal AXI defaults are `CHROME_DEVTOOLS_AXI_PORT=9335` and `CHROME_DEVTOOLS_AXI_BROWSER_URL=http://127.0.0.1:9336`. Normal browser operations use the local HTTP bridge.

If the bridge or debug port is unavailable, operations raise a browser-control error. `Browser().stop_bridge()` stops both the bridge and its automation-owned Chrome process. Use the main skill's [manual login](../SKILL.md#login-and-human-unblock) and [recovery](../SKILL.md#recovery) workflows.

## Cookies and idle stop

Read [cookie import](cookie-import.md) before enabling source access. `CHROME_DEVTOOLS_AXI_AUTO_CONNECT=1` or an explicit `CHROME_DEVTOOLS_AXI_BROWSER_URL` makes destination identity unprovable; configured cookie import fails closed under either override. Remove the override, or disable imports only with user intent.

After the final user-visible page closes, AXI re-lists pages after two seconds and stops the idle bridge only if none remain.
