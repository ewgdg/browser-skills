---
name: surf
description: Browser control through `surf-agent`, backed by a persistent browser bridge and a dedicated skill-local Chrome profile. Use for browsing, testing, screenshots, forms, page inspection, and debugging without touching the user's main Chrome tabs.
---

# surf

## Backend policy

Use `surf-agent` for browser operations. Current default implementation uses a persistent AXI browser bridge with a dedicated Chrome profile.

Optional experimental Firefox/Camoufox backend exists for high-fingerprint-resistance trials:

```bash
uv run surf-agent backend set camoufox
uv run surf-agent --thread main open https://example.com
```

Optional experimental Chrome/Patchright backend exists for persistent Chrome-profile trials:

```bash
uv sync --extra patchright
uv run surf-agent backend set patchright
uv run surf-agent --thread main open https://example.com
```

For one command only, env override still wins:

```bash
SURF_AGENT_BACKEND=camoufox uv run surf-agent --thread main open https://example.com
SURF_AGENT_BACKEND=patchright uv run surf-agent --thread main open https://example.com
```

Setup first:

```bash
uv sync --extra camoufox
uv sync --extra patchright
uv run surf-agent setup camoufox
uv run surf-agent setup patchright
```

`setup camoufox` runs `python -m camoufox sync`, selects `official/prerelease`, then fetches the browser binary without launching it. `setup patchright` runs `python -m patchright install chrome`.

Camoufox backend supports core browsing commands (`open`, `new`, `snapshot`, `text`, `click`, `fill`, `type`, `press`, `scroll`, `wait`, `back`, `screenshot`, `eval`, `close`, `focus`, `state`, `list`) through a persistent local Python bridge and `camoufox-profile/`. It is experimental: Chrome extensions/profile behavior does not apply, and `close-matching` is not implemented yet. Patchright backend uses the same core commands through a persistent Chrome-channel context and `patchright-profile/`; profile reuse and extension behavior depend on the existing Chrome install, so do not assume perfect extension support.

Persistent app-local data lives under `.surf-agent/`: backend config in `.surf-agent/config.json`, thread state in `.surf-agent/state/`.

- One thread owns one remembered browser page id in one dedicated Chrome window.
- New threads first open a short `Surf Agent` bootstrap in a normal `--new-window` Chrome window so human login/unblock has toolbar, back/forward, and extension controls. `new` then opens the welcome page; `open <url>` navigates directly to the requested URL.
- The browser backend uses a dedicated skill-local Chrome profile by default, so backend page listing only sees Surf Agent profile pages, not the user's main Chrome tabs.
- `surf-agent` talks to the browser bridge over local HTTP for normal operations and embeds browser profile defaults.
- Keep the browser bridge alive. Normal cleanup closes pages/windows only; it must not stop the bridge.
- Use `--thread` to select a page/window.
- Reuse a thread for one browsing task.
- Use unique thread ids for parallel agents unless intentionally sharing one page.
- Do not manage tabs directly through raw tab/window commands.
- If blocked, ask the user to handle it in Chrome, then resume.

Direct `surf` CLI fallback is removed. Unsupported commands fail clearly instead of switching backends.

## Backend details

Normal callers should use `surf-agent` commands only. Current AXI backend/env details live in [docs/axi-backend.md](docs/axi-backend.md). Backend selection priority is `SURF_AGENT_BACKEND`, then `.surf-agent/config.json`, then AXI default.

First use of the dedicated profile may require one-time browser setup/login. For setup without automation/debugging, close Surf Agent automation windows and run `uv run surf-agent profile open https://x.com`.

Only use explicit bridge stop when you intend to kill the persistent browser bridge:

```bash
uv run surf-agent bridge-stop
```

## Base command

Run from this skill directory:

```bash
uv run surf-agent --thread main <command>
```

## Starter workflows

### Open, inspect, and clean up

```bash
# `open` creates the thread window/page if missing; no separate `new` needed.
uv run surf-agent --thread main open https://example.com
uv run surf-agent --thread main snapshot
uv run surf-agent --thread main close

# Thread state should still be remembered.
uv run surf-agent list
```

### Subagent fan-out cleanup

```bash
uv run surf-agent --thread run-42-a open https://example.com/a
uv run surf-agent --thread run-42-b open https://example.com/b

# Closes only remembered browser pages matching thread glob. Does not call bridge stop.
uv run surf-agent close-matching 'run-42-*'
```

