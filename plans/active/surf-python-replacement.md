# Surf Python replacement

## Goal
Replace the action-command `surf-agent` CLI with a small importable Python interface. Keep browser lifecycle and backend implementations behind a deep module interface; a thin skill-local launcher will later provide file/stdin execution for ordinary Python scripts.

## Intention
Callers should hold a named `Thread` interaction/ownership context representing Surf's dedicated browser window. They should not parse command strings or capture CLI output to use browser operations.

## Scope & constraints
- Public context is `surf_agent.Thread`; it represents a dedicated window, not tab mode.
- No compatibility shims or migration logic in source.
- First slice only: `Thread.acquire`, `open`, `snapshot`, `emit`, and `close`.
- Reuse existing `SurfAgent` setup, browser lifecycle, and backend seams (`open`, `capture_snapshot`, `close`).
- Snapshot is an ordinary Python string value. `emit()` auto-gates useful diffs; `emit(full=True)` always returns the full value.
- Defer interpreter persistence. The future launcher runs normal Python scripts from file/stdin.
- Do not pin unpushed local code as a reachable remote dependency. Distribution/release is an explicit gate.
- No push, session systems, MCP, or unrelated refactors.

## Work plan
1. Establish `Thread` as the public interaction seam and cover behavior at that seam (complete for the first slice).
2. Move remaining browser actions behind object methods without CLI parsing or print capture.
3. Replace the action-command entrypoint and remove obsolete command parsing.
4. Add a thin skill-local launcher that installs a commit-pinned Git dependency and executes ordinary Python from file/stdin.
5. Update skill instructions and installation/release documentation; only pin published/reachable commits.

## Validation
- First slice: targeted `packages/surf-agent/tests/test_thread.py` plus existing backend/lifecycle tests.
- Tests fail fast and observe only `Thread`'s public interface; fakes stand in for the existing backend seam.
- Full suite only after the staged replacement is complete.

## Progress
- Added `surf_agent.thread.Thread` with acquire/open/snapshot/emit/close.
- Added test-first coverage for named acquisition, delegation, full snapshots, automatic useful diffs, explicit full emission, close delegation, and unsafe names.
- Exported `Thread` from the package root.
- Added truthful first-slice API documentation.

## Surprises & discoveries
- Existing `SurfAgent` already centralizes backend selection, lifecycle startup, profile safety, and snapshot diff gating. The new interface can stay small by delegating to those seams.
- Existing `capture_snapshot` returns metadata plus text (`SnapshotCapture`), allowing diff identity/origin safeguards without exposing metadata in the public value.

## Decisions
- `snapshot()` establishes the baseline and returns only snapshot text.
- `emit()` establishes a new baseline after every capture; automatic emission returns the full value before a baseline exists.
- `open()` clears the snapshot baseline because navigation changes page identity/content.
- `close()` returns `None`; backend status codes are implementation details of the lifecycle adapter.

## Outcomes & remaining gates
The first tested vertical slice is implemented. Remaining work is the staged action-method migration, CLI removal, launcher, skill migration, and distribution release gate; none are part of this slice.
