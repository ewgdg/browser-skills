---
name: surf
description: Generic browser control through an agent-owned Chrome window. Use for browsing, testing, screenshots, forms, page inspection, network/perf checks, emulation, and debugging.
---

# surf

Generic browser automation.

## Policy

Use `surf-agent` for all browser operations.

- One thread owns one browser window.
- Use `--thread` to select a window.
- Use separate thread ids for separate windows.
- One tab per window.
- Do not manage tabs directly.
- Do not operate on user-owned browser windows.

## Usage

Run from this skill directory:

```bash
uv run surf-agent --thread main state   # read-only: current window/url/title, cleans stale cache, does not open a window
uv run surf-agent list                  # list remembered threads, remove stale cache entries
uv run surf-agent --thread main go https://example.com
uv run surf-agent --thread main page.read --compact --depth 3
uv run surf-agent --thread main click e5
uv run surf-agent --thread main type "hello"
uv run surf-agent --thread main screenshot --output /tmp/surf-shot.png
uv run surf-agent --thread main close
```

Thread selection:

```bash
uv run surf-agent --thread main go https://example.com
uv run surf-agent --thread docs go https://docs.example.com
uv run surf-agent --thread docs page.read --compact
uv run surf-agent --thread docs state
uv run surf-agent list
```

Use same thread to continue same browsing task. Use different thread for independent browser work.

Thread names are shared state keys. Parallel agents must use different threads unless they intentionally share one browser window. For concurrent work, include a unique agent or run id in the thread name.

## Common workflow

```bash
uv run surf-agent --thread main go https://example.com
uv run surf-agent --thread main page.read --compact --depth 3
uv run surf-agent --thread main locate.role button --name "Sign in" --action click
uv run surf-agent --thread main wait 1
uv run surf-agent --thread main page.text
```

For large pages, prefer token-light reads:

```bash
uv run surf-agent --thread main page.read --compact --depth 2
uv run surf-agent --thread main page.read --no-text
uv run surf-agent --thread main search "error"
uv run surf-agent --thread main page.text | head -200
```

## Useful generic commands

```bash
uv run surf-agent --thread main go https://example.com
uv run surf-agent --thread main page.read --compact
uv run surf-agent --thread main page.text
uv run surf-agent --thread main page.state
uv run surf-agent --thread main click e3
uv run surf-agent --thread main type "text"
uv run surf-agent --thread main key Enter
uv run surf-agent --thread main scroll down
uv run surf-agent --thread main wait 2
uv run surf-agent --thread main wait.element "#app"
uv run surf-agent --thread main screenshot --output /tmp/shot.png
uv run surf-agent --thread main js "document.title"
uv run surf-agent --thread main network.list
uv run surf-agent --thread main perf.trace --duration 5
uv run surf-agent --thread main emulate.device "iPhone 14"
```

## If command unavailable

Stop and report that `surf-agent` is unavailable.
