# Surf Python API (first slice)

`surf_agent.Thread` is the importable seam for a named Surf browser interaction. A thread is an ownership context backed by Surf's dedicated browser window; it is not a tab-selection mode.

```python
from surf_agent import Thread

thread = Thread.acquire("research")
try:
    thread.open("https://example.com")
    baseline = thread.snapshot()       # full snapshot value (str)
    # perform browser actions through the Thread API as it grows
    changed = thread.emit()            # useful diff when one is available
    full = thread.emit(full=True)      # explicit full snapshot value
finally:
    thread.close()
```

## Current interface

- `Thread.acquire(name="default", **agent_options) -> Thread` creates the named context using the existing Surf backend and browser lifecycle setup. `name` must be a safe thread name.
- `thread.open(url) -> str` navigates the context's page and returns backend navigation output.
- `thread.snapshot() -> str` captures the full current accessibility snapshot and establishes the emission baseline.
- `thread.emit(full=False) -> str` captures a new snapshot, returning an automatically gated useful diff when a baseline exists. Before the first baseline it returns the full value. `full=True` always returns the full value. Every emission becomes the next baseline.
- `thread.close() -> None` closes the managed page.

This is intentionally a bounded first vertical slice. Other browser actions remain on the existing staged implementation until their object methods and tests are ready. Interpreter persistence, CLI replacement, and the skill-local file/stdin launcher are planned but not implemented yet. The launcher will use a commit-pinned Git dependency only after that commit is reachable; release pinning is an explicit gate.
