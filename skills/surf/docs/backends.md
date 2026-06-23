# Surf backends

`surf-agent` supports one active browser backend at a time.

Selection priority:

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

## Backend docs

- [AXI backend](axi-backend.md) — default Chrome bridge backend.
- [Camoufox backend](camoufox-backend.md) — experimental Firefox/Camoufox backend.
- [Patchright backend](patchright-backend.md) — experimental Chrome-channel Patchright backend.
