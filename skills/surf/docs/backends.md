# Surf backend details

`surf-agent` supports one active browser backend at a time. Selection priority:

1. `SURF_AGENT_BACKEND`
2. `.surf-agent/config.json`
3. default `axi`

Use `surf-agent backend show` to inspect the selected backend and source.

## Backend selection

Persist a backend:

```bash
uv run surf-agent backend set axi
uv run surf-agent backend set camoufox
uv run surf-agent backend set patchright
```

Use one backend for one command without changing config:

```bash
SURF_AGENT_BACKEND=patchright uv run surf-agent --thread main open https://example.com
```

Clear persisted backend:

```bash
uv run surf-agent backend reset
```

## AXI backend

`axi` is the default backend. It uses a dedicated skill-local Chrome profile and a persistent browser bridge. Details live in [axi-backend.md](axi-backend.md).

No optional Python package setup is required for normal AXI use.

## Camoufox backend

Camoufox is experimental. Use it for Firefox/Camoufox fingerprint-resistance trials, not for Chrome-extension workflows.

Setup:

```bash
uv sync --extra camoufox
uv run surf-agent setup camoufox
```

`setup camoufox` runs `python -m camoufox sync`, selects `official/prerelease`, then fetches the browser binary without launching it.

Runtime data:

- profile: `camoufox-profile/`
- port env: `SURF_AGENT_CAMOUFOX_PORT` default `9345`
- profile env: `SURF_AGENT_CAMOUFOX_PROFILE_DIR`
- app/window env: `SURF_AGENT_CAMOUFOX_APP_ID` or `SURF_AGENT_CAMOUFOX_CLASS`

Limitations:

- Chrome extensions/profile behavior does not apply.
- `close-matching` is not implemented.

## Patchright backend

Patchright is experimental. Use it for Chrome-channel persistent-profile trials where AXI is not enough. It may help Chrome-extension workflows, but extension behavior depends on the installed Chrome and profile state; do not assume perfect 1Password support without a live smoke test.

Setup:

```bash
uv sync --extra patchright
uv run surf-agent setup patchright
```

`setup patchright` runs `python -m patchright install chrome`.

Runtime data:

- profile: `patchright-profile/`
- port env: `SURF_AGENT_PATCHRIGHT_PORT` default `9346`
- profile env: `SURF_AGENT_PATCHRIGHT_PROFILE_DIR`
- app/window env: `SURF_AGENT_PATCHRIGHT_APP_ID` or `SURF_AGENT_PATCHRIGHT_CLASS`

Implementation notes:

- Uses `patchright.sync_api.sync_playwright()`.
- Launches a persistent Chrome-channel context with `launch_persistent_context(..., channel="chrome", no_viewport=True)`.
- Uses `--name=<app_id>` flag form to avoid Chromium treating the app id as a page target.
- `close-matching` is not implemented.

## Shared optional-backend command support

Camoufox and Patchright support these core commands through a local Python bridge:

```text
open, new, snapshot, text, click, fill, type, press, scroll, wait, back, screenshot, eval, close, focus, state, list
```

Unsupported commands fail clearly instead of switching backends.
