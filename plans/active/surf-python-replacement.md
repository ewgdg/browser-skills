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
- Independent verification: targeted Thread/CLI/Google browser-port/search-lifecycle tests passed (146 tests, 28 subtests); Ruff and `git diff --check` passed.
- A real Patchright/Chrome smoke run used a unique thread and a local HTTP page: open, capture, emit, observe a dynamic page change, emit a diff, request full output, and close. Complete text was 8,103 characters; incremental output was 306 characters. This is one character-count observation, not a model-token benchmark. Cleanup removed the smoke thread state.
- Full suite only after the staged replacement is complete.

## Progress
- Added `surf_agent.thread.Thread` with open/state, interaction, observation, evaluation, screenshot, and close methods; construction is `Thread(name=...)` and uses a private backend factory seam.
- Added test-first coverage for silent snapshots, last-emitted baselines, no recapture during emission, explicit full emission, output failure, navigation/independent handles, close failure, delegation, and unsafe names.
- Exported `Thread` from the package root.
- Added truthful staged API documentation and migrated `SurfBrowserPagePort` to the object interface.

## Surprises & discoveries
- Existing `SurfAgent` already centralizes backend selection, lifecycle startup, profile safety, and snapshot diff gating. The new interface can stay small by delegating to those seams.
- Before migration, `surf-google-search` called `SurfAgent.execute_in_window` directly and redirected stdout around `print_state` and `close`; it did not shell out to the CLI.
- Existing `capture_snapshot` returns metadata plus text (`SnapshotCapture`), allowing diff identity/origin safeguards while `.text` remains the complete accessibility text.
- The object consumer can query `is_open()` without starting a missing bridge; evaluation decoding now stays in backend implementations.
- `SurfBrowserPagePort` now depends on Thread-shaped methods and accepts decoded Python evaluation values. Its observation script returns objects directly; the old nested JSON decoding is removed.
- Typed waits now use separate duration/text backend seams, preserving numeric strings as text; AXI rejects numeric-text fallback when its bridge is unavailable rather than silently treating it as milliseconds.
- Malformed local state/evaluation transport raises `SurfAgentError` instead of being converted to a closed state or raw string.

## Decisions
- `snapshot()` returns an immutable `Snapshot` value (the existing `SnapshotCapture` shape, including identity metadata) and never changes emission state.
- `emit(snapshot, full=False, sink=None)` writes the already captured value/diff and never recaptures. Its baseline is the last successfully emitted snapshot; baseline advances only after a successful write.
- `open()` clears the emission baseline because navigation changes page identity/content. A new handle has independent in-memory state.
- Baselines are handle-local, not persisted across script invocations. Use one handle per observation consumer; a fresh handle starts with full output.
- `close()` uses the backend's silent close seam, returns `None` on status 0/None, and raises `SurfAgentError` for nonzero backend status. The CLI-facing backend close method retains its existing formatted output.
- `wait(int)` means milliseconds and `wait(str)` means visible text. Backend adapters expose separate typed seams; the staged CLI's string conversion remains unchanged.
- `evaluate()` returns transport-decoded Python values without coercing string scalars; malformed transport is an error.

## Outcomes & remaining gates
The first two tested slices plus contract hardening are implemented: capture/emission, object actions/Google consumer migration, and typed wait/evaluation/state semantics. Remaining work is setup/login/profile/cookie workflow migration, CLI removal, launcher, skill migration, and distribution release gate; none are part of this slice.

### Second-slice independent validation (2026-09-16)
- Thread, existing CLI, lifecycle, and all Google Search tests: **191 passed, 1 skipped, 28 subtests passed**. The skipped test is the opt-in live Google test; DOM fixture tests ran. Changed-file Ruff and diff checks passed.
- Real Chrome with a local fixture passed thread reattachment/state, native-ref fill/click, typing/keys, text/duration waits, full observations, automatic diffs, typed evaluation, scrolling, screenshots, navigation, and silent cleanup. A 7,448-character complete observation emitted a 523-character diff; this is not a model-token benchmark.
- AXI bridge-transport tests exposed and fixed numeric-text waits skipping owned-page selection and typed evaluation dropping multiline JSON. No real AXI browser was exercised.
- **Open validation gap:** the first smoke timed out after 15 seconds in the existing Patchright `go_back(wait_until="domcontentloaded")` path. Immediate cleanup was blocked while that operation occupied the bridge; a subsequent close succeeded. The passing smoke excluded back-navigation. No navigation-runtime fix was included; diagnose separately before treating back-navigation as live-validated.

### Review fixes (2026-09-16)
- Reproduced AXI false-open state with a remembered page absent from the running bridge's inventory. `is_open()` now checks inventory without starting or selecting a page, discards confirmed stale state, retains state when the bridge is unavailable, and fails on malformed inventory.
- Reproduced concatenated emissions from unterminated snapshots. Emission now terminates nonempty output while leaving complete snapshot values unchanged.
- Six new regression cases failed before the fixes and passed afterward. Targeted Thread/CLI/lifecycle/Google suites: **197 passed, 1 skipped, 28 subtests passed**; changed-file Ruff passed. No live AXI browser run; the independent back-navigation gap remains unchanged.
