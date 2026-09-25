---
status: accepted
---

# Launch Chrome without a window, with Patchright's own flags

Surf must never take focus from the user's app. On macOS, showing a Chrome window activates Chrome, and the Patchright bridge stops Chrome when the last thread closes, so a startup window stole focus about once per task. Chrome can start with none (`--no-startup-window`), but Patchright's persistent launch always adds `about:blank` and waits for that page, so the switch hangs it. Upstream Playwright closed that report as not planned (microsoft/playwright#42093): custom browser args are unsupported.

The bridge launches with `ignore_default_args=True`, which makes Patchright skip the first-page wait and pass only the args it is given. Surf gives it Patchright's own flags, read at launch: a first launch points `executable_path` at a stub that records its argv and exits, so no browser starts. The real launch uses that list without `about:blank` and without the keychain defaults cookie import rejects, plus Surf's window flags and `--no-startup-window`. Thread windows are created in the background through a browser-level CDP session, because a page-level session needs an open page and opening one shows a foreground window.

## Considered options

Weighted for removing the focus grab (20), ongoing maintenance (25), surviving Patchright upgrades loudly (20), stealth parity with Patchright (15), simplicity (10) and time to ship (10):

- **This workaround:** 85 of 100. Nothing to maintain by hand, since the flags come from the installed Patchright on every launch.
- **Patching the installed Patchright bundle:** 68. A text match on a generated file that changes between releases, and uv hardlinks installed packages from its cache, so an edit leaks into other environments.
- **Forking Patchright:** 50. Patchright rebuilds Playwright's driver on each release; a fork either repeats that work or lags on stealth fixes.
- **Waiting for an upstream fix:** does not remove the grab. Requested in Patchright ([discussion 232](https://redirect.github.com/Kaliiiiiiiiii-Vinyzu/patchright/discussions/232)); if fixed, the launch shrinks to passing `--no-startup-window` and the flag capture goes.

## Consequences

Two Patchright behaviours are relied on without being documented API: `ignoreAllDefaultArgs` skips the first-page wait, and the executable receives the full flag list. `packages/surf-agent/tests/test_patchright_launch_contract.py` checks both against the installed Patchright, and an explicit launch timeout turns a regression into an error rather than a hang. Patchright is pinned to its major version (`>=1.60.1,<2`), so minor releases reach users before those tests run here. The first thread no longer reuses a startup page, so a new thread's history starts at its own URL.
