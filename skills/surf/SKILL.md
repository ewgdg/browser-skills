---
name: surf
description: Browser control through `surf-agent`, backed by a persistent browser bridge and a dedicated skill-local Chrome profile. Use for browsing, testing, screenshots, forms, page inspection, and debugging without touching the user's main Chrome tabs.
---

# surf

## Backend policy

Use `surf-agent` for browser operations. Default backend is `axi`, backed by a persistent AXI bridge and a dedicated skill-local Chrome profile.

Optional backends exist, but stay opt-in:

- `camoufox`: experimental Firefox/Camoufox fingerprint-resistance trials.
- `patchright`: experimental Chrome-channel persistent-profile trials.

Backend selection priority: `SURF_AGENT_BACKEND`, then `.surf-agent/config.json`, then `axi` default. Backend docs: [overview](docs/backends.md), [AXI](docs/axi-backend.md), [Camoufox](docs/camoufox-backend.md), [Patchright](docs/patchright-backend.md).


## Operating rules

Persistent app-local data lives under `.surf-agent/`: backend config in `.surf-agent/config.json`, thread state in `.surf-agent/threads/`, browser profiles in `.surf-agent/profiles/`.

- One thread owns one remembered browser page id in one dedicated Chrome window.
- New threads first open a short `Surf Agent` bootstrap in a normal `--new-window` Chrome window so human login/unblock has toolbar, back/forward, and extension controls. `new` then opens the welcome page; `open <url>` navigates directly to the requested URL.
- The default browser backend uses a dedicated skill-local Chrome profile, so backend page listing only sees Surf Agent profile pages, not the user's main Chrome tabs.
- `surf-agent` talks to the browser bridge over local HTTP for normal operations and embeds browser profile defaults.
- Keep the browser bridge alive. Normal cleanup closes pages/windows only; it must not stop the bridge.
- Use `--thread` to select a page/window.
- Reuse a thread for one browsing task.
- Use unique thread ids for parallel agents unless intentionally sharing one page.
- Do not manage tabs directly through raw tab/window commands.
- If blocked, ask the user to handle it in Chrome, then resume.

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
uv run surf-agent --thread main screenshot --full-page --output /tmp/full-page.png
uv run surf-agent --thread main eval "document.title"
printf 'document.title' | uv run surf-agent --thread main eval --stdin
uv run surf-agent --thread main eval --file /tmp/script.js
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
