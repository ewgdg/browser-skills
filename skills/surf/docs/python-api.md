# Surf Python API

Reference for method signatures, return values and state contracts. For execution, human handoff, recovery and cleanup policy, follow [SKILL.md](../SKILL.md). For dependency or release problems, read [launcher setup](launcher.md).

`surf_agent.Thread` owns a named browser page/window; `surf_agent.Browser` administers runtime and profile configuration. Import them with `Snapshot`, `SurfAgentError` and `ErrorCode` from `surf_agent`.

## Thread

| Method | Result and behavior |
| --- | --- |
| `Thread(name="default")` | Named ownership handle, not a raw tab selector. |
| `open(url)` | Navigation output as `str`; creates a window if missing; resets emission baseline. |
| `is_open()` | `bool`; checks without opening a window or starting a bridge. Unavailable bridge returns false without proving the page is gone. Malformed responses raise. |
| `click(target)`, `fill(target, text)`, `type_text(text)`, `press(key)` | Action output as `str`. Use targets from observed page state. With Patchright, `target` is `@<ref>` for a ref the latest snapshot printed (`[ref=e12]` → `@e12`) or a CSS selector; any other `@` name is refused as stale. |
| `scroll(direction)` | `str`; direction is `up`, `down`, `top`, or `bottom`. |
| `wait(ms)` | `str`; sleeps for nonnegative integer milliseconds. |
| `wait(text=None, *, gone=None, url=None, timeout_ms=None)` | `str`; waits until every given condition holds: `text` becomes visible, `gone` text stops being visible, `url` glob (`fnmatch`, full URL) matches. Default timeout 10000 ms. Failure raises code `wait_timeout` naming unmet conditions, current URL and title. Numeric strings are text, not durations. `gone`, `url` and `timeout_ms` are Patchright-only. |
| `back()` | Navigation output as `str`; resets emission baseline. |
| `text(target=None)` | Visible text as `str`: the whole body, or one region by snapshot ref or CSS selector (`"@e5"`, `"main, article"`). Targets are Patchright-only. |
| `screenshot(path, *, full_page=False)` | Saves viewport/full-page image; returns backend output as `str`. |
| `evaluate(code)` | Decoded JavaScript value: nested objects/arrays, strings, numbers, booleans, or `None`. |
| `snapshot()` | Complete `Snapshot` with identity metadata and full `.text`; silent, without baseline changes. |
| `emit(snapshot, *, full=False, sink=None)` | Writes the captured observation to stdout or a text sink; returns `None`. |
| `focus()` | Brings the remembered page forward; returns `None`. |
| `reset()` | AXI-only: clears remembered ownership and emission baseline without closing the window. Patchright raises before mutation; use close instead. |
| `close()` | Closes the managed page and clears emission baseline; returns `None`, raises on backend failure. |

Except `emit()`, methods return values without printing. Use explicit `print()` for values worth reporting; avoid exposing secrets from evaluation, snapshots, or cookie data.

## Snapshot output

`snapshot()` always captures a complete value. `emit()` never recaptures: its first output is full; later output compares against that handle's last successfully emitted snapshot. A silent capture does not establish a baseline.

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

Numbers start at 1 in each Python process and are shared across handles and sinks. Diff headers identify the last successful emission from that handle, which need not be the preceding number. Unchanged observations include the same header pair and a compact no-changes note. There are no titles or thread-name fields. These are readable output boundaries, not an escaping or security protocol for page text.

Automatic diff falls back to full output if page identity/origin changes, the diff is too large, saves too little, or has too many hunks. There is no forced-diff mode. Nonempty output gets a terminating newline when needed, without changing the snapshot. The baseline advances only after a successful sink write.

Failed or incomplete writes leave the baseline unchanged; observation numbers may have gaps so a partly written frame's number is never reused. `emit()` returns `None`, and all frames append to the selected sink; it does not pause the script for an agent decision.

Capture the frame as a value by passing a sink:

