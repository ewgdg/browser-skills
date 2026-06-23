# Patchright backend

Patchright is experimental. Use it for Chrome-channel persistent-profile trials where AXI is not enough.

It may help Chrome-extension workflows, but extension behavior depends on the installed Chrome and profile state. Do not assume perfect 1Password support without a live smoke test.

## Setup

```bash
uv sync --extra patchright
uv run surf-agent setup patchright
```

`setup patchright` runs `python -m patchright install chrome`.

## Select backend

Persist Patchright:

```bash
uv run surf-agent backend set patchright
```

Use once without changing config:

```bash
SURF_AGENT_BACKEND=patchright uv run surf-agent --thread main open https://example.com
```

## Runtime data

- profile: `chrome-profile/` by default, shared with AXI because both are Chrome-family backends
- port env: `SURF_AGENT_PATCHRIGHT_PORT` default `9346`
- profile env: `SURF_AGENT_PATCHRIGHT_PROFILE_DIR` overrides the shared Chrome profile
- app/window env: `SURF_AGENT_PATCHRIGHT_APP_ID` or `SURF_AGENT_PATCHRIGHT_CLASS`

## Commands

Patchright supports these core commands through a local Python bridge:

```text
open, new, snapshot, text, click, fill, type, press, scroll, wait, back, screenshot, eval, close, focus, state, list
```

## Implementation notes

- Uses `patchright.sync_api.sync_playwright()`.
- Launches a persistent Chrome-channel context with `launch_persistent_context(..., channel="chrome", no_viewport=True)`.
- Uses `--name=<app_id>` flag form to avoid Chromium treating the app id as a page target.

## Limitations

- Chrome-extension behavior is profile/browser dependent; verify 1Password manually.
- `close-matching` is not implemented.
