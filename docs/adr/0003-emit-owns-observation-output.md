---
status: accepted
---

# Write observation frames in emit(), not print()

`Thread.emit(snapshot, *, full=False, sink=None)` writes its own numbered `--- BEGIN observation N ---` frame and returns `None`; `print()` stays the writer for every other value, including final results. The two share one stdout stream and interleave in call order, so a cell's output remains exactly what the same code prints under `run.py FILE`. The decision fuses three things into one call — reserving the observation number, choosing full or diff output, and advancing the baseline only after a completed write — because each depends on state only the writer holds.

## Considered options

**`emit()` returns the frame for the caller to print (`print(emit(snap))`).** Rejected: the baseline advance would then depend on whether someone else printed the value, which `emit()` cannot observe. A cell that computes the frame and then raises, or a caller that forgets the print, leaves the baseline advanced with the frame undelivered, so the next emission diffs against an observation the agent never received. The half-measure — write and also return — duplicates every frame and needs an opt-out flag, the hazard that makes Codex carry `{ emit: false }`. A lazy frame whose `__str__` commits the baseline fails on Python's own terms: `__str__` fires from f-strings, logging and `repr` in tracebacks. Today `print(emit(...))` appends a stray `None`, which is a loud signal that `emit()` already wrote.

**Split into `diff()` plus `output(text)`.** Rejected: `diff(prev, cur)` requires the caller to retain the previous snapshot, including across cells, and the retention pilot measured that models rebuild their handle in every cell and reuse no cross-cell values, so the explicit two-argument form would be skipped in favor of the one-argument `emit()`. Splitting the pair also opens a reservation window: between computing a diff and delivering it, another emission can advance the baseline and renumber a frame the first call already decided against. `output(text)` is a synonym for `print()`, and a launcher-parsed result marker would make stdout something other than ordinary script output.

**Remove `emit()` and print values instead (`print(snap)`, `print(snap2.diff(snap1))`).** Rejected: the diff-versus-full decision needs both snapshot texts and four rules that live in `packages/surf-agent/src/surf_agent/constants.py` — ratio at most `SNAPSHOT_DIFF_MAX_RATIO` (0.50), savings of at least `SNAPSHOT_DIFF_MIN_SAVED_CHARS` (250), at most `SNAPSHOT_DIFF_MAX_HUNKS` (8) hunks, and full output whenever page id or origin changes. A call site has neither snapshot in hand, so it either diffs blind — producing a full diff across a navigation, the largest possible output — or reads both snapshots to decide, which is the cost the gate exists to avoid. It also needs `__str__` or an explicit `.text` on `Snapshot` and hand-written boundaries, and it loses the observation number, which is how a diff names its base across cells and how `full=True` re-anchors after interpreter replacement. Today `print(snap)` emits the dataclass repr.

**REPL echo** is a separate decision, already rejected in `plans/active/persistent-interpreter.md`; this ADR does not reopen it.

## Consequences

`emit()` is the only writer that touches emission state, so a successful write is the baseline advance: numbers are reserved before writing and may therefore have gaps, and a failed or short write leaves the baseline unchanged. `emit(..., sink=io.StringIO())` hands the same frame text to callers that want it, keeping that need inside the existing path. Baselines remain per handle and die with the interpreter, so a replaced interpreter's first emission is full by construction. The accepted cost is vocabulary: `emit` is non-standard and does not reveal that it writes and returns `None`, so the rule must be taught in `skills/surf/SKILL.md` and `skills/surf/docs/python-api.md` rather than inferred. If observed sessions show models wrapping it in `print`, the fix is a guidance line, not a signature change.

## Reconsidered

A manual reader — `Snapshot.diff(base)` returning the same `SnapshotDiffDecision(output, used_diff, reason)` that `emit()` computes privately — was weighed again against 60 days of pi session transcripts: `emit()` runs in 10 of 150 browser cells, no cell captures a frame with `sink=`, and the window's only `difflib` hits belong to this repository's own development sessions rather than surf usage. Deferred, not rejected: the reader needs no import, touches no baseline and keeps `emit()` the only writer, but no consumer has been observed. Revisit when one appears — tooling that wants a diff as a value, or agents writing their own — and prefer the method form over a module-level `smart_diff`, which would also spend a per-cell import.
