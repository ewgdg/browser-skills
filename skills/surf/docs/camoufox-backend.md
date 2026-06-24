# Camoufox backend

Camoufox is experimental. Use it for Firefox/Camoufox fingerprint-resistance trials, not for Chrome-extension workflows.

## Setup

```bash
uv tool install 'surf-agent[camoufox]'
surf-agent setup camoufox
```

`setup camoufox` does not run Camoufox install/fetch commands. It checks whether the Python package and browser are present, then prints manual setup instructions when anything is missing.

Manual browser setup commands:

```bash
python -m camoufox sync
python -m camoufox set official/prerelease
python -m camoufox fetch
```

## Select backend

Persist Camoufox:

```bash
surf-agent backend set camoufox
```

Use once without changing config:

```bash
SURF_AGENT_BACKEND=camoufox surf-agent --thread main open https://example.com
```

## Runtime data

- profile: platform user data dir `profiles/firefox/` by default, or `$SURF_AGENT_HOME/profiles/firefox`, shared by Firefox-family backends
- port env: `SURF_AGENT_CAMOUFOX_PORT` default `9345`
- profile env: `SURF_AGENT_CAMOUFOX_PROFILE_DIR` overrides `SURF_AGENT_FIREFOX_PROFILE_DIR` and the shared Firefox profile
- app/window env: `SURF_AGENT_CAMOUFOX_APP_ID` or `SURF_AGENT_CAMOUFOX_CLASS`

## Commands

Camoufox supports these core commands through a local Python bridge:

```text
open, new, snapshot, text, click, fill, type, press, scroll, wait, back, screenshot, eval, close, focus, state, list
```

## Limitations

- Chrome extensions/profile behavior does not apply.
- `close-matching` is not implemented.
