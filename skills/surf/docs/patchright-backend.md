# Patchright backend

Patchright is experimental. Use it for Chrome-channel persistent-profile trials where AXI is not enough.

It may help Chrome-extension workflows, but extension behavior depends on the installed Chrome and profile state. Do not assume perfect 1Password support without a live smoke test.

## Setup

```bash
uv tool install "surf-agent[patchright] @ git+https://github.com/ewgdg/browser-skills.git#subdirectory=packages/surf-agent"
surf-agent setup patchright
```

`setup patchright` checks for the Patchright Python package and a Chrome executable. It does not run Patchright's Chrome installer. When anything is missing, it prints manual Chrome-install instructions so the user controls browser installation.

Install Google Chrome yourself and make it available on PATH as `google-chrome`, or set `SURF_AGENT_CHROME_BIN`.

## Select backend

Persist Patchright:

```bash
surf-agent backend set patchright
```

Use once without changing config:

```bash
SURF_AGENT_BACKEND=patchright surf-agent --thread main open https://example.com
```

## Runtime data

- profile: platform user data dir `profiles/chrome/` by default, or `$SURF_AGENT_HOME/profiles/chrome`, shared with AXI because both are Chrome-family backends
- port env: `SURF_AGENT_PATCHRIGHT_PORT` default `9346`
- profile env: `SURF_AGENT_PATCHRIGHT_PROFILE_DIR` overrides the shared Chrome profile
- app/window env: `SURF_AGENT_PATCHRIGHT_APP_ID` or `SURF_AGENT_PATCHRIGHT_CLASS`

## Commands

Patchright supports these core commands through a local Python bridge:

```text
open, new, snapshot, text, click, fill, type, press, scroll, wait, back, screenshot, eval, close, close-matching, close-all, focus, state, list
```

## Implementation notes

- Uses `patchright.async_api.async_playwright()` on a persistent `asyncio.Runner`.
- Launches a persistent Chrome-channel context with `launch_persistent_context(..., channel="chrome", no_viewport=True)`.
- Resets Patchright's color-scheme emulation so pages follow the desktop theme.
- Uses `--name=<app_id>` flag form to avoid Chromium treating the app id as a page target.

## Limitations

- Chrome-extension behavior is profile/browser dependent; verify 1Password manually.


## Live cookie refresh and idle stop

After explicit cookie-source consent, Patchright imports changed scoped cookies before launching its inactive persistent Chrome profile. The source may remain live and locked because the import uses SQLite online backup. Browser family, OS user, and `Local State.os_crypt` must match. For imported Linux v11 cookies, the bridge excludes Patchright’s `--password-store=basic` and `--use-mock-keychain` defaults so Chrome uses the real OS password store/keychain. Rows are upserted only and destination-only cookies survive.

Closing the final user-visible page requests bridge shutdown immediately after the close response completes; background workers do not count as user-visible pages. Chrome may independently close the persistent context when its final page disappears. In that case the bridge exits, lifecycle preflight runs again, and the interrupted command is retried once against a fresh bridge. Import or compatibility failures abort startup; stop the destination and correct the configuration before retrying `surf-agent profile import-cookies`.
