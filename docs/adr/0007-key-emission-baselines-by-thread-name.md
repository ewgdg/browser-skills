---
status: accepted
---

# Key emission baselines by thread name, not by handle

`Thread.emit()` diffs against the last frame emitted for the same thread name in the same Python process, whichever `Thread` handle wrote it. The baseline records what the agent has already received, and the agent reads one stdout per process; the handle is an incidental Python object. `open()`, `back()`, `close()` and `reset()` still clear it, and a new process (fresh script, replaced session) still starts full.

## Why

Models rebuild `Thread(name)` at the top of every session cell. The code-mode pilot (`docs/benchmarks/code-mode-pilot.md`) recorded it, and pi session `01a0d763` repeated it across 34 cells on the Google Cloud console: every rebuilt handle started without a baseline, so every `emit()` printed the full page, seven cells overflowed the agent's output cap, and the agent fell back to regex over `snapshot().text`. Guidance alone had not changed the habit, so the runtime makes it harmless.

## Considered options

**Return the existing instance from `Thread(name)`.** Rejected: a constructor that returns a cached object hides identity, and every other handle attribute would silently become shared.

**Keep per-handle baselines and document "bind once".** Rejected as the only fix: the pilot's guidance already allowed reuse and the models still rebuilt. The skill now also binds the handle once per session.

**Separate stdout and custom-sink baselines.** Deferred: it would keep independent `sink=` consumers for one thread, but ADR 0003's transcript review found no cell capturing frames with `sink=`.

## Consequences

Two handles for one name are no longer independent output consumers; a frame written to a custom sink advances the shared baseline. Baselines survive `run.py --session ID --reset`, which discards bindings but not output the agent already received; `emit(snapshot, full=True)` re-anchors when earlier observations have left the agent's context.
