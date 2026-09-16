# Surf Python replacement

## Goal
Replace the action-command `surf-agent` CLI with a small importable Python interface. Keep browser lifecycle and backend implementations behind a deep module interface; a thin skill-local launcher will later provide file/stdin execution for ordinary Python scripts.

## Intention
Callers should hold a named `Thread` interaction/ownership context representing Surf's dedicated browser window. They should not parse command strings or capture CLI output to use browser operations.

## Scope & constraints
- Public context is `surf_agent.Thread`; it represents a dedicated window, not tab mode.
- No compatibility shims or migration logic in source.
- Current slice: `Thread(name=...)` with open/state, interaction, observation, evaluation, screenshot, and close methods; snapshot/emit contracts remain unchanged.
- Reuse existing `SurfAgent` setup, browser lifecycle, and backend seams. Object actions remain silent while CLI formatting stays in CLI-facing methods.
- Snapshot is an immutable typed value exposing `.text` and identity metadata. `snapshot()` never changes emission state. `emit(snapshot, full=False, sink=None)` writes the already captured value, auto-gating useful diffs; `full=True` always writes the full value.
- Defer interpreter persistence. The future launcher runs normal Python scripts from file/stdin.
- Do not pin unpushed local code as a reachable remote dependency. Distribution/release is an explicit gate.
- No push, session systems, MCP, or unrelated refactors.

## Work plan
1. Establish `Thread` as the public interaction seam and cover behavior at that seam (complete for the first slice).
2. Move remaining browser actions behind object methods without CLI parsing or print capture.
3. Inventory and migrate all skill workflows before removing the action-command entrypoint: backend setup/default selection, manual login/unblock, profile open/show, cookie-source configuration and import, bridge stop/cleanup, and thread cleanup.
4. Migrate `surf-google-search` consumers from its direct `SurfAgent` dependency (`execute_in_window`, `print_state` with stdout redirection, and `close`) to the object interface (complete for this slice; preserve its one-thread-at-a-time coordination and challenge handling), then remove obsolete command parsing.
5. Add a thin skill-local launcher that installs a commit-pinned Git dependency and executes ordinary Python from file/stdin.
6. Update skill instructions and installation/release documentation; only pin published/reachable commits.

## Validation
- Current slice: targeted Thread and Google browser-port tests plus existing backend/lifecycle tests.
- Tests fail fast and observe only `Thread`'s public interface; fakes stand in for the existing backend seam.
- Independent verification: `uv run --package surf-agent pytest -q packages/surf-agent/tests/test_thread.py packages/surf-agent/tests/test_chrome_lifecycle.py packages/surf-agent/tests/test_cli.py` passed (130 tests, 28 subtests). Ruff on changed Python files and `git diff --check` passed.
- A real Patchright/Chrome smoke run used a unique thread and a local HTTP page: open, capture, emit, observe a dynamic page change, emit a diff, request full output, and close. Complete text was 8,103 characters; incremental output was 306 characters. This is one character-count observation, not a model-token benchmark. Cleanup removed the smoke thread state.
- Full suite only after the staged replacement is complete.

## Progress
- Added `surf_agent.thread.Thread` with open/state, interaction, observation, evaluation, screenshot, and close methods; construction is `Thread(name=...)` and uses a private backend factory seam.
- Added test-first coverage for silent snapshots, last-emitted baselines, no recapture during emission, explicit full emission, output failure, navigation/independent handles, close failure, delegation, and unsafe names.
- Exported `Thread` from the package root.
- Added truthful staged API documentation and migrated `SurfBrowserPagePort` to the object interface.

## Surprises & discoveries
- Existing `SurfAgent` already centralizes backend selection, lifecycle startup, profile safety, and snapshot diff gating. The new interface can stay small by delegating to those seams.
- `surf-google-search` does not shell out to the CLI today; `SurfBrowserPagePort` directly calls `SurfAgent.execute_in_window`, redirects stdout for `print_state`, and redirects stdout around `close`.
- Existing `capture_snapshot` returns metadata plus text (`SnapshotCapture`), allowing diff identity/origin safeguards while `.text` remains the complete accessibility text.
- The object consumer can query `is_open()` without starting a missing bridge; evaluation decoding now stays in backend implementations.

## Decisions
- `snapshot()` returns an immutable `Snapshot` value (the existing `SnapshotCapture` shape, including identity metadata) and never changes emission state.
- `emit(snapshot, full=False, sink=None)` writes the already captured value/diff and never recaptures. Its baseline is the last successfully emitted snapshot; baseline advances only after a successful write.
- `open()` clears the emission baseline because navigation changes page identity/content. A new handle has independent in-memory state.
- Baselines are handle-local, not persisted across script invocations. Use one handle per observation consumer; a fresh handle starts with full output.
- `close()` uses the backend's silent close seam, returns `None` on status 0/None, and raises `SurfAgentError` for nonzero backend status. The CLI-facing backend close method retains its existing formatted output.

## Outcomes & remaining gates
The first two tested slices are implemented: capture/emission and object actions plus the Google consumer migration. Remaining work is setup/login/profile/cookie workflow migration, CLI removal, launcher, skill migration, and distribution release gate; none are part of this slice.
