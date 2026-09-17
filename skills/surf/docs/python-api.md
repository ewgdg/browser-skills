# Surf Python API

`surf_agent.Thread` owns a named browser page/window; `surf_agent.Browser` administers runtime and profile configuration. Import them with `Snapshot` and `SurfAgentError` from `surf_agent`. The Surf action CLI is removed; `surf-google-search` remains a separate CLI.

Use the [skill-local launcher](../SKILL.md) for fresh Python scripts. It accepts a file or stdin and forwards script arguments. There is no persistent Python interpreter: each call imports and initializes handles again. Browser state persists separately, so `Thread("research")` reattaches to the same named context without navigating. Only call `open()` when navigation is intended.

## Thread

```python
from surf_agent import Thread

thread = Thread("research")
try:
    thread.open("https://example.com")
    observed = thread.snapshot()  # complete value; silent
    thread.emit(observed)        # first emission: full text
    print(thread.evaluate("document.title"))
finally:
    thread.close()
```

For a multi-call task, close at task completion instead of closing after each script. Preserve the thread during an explicit pending human handoff. Names must be safe thread names; use a unique task namespace for parallel agents.

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

Use task-owned cleanup patterns; `close_matching("*")` affects every remembered thread. Successful closing removes the remembered thread: Patchright's bridge-held entry or AXI's local state file. An unavailable Patchright bridge yields an empty inventory, not proof that every browser page is closed.

Backend/profile guidance: [selection](backends.md), [manual 1Password setup](1password-setup.md), [cookie consent and failures](cookie-import.md).

## Failure and release boundaries

`SurfAgentError` signals Surf operational failures; invalid Python argument types/values can raise normal Python exceptions. A timeout or lost response does not establish that an action failed: inspect the same named page before deciding to repeat any side effect. Restarting runtime does not make replay safe.

The launcher resolves the full reachable Git commit recorded in `../runtime-revision`. While it is `UNRELEASED`, default launch fails deliberately. `SURF_AGENT_DEPENDENCY=/absolute/path/to/surf_agent-....whl` permits local built-wheel validation, not a production release claim. Pinning remains gated on an authorized push and validation of that remote revision.
