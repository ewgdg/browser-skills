# macOS support (issue #23)

## Goal

Run Surf on macOS with the same contract as Linux, including live cookie import, and verify it on a real Mac.

## Intention

Surf has only run on Linux. The runtime assumes Linux in four places: Chrome discovery, process discovery, browser-family proof from paths, and a hard Linux gate on cookie import. Close those gaps without adding a macOS-only code path where one generic mechanism fits both.

## Scope & Constraints

- Tests must pass without a Mac: inject process listings, paths, and platform names.
- Cookie import keeps ADR-0001's model: copy encrypted rows verbatim, never decrypt.
- Windows stays unsupported.

## Decisions

- **Cookie import on macOS copies rows verbatim, like Linux.** macOS Chrome keeps one cookie key per app in the Keychain item `Chrome Safe Storage`, not per profile. Surf's destination Chrome is the same signed `Google Chrome.app` and already launches without `--use-mock-keychain` (`PATCHRIGHT_INCOMPATIBLE_DEFAULT_ARGS`), so it reads the same key and decrypts the copied rows. No Keychain read by Surf, so no Keychain prompt and no decryption code. This corrects ADR-0001's "Linux only" consequence.
- **Process discovery uses `psutil`.** The `ps -ww -o command=` fallback joins argv with spaces; on macOS the profile lives under `~/Library/Application Support/…`, so `--user-data-dir=` values are split and never match. `psutil` reads argv with real boundaries on Linux and macOS, replacing three hand-written listings (`runtime.iter_process_args`, `chrome_lifecycle._iter_process_args`, `session._proc_commands`/`_ps_commands`).
- **Windows is unsupported.** A running Chrome holds its Cookies database under a mandatory Windows file lock, which breaks the "source Chrome stays open" contract; the runtime also depends on `fcntl` and Unix sockets. Docs state "Linux and macOS".

## Work Plan

1. `processes.py`: one `iter_process_args()` backed by `psutil`; use it everywhere.
2. `find_chrome_bin()`: also check the macOS app bundles.
3. `browser_executable_family()` / `detect_browser_family()`: recognize macOS bundle executables and `~/Library/Application Support/<vendor>/<browser>` roots.
4. Remove the Linux-only gate for cookie import on `darwin`.
5. Verify `Local State.os_crypt` on a real Mac and adjust the metadata check if macOS has none.
6. Real Mac run: `Browser().setup()`, installed acceptance test, and a cookie import into a logged-in site.
7. Docs: ADR-0001 consequence, skill docs state macOS support and limits.

## Validation

- Unit tests for each fix with injected inputs.
- On the Mac: the README acceptance command and a manual cookie import check.

## Progress

- [x] Steps 1–4 with tests (`209635c`)
- [ ] Step 5–6 on the Mac
- [x] Step 7: platform line and cookie docs (ADR amended); revisit if the Mac run changes anything

## Surprises & Discoveries

- `ps`-based argv parsing breaks on paths with spaces, which is the macOS default location.
