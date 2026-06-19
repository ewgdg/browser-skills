---
name: surf
description: Browser control through an agent-owned Chrome window. Use for browsing, testing, screenshots, forms, page inspection, network/perf checks, emulation, and debugging.
---

# surf

## Browser/window policy

Use `surf-agent` for all browser operations. It owns a Chrome window per thread and prevents accidental control of user-owned browser windows.

- One thread owns one browser window with one tab.
- Use `--thread` to select a window.
- Reuse a thread for one browsing task.
- Use a new thread for independent work.
- Parallel agents must use unique thread ids unless intentionally sharing one window.
- Do not manage tabs directly.
- Do not operate on user-owned browser windows.
- If login, CAPTCHA, consent, FedCM, or other anti-automation UI blocks progress, stop and ask the user to handle it in the managed window, then resume after they confirm.
- Close temporary sessions when done; `reset` only forgets state and can leave a window open.
- For subagent fan-out, use a unique thread prefix per run (for example `review-42-a`) and sweep it with `close-matching 'review-42-*'`.

## Base command

Run from this skill directory:

```bash
uv run surf-agent --thread main <command>
```

Browser commands are passed through to `surf` inside the managed thread window.

## Starter workflows

### Open, read, and clean up

```bash
# `go` creates the thread window if missing; no separate `new` needed.
uv run surf-agent --thread main go https://example.com
uv run surf-agent --thread main page.read --compact --depth 2
uv run surf-agent --thread main close
```

### Subagent fan-out cleanup

```bash
# Each subagent gets one unique thread under a run prefix.
uv run surf-agent --thread run-42-a go https://example.com/a
uv run surf-agent --thread run-42-b go https://example.com/b

# Parent/subagent cleanup closes only remembered surf-agent windows matching the thread glob.
uv run surf-agent close-matching 'run-42-*'
```

### Click known UI

```bash
uv run surf-agent --thread main go https://example.com
uv run surf-agent --thread main locate.role button --name "Sign in" --action click
uv run surf-agent --thread main page.state
```

### Human-in-the-loop unblock

```bash
uv run surf-agent --thread main go https://x.com/explore
# If blocked by login/CAPTCHA/consent/FedCM, ask the user to complete it in the managed window.
uv run surf-agent --thread main wait 2
uv run surf-agent --thread main page.read --compact --depth 2
```

### Read large pages cheaply

```bash
uv run surf-agent --thread main go https://example.com
uv run surf-agent --thread main page.read --compact --depth 2
uv run surf-agent --thread main search "error"
```

## Command reference

### Session

```bash
uv run surf-agent --thread main state      # current thread/window/page state; removes stale cache; does not open a window
uv run surf-agent list                     # remembered threads; removes stale cache entries
uv run surf-agent --thread main new        # replace/create the thread window; use only when forcing a fresh window
uv run surf-agent --thread main close      # close remembered thread window; use for cleanup
uv run surf-agent close-all                # close all remembered thread windows
uv run surf-agent close-matching 'run-*'   # close remembered thread windows with matching thread names
uv run surf-agent --thread main reset      # forget thread state without closing window; can leave orphan windows
uv run surf-agent --thread main window-id  # print/create managed window id
```

### Navigate and read

```bash
uv run surf-agent --thread main go https://example.com  # creates/reuses thread window automatically
uv run surf-agent --thread main back
uv run surf-agent --thread main forward
uv run surf-agent --thread main page.read --compact --depth 3
uv run surf-agent --thread main page.text
uv run surf-agent --thread main page.state
uv run surf-agent --thread main search "text"
```

### Interact

```bash
uv run surf-agent --thread main locate.role button --name "Submit"
uv run surf-agent --thread main click e3
uv run surf-agent --thread main type "text"
uv run surf-agent --thread main type "text" --submit
uv run surf-agent --thread main key Enter
uv run surf-agent --thread main scroll down
uv run surf-agent --thread main wait 2
uv run surf-agent --thread main wait.element "#app"
uv run surf-agent --thread main wait.network
```

### Diagnostics

```bash
uv run surf-agent --thread main screenshot --output /tmp/shot.png
uv run surf-agent --thread main js "document.title"
uv run surf-agent --thread main console
uv run surf-agent --thread main network
uv run surf-agent --thread main network.get <id>
uv run surf-agent --thread main perf.metrics
uv run surf-agent --thread main perf.start
uv run surf-agent --thread main perf.stop
uv run surf-agent --thread main emulate.device "iPhone 14"
uv run surf-agent --thread main emulate.viewport --width 390 --height 844
```

Forbidden through `surf-agent`: web chat/client commands such as `chatgpt` and `ai`, direct tab commands, and direct `window.new`. Use thread/session commands instead.

## Session cleanup

Close temporary sessions when done. Do not use `reset` for cleanup unless you intentionally want to leave the browser window open but forget agent state.

Use `close-matching` for batch cleanup. It matches remembered thread names, not page titles or arbitrary Chrome windows, so it should not close user-owned windows.

```bash
uv run surf-agent --thread main close
uv run surf-agent close-matching 'run-42-*'
uv run surf-agent close-all
```


