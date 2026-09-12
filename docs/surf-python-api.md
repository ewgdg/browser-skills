# Surf Python API (first slice)

`surf_agent.Thread` is the importable seam for a named Surf browser interaction. A thread is an ownership context backed by Surf's dedicated browser window; it is not a tab-selection mode.

```python
from surf_agent import Thread

thread = Thread("research")
try:
    thread.open("https://example.com")
    baseline = thread.snapshot()       # Snapshot value; baseline.text is complete
    # perform browser actions through the Thread API as it grows
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
- `thread.snapshot() -> Snapshot` captures a complete accessibility snapshot value with identity metadata. It never changes the emission baseline; use `.text` for the full text.
- `thread.emit(snapshot, full=False, sink=None) -> None` writes an already captured observation to `sink` (stdout by default), without recapturing. Automatic emission compares against the last successfully emitted snapshot; before one exists it writes the full value. `full=True` always writes the complete value. The baseline advances only after a successful write.
- `thread.close() -> None` closes the managed page and raises `SurfAgentError` if the backend reports a nonzero status.

This is intentionally a bounded first vertical slice. Other browser actions remain on the existing staged implementation until their object methods and tests are ready. Interpreter persistence, CLI replacement, and the skill-local file/stdin launcher are planned but not implemented yet. The launcher will use a commit-pinned Git dependency only after that commit is reachable; release pinning is an explicit gate.
