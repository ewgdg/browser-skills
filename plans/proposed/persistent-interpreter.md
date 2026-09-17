# Persistent Python interpreter for Surf agent sessions

Tracking issue: [#22](https://github.com/ewgdg/browser-skills/issues/22). Status: proposed, not started. This plan is written for a fresh agent continuing the work with no prior conversation.

## Goal

An agent session keeps one isolated Python interpreter alive across tool calls and user turns, so handles, helpers and intermediate data persist. Add persistence at the **execution layer**; `Thread`/`Browser` semantics do not change.

## Intention

Today every script is a fresh interpreter: each call re-imports, reattaches by thread name and re-derives anything not written to disk. That is correct but repetitive for multi-step browser work. Persistence should make the session feel continuous while keeping ordinary file/stdin scripts working unchanged.

## Current state (already released)

- Surf skill is pinned to runtime `aa3e8d455c8c0bb26dd13a903c16e43e9c9cfdaf`; installed skill and repo match.
- Normal entry point: `python3 "$SURF_SKILL/scripts/run.py" FILE|- [args]` — a fresh interpreter per call. Dev-only override `SURF_AGENT_DEPENDENCY=/abs/path.whl`.
- Browser threads already persist across interpreter lifetimes. Only Python-level state does not.
- Observations use numbered `--- BEGIN observation N ---` / `--- END observation N ---` frames; diffs use `--- observation PREV` / `+++ observation N`. IDs are per-process; baselines are per-handle and last-successful-write.
- Release guardrail: `tests/test_skill_distribution.py` fails if the shipped skill has a missing/invalid runtime pin.
- Do not promote `benchmarks/persistent.py` as production. It is a trusted-code benchmark helper; its `reset()` clears names but keeps old objects alive, it uses `SIGALRM`, and it cannot preempt native blocking code.

## Decisions already made (do not relitigate)

1. **Explicit imports stay.** No auto-injected `Thread`/`Browser` names. Two dialects (worker vs file launcher) would break code the moment it moves to a script, and injected names hide shadowing.
2. **No pre-import of modules.** Measured: bare start 8 ms, `import surf_agent` 8 ms (lazy package), `from surf_agent import Thread` 82 ms, re-import ~0 ms. Pre-warming saves ~74 ms once per session — not worth the extra startup path.
3. **Interpreter lifetime is independent of browser lifetime.** Replacing the interpreter must not close the browser; browser and permitted files survive.
4. **Reset is not browser rollback.** Interpreter reset clears Python bindings only.
5. **No automatic replay.** After a timeout or lost response, an action may already have taken effect; inspect first, never blind-retry a submission.
6. **One isolated interpreter per agent session**, sequential cells.

## Open decisions (settle before implementing)

1. **Runtime.** Evaluate `ipykernel` + a thin client first: it already provides per-cell stdout/stderr capture, timeouts/interrupts, execution counts and crash detection. Fall back to a small hand-rolled worker only if kernel stream/`display_data` semantics fight our observation framing. Do not silently use the benchmark worker as production.
2. **Cell result convention.** Require explicit `print()`/`emit()`, or echo the final expression like a REPL? REPL echo reduces tokens but can double-emit next to `emit()`.
3. **Busy and timeout behaviour.** A blocked cell must make new cells fail fast rather than interleave. A timeout must never imply rollback or trigger a retry.
4. **Session lifecycle.** Who creates a session, how it is named, how it is discovered by a later tool call, and how it is stopped/cleaned up.
5. **Environment pinning.** `SURF_AGENT_HOME`, backend selection and bridge port must be fixed at session start so a session cannot drift onto a different profile mid-task.
6. **Reset contract.** What exactly survives reset (handle objects? files? nothing?), and how the agent is told.

## Evidence from the first benchmark

`docs/benchmarks/code-mode-pilot.md`, protocol in `plans/done/code-mode-benchmark.md`.

- Fresh Python 356,608 cumulative tokens; persistent Python 384,108 (+7.7%); CLI 655,546.
- The extra cost was **workflow**, not persistence overhead: 21 vs 17 model calls, four extra submission/pagination calls, one extra cleanup call, one fewer lookup call. Tool-result text was actually smaller for persistent.
- The persistent participant rebuilt its handle in every cell and did not reuse cross-cell values; all modes answered the follow-up from conversation. So the pilot measured persistence *available*, not persistence *used*.
- Instruction wording was mixed: the prompt said globals/helpers survive, while the public API doc still called interpreter persistence "planned".

Treat that as a harness/wording defect to fix, not a verdict on persistence.

## Concrete risks to address with a failure case

- **Silent session loss.** If a later cell lands in a different interpreter, cached data disappears mid-task. Prediction: with session identity mismatched, a retained variable raises `NameError` instead of returning stale data.
- **Namespace leakage between concurrent sessions.** Two agents must not share globals. Prediction: writing a marker in session A is invisible in session B.
- **Output framing across cells.** Observation IDs are per-process; a resumed process restarts at 1. Do not let a diff header point at an observation number from a previous process without an explicit full observation.
- **Deadline semantics.** Native/blocking browser calls may not be interrupted; a timeout leaves outcome uncertain.
- **Emitting a stale baseline.** If model context no longer contains a prior emission, the agent must request full output rather than trust a diff.

## Validation plan

- Test-first at the execution seam: sequential cells, retained bindings, per-cell output/error capture, reset, timeout, busy rejection, session isolation, crash/stale-session detection, cleanup.
- Live browser acceptance extending `tests/test_installed_workflow.py`: initialize once, act across several cells, replace the interpreter, reattach to the surviving browser thread, close.
- Rerun the retention-focused benchmark from #22: larger dataset, unrevealed follow-up queries, equivalent batching/retention guidance for every mode, files allowed for fresh scripts, counterbalanced order, distributions and uncertainty-side-effect recovery reported separately.
- Confirm the ordinary file/stdin launcher and project import paths still work unchanged.

## References

- `skills/surf/SKILL.md` — agent workflow; `skills/surf/docs/python-api.md` — Thread/Browser contracts; `skills/surf/docs/launcher.md` — execution/release.
- `packages/surf-agent/src/surf_agent/thread.py`, `snapshots.py` — emission framing and baselines.
- `benchmarks/persistent.py` — benchmark-only prototype, do not promote.
- `tests/test_installed_workflow.py`, `tests/test_skill_launcher.py` — existing installed-skill coverage.
