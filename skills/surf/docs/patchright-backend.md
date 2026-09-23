# Patchright backend

Read this for Patchright startup failures, profile/port overrides, or shutdown behavior. This backend controls a persistent Chrome-channel profile through a local bridge.

## Setup and selection

Install Google Chrome where Patchright's `chrome` channel finds it (its standard install location). Automation always launches that channel; `SURF_AGENT_CHROME_BIN` only changes the executable `Browser().open_profile()` starts, and it must be the same Chrome so both use one profile format. For Python dependency failures, read [launcher setup](launcher.md); for restoring the default backend or checking environment overrides, read [backend selection](backends.md).

## Runtime data

- Profile: platform user data directory `profiles/chrome/`, or `$SURF_AGENT_HOME/profiles/chrome`, shared with AXI.
- `SURF_AGENT_PATCHRIGHT_PROFILE_DIR`: overrides the profile.
- `SURF_AGENT_PATCHRIGHT_PORT`: bridge port, default `9346`.
- `SURF_AGENT_PATCHRIGHT_APP_ID` or `SURF_AGENT_PATCHRIGHT_CLASS`: application/window identity.

Inspect actual settings with `Browser().profile()`.

## Browser behavior

The bridge uses Patchright's async API with a persistent asyncio runner and a persistent Chrome context, without a fixed viewport. Color-scheme emulation is reset so pages follow the desktop theme. The application identifier uses `--name=<app_id>` so Chromium does not mistake it for a page target.

Chrome-extension behavior depends on the installed browser and profile; verify [1Password setup](1password-setup.md) with a live check rather than assuming extension compatibility.

## Cookies and shutdown

[Cookie import](cookie-import.md) refreshes configured login state only before starting an inactive profile. Patchright excludes its `--password-store=basic` and `--use-mock-keychain` defaults so imported Linux v11 cookies use Chrome's real OS password store/keychain.

Closing the final user-visible page requests bridge shutdown after the close response; background workers do not count. Chrome may independently close the persistent context, causing the bridge to exit. Subsequent startup repeats lifecycle checks. Handle interrupted actions through the [recovery workflow](../SKILL.md#recovery).
