# Windowless Patchright launch

## Goal

Surf never takes focus from the user's app: not when Chrome starts, not when a thread opens a window.

## Intention

Thread windows already open in the background (`2e6312b`). The remaining grab is Chrome's startup window: on macOS showing it activates Chrome, and the Patchright bridge stops Chrome when the last thread closes, so this happens about once per task. Chrome can start with no window (`--no-startup-window`), but Patchright's persistent launch always opens `about:blank` and waits for that page, so it hangs. Upstream Playwright declined the fix (microsoft/playwright#42093, not planned); the user will file a Patchright request.

## Decisions

- **Launch with `ignore_default_args=True`.** Patchright then skips the first-page wait (`if (persistent && !ignoreAllDefaultArgs) loadDefaultContext`) and passes only our args, so Surf supplies the full list.
- **Copy Patchright's own flags at launch instead of maintaining them.** Launch once with `executable_path` set to a stub that records its argv and exits (about 20 ms, no browser). Then launch Chrome with that list minus `about:blank` and `PATCHRIGHT_INCOMPATIBLE_DEFAULT_ARGS`, plus Surf's window flags and `--no-startup-window`. The stub is a `/bin/sh` script so interpreter paths with spaces cannot break its shebang.
- **Create windows through a browser-level CDP session** (`context.browser.new_browser_cdp_session()`). The page-anchored session needed an existing page; with none open, the anchor `new_page()` opened a foreground window. The anchor and the startup-page adoption are removed: with no startup window there is nothing to adopt.
- **Pin Patchright to its major version** (`>=1.60.1,<2`) as the user chose. Minor releases still reach users; the contract tests run on every bump here.
- **Fail fast if the skip ever breaks:** an explicit launch timeout turns a hang into an error.

Rejected, with weighted scores out of 100: forking Patchright (50: rebuilds per release, stealth lag), patching the installed bundle (68: fragile text match, and uv hardlinks installed packages from its cache, so an edit leaks into other environments), waiting for upstream only (does not fix the problem).

## Work Plan

1. Tests first: fake-runtime tests for the launch arguments and for window creation without an anchor; live contract tests for the stub capture and for a headed windowless launch returning with zero pages.
2. `backends/patchright/launch_args.py`: capture Patchright's default args with the stub.
3. Bridge: launch with the captured args; browser-level CDP session; remove `_cdp_anchor_page` and `_adopt_initial_page`.
4. Pin `patchright>=1.60.1,<2`.
5. ADR 0002 and the Patchright backend doc.
6. Verify: full suite on Linux and the Mac; live Patchright tests; installed acceptance on the Mac; focus check on the Mac (first and later threads, another app in front); niri app id on Linux.

## Progress

- [x] Steps 1–5: tests, capture module, bridge, pin, ADR 0005 and backend doc
- [x] Step 6: full suite green on Linux and the Mac (341 passed); live and installed acceptance 36 passed on both; Mac focus stays on the user's app through launch, later threads and relaunch, and moves only on `focus()`; niri keeps the `surf-agent` app id

## Surprises & Discoveries

- The first thread used to adopt Chrome's `about:blank` startup page, so its history began there. A thread window now opens at its URL, and `back` on a fresh thread is a no-op.
- Upstream Playwright already declined the fix (microsoft/playwright#42093, not planned).
