---
name: surf
description: Browser control through an agent-owned Chrome window. Use for browsing, testing, screenshots, forms, page inspection, network/perf checks, emulation, and debugging.
---

# surf

## Browser ownership policy

Use `surf-agent` for all browser operations. It owns a Chrome window per thread and prevents accidental control of user-owned browser windows.

- One thread owns one browser window.
- One agent-owned window has one tab.
- Use `--thread` to select a window.
- Reuse the same thread for the same browsing task.
- Use different thread ids for independent browser work.
- Parallel agents must use unique thread ids unless intentionally sharing one window.
- Do not manage tabs directly.
- Do not operate on user-owned browser windows.
- Close temporary sessions when done.

## Base command

Run from this skill directory:

```bash
uv run surf-agent --thread main <command>
```

`surf-agent` manages the window/thread, then forwards browser commands to `surf` in that window. Use `uv run surf-agent --help` for wrapper help and `surf --help` / `surf --help-full` for forwarded command details.

## Quick start

```bash
uv run surf-agent --thread main state
uv run surf-agent --thread main go https://example.com
uv run surf-agent --thread main page.read --compact --depth 3
uv run surf-agent --thread main close
```

## Thread/window model

Thread names are skill-local state keys stored under `.state/<thread>.json`.

```bash
uv run surf-agent --thread main go https://example.com
uv run surf-agent --thread docs go https://docs.example.com
uv run surf-agent --thread docs page.read --compact
uv run surf-agent list
```

Before acting on an uncertain session, inspect it:

```bash
uv run surf-agent --thread main state
```

Use stable, specific names for parallel work, e.g. `review-a`, `run-123-docs`, or an agent/run id.

## Common workflows

### Inspect a page

```bash
uv run surf-agent --thread main go https://example.com
uv run surf-agent --thread main page.read --compact --depth 3
uv run surf-agent --thread main page.text
```

### Click known UI

```bash
uv run surf-agent --thread main page.read --compact --depth 3
uv run surf-agent --thread main locate.role button --name "Sign in" --action click
uv run surf-agent --thread main wait 1
uv run surf-agent --thread main page.state
```

### Large page / low tokens

```bash
uv run surf-agent --thread main page.read --compact --depth 2
uv run surf-agent --thread main page.read --no-text
uv run surf-agent --thread main search "error"
uv run surf-agent --thread main page.text | head -200
```

### Screenshot/debug flow

```bash
uv run surf-agent --thread main screenshot --output /tmp/surf-shot.png
uv run surf-agent --thread main console
uv run surf-agent --thread main network
uv run surf-agent --thread main perf.metrics
```

### Parallel browser work

```bash
uv run surf-agent --thread agent-a go https://example.com/a
uv run surf-agent --thread agent-b go https://example.com/b
uv run surf-agent list
```

## Command reference

This is the canonical command list for this skill. Prefer these commands before reaching for lower-level `surf` help.

### Session

```bash
uv run surf-agent --thread main state      # current thread/window/page state; does not open a window
uv run surf-agent list                     # remembered threads; removes stale cache entries
uv run surf-agent --thread main new        # replace/create the thread window
uv run surf-agent --thread main close      # close remembered thread window
uv run surf-agent --thread main reset      # forget thread state without closing the browser window
uv run surf-agent --thread main window-id  # print/create managed window id
```

### Navigate/read

```bash
uv run surf-agent --thread main go https://example.com
uv run surf-agent --thread main back
uv run surf-agent --thread main forward
uv run surf-agent --thread main page.read --compact --depth 3
uv run surf-agent --thread main page.text
uv run surf-agent --thread main page.state
uv run surf-agent --thread main search "text"
```

### Act

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

### Capture/debug

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

Forbidden through `surf-agent`: AI helper commands, direct tab commands, and direct `window.new`. Use thread/session commands instead.

## Output formats

`state` prints JSON. Missing and stale windows both report as not open; stale cache is removed silently.

```json
{"open":false,"thread":"main"}
```

With an open window:

```json
{"open":true,"tab_id":123,"thread":"main","title":"Example","url":"https://example.com/","window_id":456}
```

`list` prints JSON with active `threads`. Stale entries are removed silently.

```json
{"threads":[{"open":true,"tab_id":123,"thread":"main","title":"Example","url":"https://example.com/","window_id":456}]}
```

Forwarded `surf` command output varies by command. Inspect command-specific help when exact shape matters:

```bash
surf <command> --help
```

## Session cleanup

Close temporary sessions when done:

```bash
uv run surf-agent --thread main close
uv run surf-agent list
```

Use `reset` only when state is wrong and you intentionally want to forget the managed window without closing it.

## Troubleshooting

- Command missing or syntax unclear: `uv run surf-agent --help`, then `surf --help-full` or `surf <command> --help`.
- Stale or unknown page: run `uv run surf-agent --thread main state`.
- Wrong thread/window: run `uv run surf-agent list` and choose the right `--thread`.
- Browser stuck: `uv run surf-agent --thread main close`, then retry; use `new` if you need a fresh window.
- Selector/ref missing: run `page.read --compact --depth 3`, `search "text"`, or take a screenshot before acting.
- Page not ready: use `wait`, `wait.element`, `wait.network`, `wait.url`, or `wait.load` before reading/clicking.

## Validation checklist

Before reporting browser results, verify:

- correct thread and URL
- page has loaded or target element is visible/found
- action result was checked with `state`, `page.read`, `page.text`, screenshot, or relevant logs
- screenshots/logs were saved only when useful
- temporary sessions were closed
