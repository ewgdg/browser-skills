# Surf Python replacement

Tracking issue: [#21 — Replace Surf command CLI with a Python-first skill and Thread handles](https://github.com/ewgdg/browser-skills/issues/21). Keep the issue open until installation, skill migration, and CLI removal are verified; implementation slices are not completion.

## Goal
Replace the action-command `surf-agent` CLI with an importable Python interface and a thin skill-local launcher for ordinary file/stdin scripts. Local implementation and installed-artifact validation are complete; publishing and pinning the runtime remain gated.

## Intention
Callers should hold a named `Thread` interaction/ownership context representing Surf's dedicated browser window. They should not parse command strings or capture CLI output to use browser operations.

## Scope & constraints
- Public context is `surf_agent.Thread`; it represents a dedicated window, not tab mode.
- No compatibility shims or migration logic in source.
- Public administration is `surf_agent.Browser`: setup, backend selection, profile/manual login, cookie source/import, inventory and cleanup.
- Reuse existing browser lifecycle and backend seams in `runtime.py`; snapshot values/diff logic live in `snapshots.py`. The old CLI module, entry point, parser, formatting adapters and `do` language are removed.
- Snapshot is an immutable typed value exposing `.text` and identity metadata. `snapshot()` never changes emission state. `emit(snapshot, full=False, sink=None)` writes the already captured value, auto-gating useful diffs; `full=True` always writes the full value.
- Defer interpreter persistence to #22. The launcher runs fresh Python scripts from file/stdin; browser ownership persists independently.
- Do not pin unpushed local code as a reachable remote dependency. Distribution/release is an explicit gate.
- No push, session systems, MCP, or unrelated refactors.

## Work plan
1. Completed: Thread actions, typed values, complete snapshots and explicit emission.
2. Completed: silent administrative Python API, retained lifecycle/cookie safeguards, Google consumer migration, and CLI removal without compatibility shims.
3. Completed: `skills/surf/scripts/run.py`, file/stdin/argument/error handling, wheel validation override and fail-closed release marker.
4. Completed: maintained skill/docs migration; distribution includes launcher and references without bundled runtime source.
5. Completed: full tests, independent review, real Chrome navigation and installed-artifact acceptance outside checkout.
6. Pending authorization: publish the tested runtime commit, pin its reachable full SHA, publish the skill pin, and run installed acceptance without the wheel override. Keep #21 open until this gate passes.

## Validation
- `uv run pytest -q`: **227 passed, 4 skipped, 3 subtests**. Skips: opt-in live Google, two live navigation cases, and installed-browser acceptance.
- `uv run pytest benchmarks -q`: **12 passed**. Historical CLI comparisons must use the recorded benchmark revision, not restore the removed CLI.
- `uv run ruff check packages tests benchmarks` and `git diff --check`: passed.
- Built the Python wheel and `npm pack` skill archive; extracted the archive outside the checkout. The actual launcher installs that wheel through uv, with no workspace/PYTHONPATH dependency and no separately installed action CLI.
- Opt-in acceptance plus BFCache/reload navigation: **3 passed**. Covers stdin opening, a separate file invocation with spaces/arguments and same-name reattachment, exact multiline input through native refs, click/text wait, full/diff/full emission, screenshot, back-navigation, typed administrative inventory and cleanup. The unrelated working directory has an incompatible project config, which the launcher ignores.
- Independent ordinary project import from the built wheel passes without the launcher; installed distribution has no console-script entry point. Launcher tests also verify sibling imports, script stdin, exception/nonzero exit propagation, missing uv, invalid dependency and fail-closed unreleased revision.
- Separate agent shell tool invocations against the extracted skill also passed setup/profile inspection, browser continuity, back-navigation and cleanup without a prestarted bridge. All browser profiles and ports used for validation were isolated; no user login/cookies were accessed.
- Real AXI, live credential login and a published no-override install were not exercised. Retained backend/profile/cookie lifecycle behavior has regression coverage; these are not claims of live AXI/auth validation.

## Progress
- Completed the replacement, including administrative operations, the fresh-script launcher, installed skill documentation and CLI removal. Removed obsolete DSL tests while migrating retained lifecycle/backend/cookie coverage.
- Release constraint: no push is authorized by this plan. An unreleased launcher must fail clearly rather than pin an unavailable SHA; local built-wheel validation is separate evidence from a published pinned install.
- Added `surf_agent.thread.Thread` with open/state, interaction, observation, evaluation, screenshot, and close methods; construction is `Thread(name=...)` and uses a private backend factory seam.
- Added test-first coverage for silent snapshots, last-emitted baselines, no recapture during emission, explicit full emission, output failure, navigation/independent handles, close failure, delegation, and unsafe names.
- Exported `Thread` from the package root.
- Added truthful staged API documentation and migrated `SurfBrowserPagePort` to the object interface.

## Surprises & discoveries
- Fresh Python continuity succeeds in independent shell calls in the current harness without a controller-owned bridge. Do not generalize the earlier benchmark harness failure into a production lifecycle defect without reproduction.
- Reproduced the historical back-navigation timeout on a two-page HTTP fixture. Chrome BFCache restores a complete document without another DOMContentLoaded event; the original Patchright wait timed out after navigating successfully. Waiting for history commit followed by document readiness fixes both BFCache restoration and actual reload, with live regression coverage for each and empty/same-document history.
- Installed-wheel validation caught the administrative thread-list decoder assuming a bare list while the real bridge wraps it; the public API must be tested against that real response shape.
- Independent review caught backend switching losing prior-runtime cleanup during extraction. Restored cleanup before config mutation, ignoring temporary environment overrides; failure preserves prior config. Added regression cases for changed/unchanged/default/invalid selections and both backend directions.
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
- `close()` uses the single silent backend close seam, returns `None` on status 0/None, and raises `SurfAgentError` for nonzero backend status.
- `wait(int)` means milliseconds and `wait(str)` means visible text; backend adapters expose separate typed seams.
- `evaluate()` returns transport-decoded Python values without coercing string scalars; malformed transport is an error.
- `setup()` validates prerequisites. The launcher installs the Patchright extra; Chrome installation remains explicit, outside package setup.
- `set_backend()` stops the previous persisted backend before writing selection; `reset_backend()` only clears selection, so stop runtime explicitly first.
- AXI `reset()` forgets local ownership without closing the window. Patchright rejects reset before mutation instead of preserving the old silent no-op for bridge-held ownership.
- `runtime-revision` is deliberately `UNRELEASED`. The wheel override is explicit validation-only, not an implicit fallback or a claim that an unpushed commit is installable.

## Outcomes & remaining gates
The local implementation and actual installed-artifact workflow are validated. Only authorized publication, reachable commit pinning and the final no-override install remain completion gates. No push or global user-skill replacement was performed. The plan stays active and #21 stays open.

### Earlier checkpoints (superseded by complete-replacement validation above)
- Thread, existing CLI, lifecycle, and all Google Search tests: **191 passed, 1 skipped, 28 subtests passed**. The skipped test is the opt-in live Google test; DOM fixture tests ran. Changed-file Ruff and diff checks passed.
- Real Chrome with a local fixture passed thread reattachment/state, native-ref fill/click, typing/keys, text/duration waits, full observations, automatic diffs, typed evaluation, scrolling, screenshots, navigation, and silent cleanup. A 7,448-character complete observation emitted a 523-character diff; this is not a model-token benchmark.
- AXI bridge-transport tests exposed and fixed numeric-text waits skipping owned-page selection and typed evaluation dropping multiline JSON. No real AXI browser was exercised.
- The initial smoke exposed a 15-second back-navigation timeout. This gap is now resolved by the BFCache-aware fix and live regression tests described above.

### Review fixes (2026-09-16)
- Reproduced AXI false-open state with a remembered page absent from the running bridge's inventory. `is_open()` now checks inventory without starting or selecting a page, discards confirmed stale state, retains state when the bridge is unavailable, and fails on malformed inventory.
- Reproduced concatenated emissions from unterminated snapshots. Emission now terminates nonempty output while leaving complete snapshot values unchanged.
- Six new regression cases failed before the fixes and passed afterward. That earlier Thread/CLI/lifecycle/Google checkpoint had **197 passed, 1 skipped, 28 subtests passed**; changed-file Ruff passed. No live AXI browser run.
