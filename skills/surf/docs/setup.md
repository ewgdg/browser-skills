# Surf setup

Run commands from `skills/surf/` unless noted.

Default AXI use needs no optional Python extra.

## Optional backend setup

Camoufox:

```bash
uv sync --extra camoufox
uv run surf-agent setup camoufox
```

Patchright:

```bash
uv sync --extra patchright
uv run surf-agent setup patchright
```

Backend-specific setup details:

- [Camoufox backend](camoufox-backend.md)
- [Patchright backend](patchright-backend.md)

## Backend selection

Persist optional backend:

```bash
uv run surf-agent backend set camoufox
uv run surf-agent backend set patchright
uv run surf-agent backend reset
```

Use one backend for one command without changing config:

```bash
SURF_AGENT_BACKEND=camoufox uv run surf-agent --thread main open https://example.com
SURF_AGENT_BACKEND=patchright uv run surf-agent --thread main open https://example.com
```

See [backends.md](backends.md) for selection priority and backend docs.

## First-use profile setup

First use of a dedicated profile may require one-time browser setup/login. To open the profile without automation/debugging, close Surf Agent automation windows first, then run:

```bash
uv run surf-agent profile open https://x.com
```

## Bridge restart

Only use explicit bridge stop when you intend to kill the persistent browser bridge:

```bash
uv run surf-agent bridge-stop
```

After `bridge-stop`, next use restarts the bridge.
