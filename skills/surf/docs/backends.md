# Surf backends

`surf-agent` supports one active browser backend at a time.

Selection priority:

1. `SURF_AGENT_BACKEND`
2. persisted platform user config (`surf-agent backend show` prints path)
3. default `axi`

Use `surf-agent backend show` to inspect the selected backend and source.

## Backend selection

Persist a backend:

```bash
surf-agent backend set axi
surf-agent backend set camoufox
surf-agent backend set patchright
```

Changing the persisted backend best-effort stops the previously selected backend runtime first, including old bridge processes and their automation-owned browser process. This prevents shared-profile lock conflicts when moving between AXI and Patchright.

Use one backend for one command without changing config:

```bash
SURF_AGENT_BACKEND=patchright surf-agent --thread main open https://example.com
```

Clear persisted backend:

```bash
surf-agent backend reset
```

## Backend docs

- [AXI backend](axi-backend.md) — default Chrome bridge backend.
- [Camoufox backend](camoufox-backend.md) — experimental Firefox/Camoufox backend.
- [Patchright backend](patchright-backend.md) — experimental Chrome-channel Patchright backend.
