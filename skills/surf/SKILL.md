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

## Base command

Run from this skill directory:

```bash
uv run surf-agent --thread main <command>
```

Browser commands are passed through to `surf` inside the managed thread window.

## Starter workflows

### Click known UI

```bash
uv run surf-agent --thread main locate.role button --name "Sign in" --action click
uv run surf-agent --thread main page.state
```

### Read large pages cheaply

```bash
uv run surf-agent --thread main page.read --compact --depth 2
uv run surf-agent --thread main search "error"
```

## Command reference

### Session

```bash
uv run surf-agent --thread main state      # current thread/window/page state; removes stale cache; does not open a window
uv run surf-agent list                     # remembered threads; removes stale cache entries
uv run surf-agent --thread main new        # replace/create the thread window
uv run surf-agent --thread main close      # close remembered thread window
uv run surf-agent --thread main reset      # forget thread state without closing window
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

Close temporary sessions when done:

```bash
uv run surf-agent --thread main close
```


