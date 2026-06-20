---
name: surf
description: Browser control through `surf-agent`, backed by persistent chrome-devtools-axi and a dedicated skill-local Chrome profile. Use for browsing, testing, screenshots, forms, page inspection, and debugging without touching the user's main Chrome tabs.
---

# surf

## Backend policy

Use `surf-agent` for browser operations. Backend is AXI-only through the persistent `chrome-devtools-axi` bridge.

- One thread owns one remembered AXI page id in one dedicated Chrome window.
- New threads first open a short `Surf Agent` bootstrap in a normal `--new-window` Chrome window so human login/unblock has toolbar, back/forward, and extension controls. `new` then opens the welcome page; `go <url>` navigates directly to the requested URL.
- AXI uses a dedicated skill-local Chrome profile by default, so global AXI page listing only sees Surf Agent profile pages, not the user's main Chrome tabs.
- `surf-agent` talks to the AXI bridge over local HTTP for normal operations and embeds AXI profile defaults; callers do not need AXI env boilerplate.
- Keep the AXI bridge alive. Normal cleanup closes pages/windows only; it must not stop the bridge.
- First use may require Chrome approval. Approve once, then reuse the live bridge.
- Use `--thread` to select a page/window.
- Reuse a thread for one browsing task.
- Use unique thread ids for parallel agents unless intentionally sharing one page.
- Do not manage tabs directly through raw tab/window commands.
- If blocked, ask the user to handle it in Chrome, then resume.

Direct `surf` CLI fallback is removed. Unsupported legacy surf commands fail clearly instead of switching backends. Legacy `window-id`/`id` management aliases are removed; use `page-id`.

## AXI setup

`surf-agent` sets these AXI defaults internally for the bridge and the startup CLI fallback:

```bash
CHROME_DEVTOOLS_AXI_PORT=9335
CHROME_DEVTOOLS_AXI_BROWSER_URL=http://127.0.0.1:9336
```

`surf-agent` launches dedicated Chrome itself with `--user-data-dir=<skill-dir>/chrome-profile`, `--remote-debugging-port=9336`, and `--class=surf-agent`, then points AXI at that browser URL. Thread windows are normal Chrome `--new-window` windows with the same profile and `--class=surf-agent` for window-manager rules. Raw `--app=<url>` remains a possible future mode if a bare app shell is preferable to toolbar/extension UX. The profile lives under top-level `chrome-profile/` in this skill directory; that directory is git-ignored.

Optional overrides:

```bash
# AXI binary used only for bridge startup/stop fallback; default: npx -y chrome-devtools-axi
export SURF_AGENT_AXI_BIN="npx -y chrome-devtools-axi"
# Chrome launcher for dedicated windows; auto-detected when possible
export SURF_AGENT_CHROME_BIN="google-chrome"
# Dedicated profile directory; default: <skill-dir>/chrome-profile
export SURF_AGENT_CHROME_PROFILE_DIR="./chrome-profile"
# Linux window class; default: surf-agent
export SURF_AGENT_CHROME_CLASS="surf-agent"
# Dedicated Chrome remote debugging port; default 9336
export SURF_AGENT_CHROME_DEBUG_PORT=9336
# Hard timeout, seconds; default 15
export SURF_AGENT_AXI_TIMEOUT=15
```

If bridge is down or Chrome waits for approval, commands fail fast with a clear AXI error. Approve Chrome prompt, then retry. First use of the dedicated profile may require one-time browser setup/login. For setup without automation/debugging, close Surf Agent automation windows and run `uv run surf-agent profile open https://x.com`.

Only use explicit bridge stop when you intend to kill persistent bridge:

```bash
uv run surf-agent bridge-stop
```

After `bridge-stop`, next use may require Chrome approval again.

## Base command

Run from this skill directory:

```bash
uv run surf-agent --thread main <command>
```

## Starter workflows

### Open, read, and clean up

```bash
# `go` creates the thread window/page if missing; no separate `new` needed.
uv run surf-agent --thread main go https://example.com
uv run surf-agent --thread main page.read
uv run surf-agent --thread main close

# Bridge should still be alive.
npx -y chrome-devtools-axi pages
```

### Subagent fan-out cleanup

```bash
uv run surf-agent --thread run-42-a go https://example.com/a
uv run surf-agent --thread run-42-b go https://example.com/b

# Closes only remembered AXI pages matching thread glob. Does not call AXI stop.
uv run surf-agent close-matching 'run-42-*'
```

### Human-in-the-loop unblock

```bash
uv run surf-agent --thread main go https://x.com/explore
uv run surf-agent --thread main page.read || true
uv run surf-agent --thread main focus
```

Tell user: "Please complete blocker in Chrome, then tell me when done."

After user confirms:

```bash
uv run surf-agent --thread main page.read
```

## Command reference

### Session

```bash
uv run surf-agent --thread main state          # current thread/page state; does not open a page
uv run surf-agent list                         # remembered threads from local state; does not probe all Chrome pages
uv run surf-agent --thread main new            # replace/create dedicated thread window showing Surf Agent welcome page; prints page id
uv run surf-agent --thread main close          # close remembered thread page/window; bridge stays alive
uv run surf-agent --thread main focus          # select remembered thread page
uv run surf-agent profile show                 # print dedicated profile configuration
uv run surf-agent profile open [url]           # open dedicated profile without automation/debug port for manual login/setup
uv run surf-agent close-all                    # close all remembered thread pages/windows
uv run surf-agent close-matching 'run-*'       # close remembered pages/windows with matching thread names
uv run surf-agent --thread main reset          # forget state without closing page
uv run surf-agent --thread main page-id        # print/create managed AXI page id
uv run surf-agent bridge-stop                  # explicit destructive bridge stop
```

### Navigate and read

```bash
uv run surf-agent --thread main go https://example.com
uv run surf-agent --thread main open https://example.com/other
uv run surf-agent --thread main back
uv run surf-agent --thread main page.read
uv run surf-agent --thread main read
uv run surf-agent --thread main page.text
uv run surf-agent --thread main page.state
```

### Interact

```bash
uv run surf-agent --thread main click @uid
uv run surf-agent --thread main type "text"
uv run surf-agent --thread main key Enter
uv run surf-agent --thread main press Enter
uv run surf-agent --thread main scroll down
uv run surf-agent --thread main scroll up
uv run surf-agent --thread main wait 1000
```

### Diagnostics

```bash
uv run surf-agent --thread main screenshot --output /tmp/shot.png
uv run surf-agent --thread main js "document.title"
```

Unsupported legacy surf flags fail clearly. Direct surf fallback is not available.

Forbidden through `surf-agent`: web chat/client commands such as `chatgpt` and `ai`, direct tab commands, and direct `window.new`. Use thread/session commands instead.

## Recovery

Symptoms and fixes:

- `AXI command timed out... Bridge may be down or waiting for Chrome approval.`
  Approve Chrome prompt, confirm bridge with `npx -y chrome-devtools-axi pages`, retry.
- `remembered AXI page <id> is gone; state forgotten`
  Page closed outside agent. Run `go <url>` again.
- `could not parse AXI pages output`
  AXI output format changed. Capture short output and update parser/tests.

## Session cleanup

Close temporary sessions when done:

```bash
uv run surf-agent --thread main close
uv run surf-agent close-matching 'run-42-*'
uv run surf-agent close-all
```

Cleanup closes remembered AXI pages only. It never calls `chrome-devtools-axi stop`. Use `reset` only when you intentionally want to forget state and leave the page open.
