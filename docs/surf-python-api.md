# Surf Python API (first slice)

`surf_agent.Thread` is the importable seam for a named Surf browser interaction. A thread is an ownership context backed by Surf's dedicated browser window; it is not a tab-selection mode.

```python
from surf_agent import Thread

thread = Thread("research")
try:
    thread.open("https://example.com")
    baseline = thread.snapshot()       # Snapshot value; baseline.text is complete
    # perform browser actions through the Thread API
    thread.click("@submit")
    thread.fill("@query", "browser skills")
    thread.press("Enter")
    current = thread.snapshot()        # silent observation for application logic
    thread.emit(current)               # writes full value to stdout; establishes baseline
    changed = thread.snapshot()
    thread.emit(changed)               # writes a useful diff from the last emission
    thread.emit(changed, full=True)    # writes the complete value explicitly
    # emit() returns None; its output is the browser snapshot/diff on stdout.
finally:
    thread.close()
```

## Current interface

- `Thread(name="default") -> Thread` creates the named context using the existing Surf backend and browser lifecycle setup. `name` must be a safe thread name.
- `thread.open(url) -> str` navigates the context's page and returns backend navigation output. Navigation starts a fresh emission baseline.
- `thread.is_open() -> bool` checks managed state without starting a bridge or opening a missing window.
- `thread.click(target) -> str`, `thread.fill(target, text) -> str`, `thread.type_text(text) -> str`, and `thread.press(key) -> str` perform interaction actions using backend targets/keys.
- `thread.scroll(direction) -> str` accepts `up`, `down`, `top`, or `bottom`.
- `thread.wait(target) -> str` accepts an integer number of milliseconds or a non-empty visible-text string; it does not infer between the two.
- `thread.back() -> str` navigates back and starts a fresh emission baseline. `thread.text() -> str` returns visible body text.
- `thread.screenshot(path, full_page=False) -> str` saves a viewport or full-page screenshot.
- `thread.evaluate(code) -> object` returns the browser's decoded JavaScript result as a Python value (including nested objects/arrays, strings, numbers, booleans, and null).
- `thread.snapshot() -> Snapshot` captures a complete accessibility snapshot value with identity metadata. It never changes the emission baseline; use `.text` for the full text.
- `thread.emit(snapshot, full=False, sink=None) -> None` writes an already captured observation to `sink` (stdout by default), without recapturing. Automatic emission compares against the last successfully emitted snapshot; before one exists it writes the full value. `full=True` always writes the complete value. The baseline advances only after a successful write.
- `thread.close() -> None` closes the managed page and raises `SurfAgentError` if the backend reports a nonzero status.

Emission baselines belong to the handle, not persistent thread storage. Reacquiring the same named thread in another script retains the browser context but starts with full output. Use a separate handle for each observation consumer rather than sending dependent diffs to unrelated destinations.

All non-emission methods return values and do not print to stdout. This is intentionally a bounded staged replacement: setup/default selection, manual login/unblock, profile and cookie workflows, bridge cleanup, CLI removal, interpreter persistence, and the skill-local file/stdin launcher remain planned. The launcher will use a commit-pinned Git dependency only after that commit is reachable; release pinning is an explicit gate.
