# Surf Python API

Reference for method signatures, return values and state contracts. For execution, human handoff, recovery and cleanup policy, follow [SKILL.md](../SKILL.md). For dependency or release problems, read [launcher setup](launcher.md).

`surf_agent.Thread` owns a named browser page/window; `surf_agent.Browser` administers runtime and profile configuration. Import them with `Snapshot`, `SurfAgentError` and `ErrorCode` from `surf_agent`.

Each method is a heading holding its exact call signature, so one grep returns its contract: `grep -n -A3 '^### .*wait(' docs/python-api.md`.

## Thread

Targets: with Patchright, `target` is `@<ref>` for a ref the latest snapshot printed (`[ref=e12]` → `@e12`) or a CSS selector; any other `@` name is refused as stale. Use targets from observed page state.

Except `emit()`, methods return values without printing. Use explicit `print()` for values worth reporting; avoid exposing secrets from evaluation, snapshots, or cookie data.

### `Thread(name='default')`

Named ownership handle, not a raw tab selector. Handles for the same name in one Python process share its emission baseline.

### `thread.open(url) -> str`

Navigation output; creates a window if missing; resets the emission baseline.

### `thread.is_open() -> bool`

Checks without opening a window or starting a bridge. An unavailable bridge returns false without proving the page is gone. Malformed responses raise.

### `thread.click(target) -> str`

Action output. `target` per Targets above.

### `thread.fill(target, text) -> str`

Replaces the field's value; action output. `target` per Targets above.

### `thread.type_text(text) -> str`

Types into the focused element; action output.

### `thread.press(key) -> str`

Presses one key, such as `Enter`; action output.

### `thread.scroll(direction) -> str`

`direction` is `up`, `down`, `top`, or `bottom`.

### `thread.wait(target=None, *, gone=None, url=None, timeout_ms=None) -> str`