```python
import io

buffer = io.StringIO()
thread.emit(thread.snapshot(), sink=buffer)
frame = buffer.getvalue()  # the same text, boundaries and any fallback header included
```

Baselines belong to Python handles, not persistent thread storage or the browser. A fresh script's first emission is full. A persistent session keeps each handle's baseline across cells; a timed-out cell ends the interpreter and its baselines, so the first emission of a handle rebuilt in a new session is full by construction. Use separate handles for independent output consumers rather than sending dependent diffs to unrelated sinks.

## Browser administration

Construct `Browser()` without opening a window. Methods are silent; print their result only when needed.

| Method | Result and behavior |
| --- | --- |
| `setup()` | Validates selected-backend prerequisites; returns `None`. Does not install Chrome. |
| `backend()` | `BackendInfo`: `backend`, `source`, `config_file`. |
| `set_backend(name)` | Stops the prior persisted backend before changing selection; cleanup failure leaves config unchanged. Temporary environment overrides are ignored for this cleanup. Returns `None`. |
| `reset_backend()` | Clears persisted selection; returns `None`. Stop current runtime first. Environment selection retains priority. |
| `profile()` | `ProfileInfo`: backend, profile directory, browser URL, Chrome class, Patchright bridge port and app ID. |
| `open_profile(url="about:blank")` | Opens dedicated profile for manual setup without automation/debugging; returns `None`. |
| `cookie_source()` | `CookieSourceConfig` or `None`. |
| `set_cookie_source(source, profile, *, domains=(), all_domains=False)` | Validates/persists explicit access scope; returns `CookieSourceConfig`. Provide domains or all-domain consent, exclusively. Linux and macOS only. |
| `reset_cookie_source()` | Disables future imports, not already imported cookies; returns `None`. |
| `import_cookies()` | Explicit refresh; returns `CookieImportResult` with `imported_rows`, `skipped`, `destination`. |
| `import_cookies_for(domain)` | Adds one consented domain to the configured scope, stops the browser, and imports; returns `CookieImportResult`. Refuses, naming them, while any thread is open. |
| `stop_bridge()` | Stops selected automation runtime; returns `None`. |
| `threads()` | List of `ThreadInfo(name, page_id, url, title)` from Patchright's running bridge or AXI's local records; does not start a bridge or scan every browser page. |
| `close_matching(pattern)` | Closes remembered pages whose thread names match the glob; returns `None`. |

Successful closing removes the remembered thread: Patchright's bridge-held entry or AXI's local state file. An unavailable Patchright bridge yields an empty inventory, not proof that every browser page is closed.

Backend/profile guidance: [selection](backends.md), [manual 1Password setup](1password-setup.md), [cookie consent and failures](cookie-import.md).

## Errors

`SurfAgentError` signals Surf operational failures; invalid Python argument types/values can raise normal Python exceptions. Branch on `error.code` (an `ErrorCode`, or `None` when uncategorized), never on message text:

| Code | Meaning and next step |
| --- | --- |
| `stale_ref` | Ref is not in the current page. Take a new snapshot and use its refs. |
| `not_found` | Selector matches nothing. Snapshot and pick a real target. |
| `not_visible`, `not_enabled`, `not_editable` | Element exists but cannot take the action. Reveal it, wait for it, or target the real control. |
| `intercepted` | Another element covers the target; the message names it (often a cookie banner or modal). Dismiss it first. |
| `action_timeout` | Element looked actionable but the action did not finish. Snapshot before retrying. |
| `wait_timeout` | A `wait()` condition never held; the message shows what the page showed. |
| `page_closed` | The page closed during the call. Inspect with `is_open()` before reopening. |
| `bridge_unavailable` | The browser bridge is not reachable. The call did not reach the browser. |
| `outcome_unknown` | The call reached the browser but its result was lost. It may have taken effect; follow the [recovery workflow](../SKILL.md#recovery). |
| `unsupported` | The selected backend lacks this capability. |

Actionability codes come from Patchright; AXI failures carry `None` except `unsupported`.
