# Surf Python API

Reference for method signatures, return values and state contracts. For execution, human handoff, recovery and cleanup policy, follow [SKILL.md](../SKILL.md). For dependency or release problems, read [launcher setup](launcher.md).

`surf_agent.Thread` owns a named browser page/window; `surf_agent.Browser` administers runtime and profile configuration. Import them with `Snapshot` and `SurfAgentError` from `surf_agent`.

## Thread

| Method | Result and behavior |
| --- | --- |
| `Thread(name="default")` | Named ownership handle, not a raw tab selector. |
| `open(url)` | Navigation output as `str`; creates a window if missing; resets emission baseline. |
| `is_open()` | `bool`; checks without opening a window or starting a bridge. Unavailable bridge returns false without proving the page is gone. Malformed responses raise. |
| `click(target)`, `fill(target, text)`, `type_text(text)`, `press(key)` | Action output as `str`. Use targets from observed page state. |
| `scroll(direction)` | `str`; direction is `up`, `down`, `top`, or `bottom`. |
| `wait(target)` | `str`; nonnegative integer milliseconds or nonempty visible-text string. Numeric strings are text, not durations. |
| `back()` | Navigation output as `str`; resets emission baseline. |
| `text()` | Visible body text as `str`. |
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
thread.click("@details")  # observed target; deterministic action
current = thread.snapshot()
thread.emit(current)              # useful diff, or full fallback
thread.emit(current, full=True)   # explicitly complete output
```

Automatic diff falls back to full output if page identity/origin changes, the diff is too large, saves too little, or has too many hunks. There is no forced-diff mode. Nonempty output gets a terminating newline when needed, without changing the snapshot. The baseline advances only after a successful sink write.

Baselines belong to Python handles, not persistent thread storage. A fresh script's first emission is full. Use separate handles for independent output consumers rather than sending dependent diffs to unrelated sinks.

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
| `set_cookie_source(source, profile, *, domains=(), all_domains=False)` | Validates/persists explicit access scope; returns `CookieSourceConfig`. Provide domains or all-domain consent, exclusively. Linux-only. |
| `reset_cookie_source()` | Disables future imports, not already imported cookies; returns `None`. |
| `import_cookies()` | Explicit refresh; returns `CookieImportResult` with `imported_rows`, `skipped`, `destination`. |
| `stop_bridge()` | Stops selected automation runtime; returns `None`. |
| `threads()` | List of `ThreadInfo(name, page_id, url, title)` from Patchright's running bridge or AXI's local records; does not start a bridge or scan every browser page. |
| `close_matching(pattern)` | Closes remembered pages whose thread names match the glob; returns `None`. |

Successful closing removes the remembered thread: Patchright's bridge-held entry or AXI's local state file. An unavailable Patchright bridge yields an empty inventory, not proof that every browser page is closed.

Backend/profile guidance: [selection](backends.md), [manual 1Password setup](1password-setup.md), [cookie consent and failures](cookie-import.md).

## Errors

`SurfAgentError` signals Surf operational failures; invalid Python argument types/values can raise normal Python exceptions. For uncertain outcomes after transport failures, follow the [recovery workflow](../SKILL.md#recovery).