### Human-in-the-loop unblock

```bash
uv run surf-agent --thread main open https://x.com/explore
uv run surf-agent --thread main snapshot || true
uv run surf-agent --thread main focus
```

Tell user: "Please complete blocker in Chrome, then tell me when done."

After user confirms:

```bash
uv run surf-agent --thread main snapshot
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
uv run surf-agent --thread main reset          # clear state without closing page
uv run surf-agent --thread main page-id        # print/create managed browser page id
uv run surf-agent bridge-stop                  # explicit destructive bridge stop
```

### Navigate and inspect

```bash
uv run surf-agent --thread main open https://example.com
uv run surf-agent --thread main back
uv run surf-agent --thread main snapshot        # full snapshot; no hidden baseline
uv run surf-agent --thread main snapshot --diff # full snapshot with no-baseline fallback outside do
uv run surf-agent --thread main text
uv run surf-agent --thread main state
```

`snapshot --baseline` is valid only inside `do`.

### Interact

```bash
uv run surf-agent --thread main click @uid
uv run surf-agent --thread main fill @uid "text"
uv run surf-agent --thread main type "text"
uv run surf-agent --thread main press Enter
uv run surf-agent --thread main scroll down
uv run surf-agent --thread main scroll top
uv run surf-agent --thread main wait 1000
uv run surf-agent --thread main wait "Loaded"
```

### Compose with `do`

`do` composes one command per stdin line in the current thread. It is fail-fast. Non-final step output is suppressed unless the step has `--emit`; final step output is printed unless it has `--quiet`. A single emitted step prints raw output. Multiple emitted steps are separated with fenced `surf-step` blocks.

Snapshot modes inside one `do` invocation:

- `snapshot`: full snapshot; does not set a baseline.
- `snapshot --baseline`: captures baseline and emits no output.
- `snapshot --diff`: compares current snapshot to current `do` baseline, then updates baseline to current.
- Baseline lives only for one `do` invocation. No persistent baseline state.
- Diff is auto-gated. If diff is too large, saves too few chars, has too many hunks, or page identity/origin changes, output falls back to full snapshot with compact reason. No force mode.

Recommended diff pattern: capture baseline, perform one or more actions, then ask for `snapshot --diff`. Use it when the action should only affect a small part of the page.

```bash
uv run surf-agent --thread main do <<'EOF'
open https://example.com
snapshot
EOF

uv run surf-agent --thread main do <<'EOF'
open https://example.com
snapshot --baseline
click @button
snapshot --diff
EOF

uv run surf-agent --thread main do --jsonl <<'EOF'
open https://example.com --emit
snapshot
EOF
```

Use `do -` explicitly when helpful. Prefer stdin/heredoc for `do`. One-line composition with `::` or `--then` remains available for simple commands only; avoid it when quoting, long text, or flags are involved. In stdin scripts, only full-line comments are ignored; URL fragments and literal `#` stay intact. Within a step, `--` makes later tokens literal so command args can include `--emit` or `--quiet`.

```bash
uv run surf-agent --thread main do open https://example.com :: snapshot
uv run surf-agent --thread main do type -- --emit
```

### Diagnostics

```bash
uv run surf-agent --thread main screenshot --output /tmp/shot.png
uv run surf-agent --thread main eval "document.title"
```

Unsupported commands fail clearly. Direct surf fallback is not available.

Forbidden through `surf-agent`: web chat/client commands such as `chatgpt` and `ai`, direct tab commands, and direct `window.new`. Use thread/session commands instead.

## Recovery

Symptoms and fixes:

- `browser command timed out... browser bridge may be unavailable.`
  Retry once; if it persists, restart the browser bridge with `uv run surf-agent bridge-stop`, then rerun the command.
- `remembered browser page <id> is gone; state cleared`
  Page closed outside agent. Run `open <url>` again.
- `could not parse browser pages output`
  Browser page-list output format changed. Capture short output and update parser/tests.

## Session cleanup

Close temporary sessions when done:

```bash
uv run surf-agent --thread main close
uv run surf-agent close-matching 'run-42-*'
uv run surf-agent close-all
```

Cleanup closes remembered browser pages only. It never stops the browser bridge. Use `reset` only when you intentionally want to clear state and leave the page open.
