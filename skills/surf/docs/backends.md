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


## Live cookie import

AXI and Patchright share the Surf Chrome profile and can use an explicitly configured live Chrome cookie source. Configure allowed domains (or explicit all-domain consent) with `profile cookie-source set`, then inspect or force a refresh with `profile cookie-source show` and `profile import-cookies`.

Automatic import occurs before starting an inactive owned profile only when the source fingerprint changes. SQLite online backup supports a running locked source. Same browser family, OS user, and `Local State.os_crypt` metadata are required. For imported Linux v11 cookies, Patchright must use Chrome’s real OS password store/keychain rather than its `--password-store=basic` and `--use-mock-keychain` automation defaults; the bridge excludes those defaults at launch. Imports upsert rows and do not remove destination-only cookies. Camoufox rejects this feature.