An `int` target sleeps that many nonnegative milliseconds and takes no other argument. Otherwise waits until every given condition holds: `target` text becomes visible (case-insensitive substring of an element's text, where child texts can join without the space a snapshot name shows), `gone` text stops being visible, `url` glob (`fnmatch`, full URL) matches. `timeout_ms` defaults to 10000. Failure raises code `wait_timeout` naming unmet conditions, current URL and title. Numeric strings are text, not durations. `gone`, `url` and `timeout_ms` are Patchright-only.

### `thread.back() -> str`

Navigation output; resets the emission baseline.

### `thread.text(target=None) -> str`

Visible text: the whole body, or one region by snapshot ref or CSS selector (`"@e5"`, `"article"`). Prefer the ref: a selector matches HTML tags, not the ARIA roles a snapshot prints. Targets are Patchright-only.

### `thread.screenshot(path, *, full_page=False) -> str`

Saves a viewport or full-page image; returns backend output.

### `thread.evaluate(code) -> Any`

Decoded JavaScript value: nested objects/arrays, strings, numbers, booleans, or `None`.

### `thread.snapshot() -> Snapshot`

Complete `Snapshot` with identity metadata and full `.text`; silent, without baseline changes. See [Snapshot output](#snapshot-output).

### `thread.emit(snapshot, *, full=False, sink=None) -> None`

Writes the captured observation to stdout or a text sink; `full=True` forces complete output. See [Snapshot output](#snapshot-output).

### `thread.focus() -> None`

Brings the remembered page forward.

### `thread.reset() -> None`

AXI-only: clears remembered ownership and emission baseline without closing the window. Patchright raises before mutation; use close instead.

### `thread.close() -> None`

Closes the managed page and clears the emission baseline; raises on backend failure.

## Snapshot output

`snapshot()` always captures a complete value. `emit()` never recaptures: its first output is full; later output compares against the last snapshot successfully emitted for that thread name in this Python process, by any handle. A silent capture does not establish a baseline.

```python
from surf_agent import Thread

thread = Thread("research")
thread.emit(thread.snapshot())
thread.click("@e12")      # ref from the emitted snapshot
current = thread.snapshot()
thread.emit(current)              # useful diff, or full fallback
thread.emit(current, full=True)   # explicitly complete output
```

Every emission has matching boundaries, including full snapshots, fallback output, empty snapshots and no-change observations. Diffs use ordinary unified-diff headers naming the observations:

```text
--- BEGIN observation 2 ---
--- observation 1
+++ observation 2
@@ -1 +1 @@
-Old heading
+New heading
--- END observation 2 ---
```

Numbers start at 1 in each Python process and are shared across handles and sinks. Diff headers identify the last successful emission for that thread name, which need not be the preceding number. Unchanged observations include the same header pair and a compact no-changes note. There are no titles or thread-name fields. These are readable output boundaries, not an escaping or security protocol for page text.

Automatic diff falls back to full output if page identity/origin changes, the diff is too large, saves too little, or has too many hunks. There is no forced-diff mode. Nonempty output gets a terminating newline when needed, without changing the snapshot. The baseline advances only after a successful sink write.

Failed or incomplete writes leave the baseline unchanged; observation numbers may have gaps so a partly written frame's number is never reused. `emit()` returns `None`, and all frames append to the selected sink; it does not pause the script for an agent decision.

Capture the frame as a value by passing a sink:

```python
import io

buffer = io.StringIO()
thread.emit(thread.snapshot(), sink=buffer)
frame = buffer.getvalue()  # the same text, boundaries and any fallback header included
```

Baselines belong to the Python process, keyed by thread name: not to the handle, persistent thread storage or the browser. A fresh script's first emission is full. A persistent session keeps each thread's baseline across cells, including through `--reset` and handles rebuilt with `Thread(name)`; a timed-out cell ends the interpreter and its baselines, so the first emission in a new session is full by construction. Every sink advances the same baseline, so a frame captured into a buffer becomes the base of the next stdout diff.

## Browser administration

Methods are silent; print their result only when needed.

### `Browser()`

Constructs without opening a window.

### `browser.setup() -> None`

Validates selected-backend prerequisites. Does not install Chrome.

### `browser.backend() -> BackendInfo`

Fields `backend`, `source`, `config_file`.

### `browser.set_backend(name) -> None`

Stops the prior persisted backend before changing selection; cleanup failure leaves config unchanged. Temporary environment overrides are ignored for this cleanup.

### `browser.reset_backend() -> None`

Clears persisted selection. Stop the current runtime first. Environment selection retains priority.

### `browser.profile() -> ProfileInfo`

Backend, profile directory, browser URL, Chrome class, Patchright bridge port and app ID.

### `browser.open_profile(url='about:blank') -> None`

Opens the dedicated profile for manual setup without automation/debugging.

### `browser.cookie_source() -> CookieSourceConfig | None`

The configured cookie source, if any.

### `browser.set_cookie_source(source, profile, *, domains=(), all_domains=False) -> CookieSourceConfig`

Validates and persists an explicit access scope. Provide domains or all-domain consent, exclusively. Linux and macOS only.

### `browser.reset_cookie_source() -> None`

Disables future imports, not already imported cookies.

### `browser.import_cookies() -> CookieImportResult`

Explicit refresh; the result has `imported_rows`, `skipped`, `destination`.

### `browser.import_cookies_for(domain) -> CookieImportResult`

Adds one consented domain to the configured scope, stops the browser, and imports. Refuses, naming them, while any thread is open.

### `browser.stop_bridge() -> None`

Stops the selected automation runtime.

### `browser.threads() -> list[ThreadInfo]`

`ThreadInfo(name, page_id, url, title)` entries from Patchright's running bridge or AXI's local records; does not start a bridge or scan every browser page.

### `browser.close_matching(pattern) -> None`

Closes remembered pages whose thread names match the glob.

Successful closing removes the remembered thread: Patchright's bridge-held entry or AXI's local state file. An unavailable Patchright bridge yields an empty inventory, not proof that every browser page is closed.

Backend/profile guidance: [selection](backends.md), [manual 1Password setup](1password-setup.md), [cookie consent and failures](cookie-import.md).

## Errors

`SurfAgentError` signals Surf operational failures; invalid Python argument types/values can raise normal Python exceptions. Branch on `error.code` (an `ErrorCode`, or `None` when uncategorized), never on message text:

- `stale_ref`: Ref is not in the current page. Take a new snapshot and use its refs.
- `not_found`: Selector matches nothing. Snapshot and pick a real target.
- `not_visible`, `not_enabled`, `not_editable`: Element exists but cannot take the action. Reveal it, wait for it, or target the real control.
- `intercepted`: Another element covers the target; the message names it (often a cookie banner or modal). Dismiss it first.
- `action_timeout`: Element looked actionable but the action did not finish. Snapshot before retrying.
- `wait_timeout`: A `wait()` condition never held; the message shows what the page showed.
- `page_closed`: The page closed during the call. Inspect with `is_open()` before reopening.
- `bridge_unavailable`: The browser bridge is not reachable. The call did not reach the browser.
- `outcome_unknown`: The call reached the browser but its result was lost. It may have taken effect; follow the [recovery workflow](../SKILL.md#recovery).
- `unsupported`: The selected backend lacks this capability.

Actionability codes come from Patchright; AXI failures carry `None` except `unsupported`.
