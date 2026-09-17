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

## Decisions settled in design review

Two constraints settled these. First: **a cell whose outcome is uncertain must be reportable from outside the cell.** Anything that times, executes or dies inside the cell cannot report its own fault. Second: **a cell behaves exactly like a script.** No second dialect that breaks when the same code moves to `run.py FILE`, a project import, or a different kernel.

- **Runtime (was open decision 1): out-of-process supervisor, `ipykernel` + thin client as the default.** The launcher that started the cell must survive it, because it is the only component able to report "your interpreter is gone". An in-process worker cannot: a timer fires inside the very code it is timing, and a crash takes the report with it. `benchmarks/persistent.py` is excluded by this rule on its own (in-process `exec` plus `SIGALRM`), independently of its other problems. A hand-rolled *out-of-process* worker remains an acceptable fallback if kernel stream/`display_data` semantics fight the observation framing.
- **Busy and timeout behaviour (was open decision 3): per-cell, caller-set timeout that destroys the interpreter.** Not a background continuation, and never a rollback. A blocked cell makes new cells fail fast rather than interleave. The caller raises the limit for a long operation, as Codex's per-call `timeout_ms` does.
- **Session lifecycle (was open decision 4): the interpreter lives exactly as long as the session that created it.** Established by an owner-process reference, not a heartbeat and not an idle timer. The interpreter records the process that created it and exits once that process is gone — liveness by `/proc/<pid>/cmdline` with the process start time as a pid-reuse guard, the technique `packages/surf-agent/src/surf_agent/chrome_lifecycle.py` already uses to resolve live Chrome roots. A heartbeat asks "is anyone there" when the question is "is my owner alive", and the only party that could send one would spend cells doing it. A long idle backstop may remain for pathologies. Consequence: a session restart always yields a new interpreter, which is predictable and cheap to recover from.
- **Environment pinning (was open decision 5): fixed at interpreter creation and not re-read per cell.** `SURF_AGENT_HOME`, backend selection and bridge port cannot drift onto a different profile mid-task, and reset discards bindings without discarding configuration. Codex keeps added module directories across reset for the same reason.
- **Reset contract (was open decision 6): Python bindings only.** Browser threads, pages and files survive untouched. This is decision 4 of "Decisions already made", and matches Codex's `js_reset`: "All JavaScript bindings are discarded… This does not close browser tabs or native apps, or erase their state."
- **Cell result convention (was open decision 2): explicit `print()`/`emit()` only, never REPL echo.** Echo is a second dialect, which the explicit-imports decision in "Decisions already made" already rejects: code that depends on it stops working the moment it becomes `run.py FILE` or a project import. It also bypasses the observation framing — a cell ending in `thread.snapshot()` would print raw text with no `--- BEGIN observation N ---` boundary and advance no baseline, which is where stale-baseline bugs come from. And it couples the runtime to the kernel, since REPL echo is an `ipykernel` feature; choosing it would force the out-of-process fallback to reimplement REPL semantics, leaving that fallback nominal rather than real. `emit()` stays the only writer, so code behaves identically in a cell, a file and an import. The token saving is unmeasured — the pilot that suggested it carries the contradictory-instruction defect — and belongs in the retention benchmark rerun if it is ever wanted; it can be added later without disturbing the framing contract, whereas retrofitting framing discipline onto echo would be breaking. Codex shows the hazard from the other side: its observation APIs emit internally and warn that wrapping them in `nodeRepl.write` duplicates output, which is why it needs a separate `{ emit: false }` control.

Long jobs that must outlive the session are not the session interpreter's problem; they need their own process or files on disk.

No open decisions remain before implementation.

## Replay contract

Replay means re-running the same cell. It is never automatic. The three questions it raises have different truth sources, and only the first is answerable by the interpreter:

| Question | Truth source | Contract |
|---|---|---|
| Does the interpreter still hold my bindings? | interpreter identity, reported outside the cell | every cell result states interpreter identity, a monotonic per-interpreter cell counter, and created-vs-attached |
| Did the failed cell's browser action take effect? | the named `Thread` | never inferred from the fault; inspect the same thread with a full observation before deciding |
| Is my emission baseline still valid? | the baseline lives on the handle, so it dies with the interpreter | a replaced interpreter's first emission is full by construction |

Rules:

- A fault result classifies **interpreter state only** ("interpreter replaced, bindings lost"). It never states or implies an outcome for side effects.
- The launcher prints identity and the cell counter, never the cell itself. A cell that dies mid-execution prints nothing, so a cell-printed header would put the agent back to inferring.
- The recovery cell is the initialization cell: `Thread(name)` reattaches idempotently and `emit(snapshot(), full=True)` re-establishes both the handle and the baseline. It is correct whether the interpreter is new or old, so the agent does not branch on the header before acting.
- Rebind before acting; inspect before repeating. Replaying a pure observation is harmless and replaying a submission is not, and a cell cannot be classified reliably enough to tell them apart.

Design reference, not an implementation dependency: installed Codex bundle `26.908.61612`, package label `0.1.0-premerge-…`. Its `js` tool takes an optional per-call `timeout_ms` (default 30000), and any execution fault destroys the kernel and says so — `js execution timed out; kernel reset, rerun your request`, `trusted Node process exited unexpectedly; kernel reset, rerun your request`, `js sandbox changed; kernel reset, rerun your request`. Replay stays a fresh model decision rather than an automatic resend, and the fault text never states whether effects landed. Its workflow compensates structurally: `getAXState()` after every action batch, element indices re-derived from fresh text, and idempotent getters (`getTab`, `getState`) as the rebind path. Read from `/usr/lib/chatgpt/resources/cua_node/bin/node_repl` (strings) and the shipped markdown under `.../@oai/cua/docs/` and `.../@oai/cua-repl/instructions/`.

## Evidence from the first benchmark

`docs/benchmarks/code-mode-pilot.md`, protocol in `plans/done/code-mode-benchmark.md`.

- Fresh Python 356,608 cumulative tokens; persistent Python 384,108 (+7.7%); CLI 655,546.
- The extra cost was **workflow**, not persistence overhead: 21 vs 17 model calls, four extra submission/pagination calls, one extra cleanup call, one fewer lookup call. Tool-result text was actually smaller for persistent.
- The persistent participant rebuilt its handle in every cell and did not reuse cross-cell values; all modes answered the follow-up from conversation. So the pilot measured persistence *available*, not persistence *used*.
- Instruction wording was mixed: the prompt said globals/helpers survive, while the public API doc still called interpreter persistence "planned".

Treat that as a harness/wording defect to fix, not a verdict on persistence.

## Concrete risks to address with a failure case

- **Silent session loss.** If a later cell lands in a different interpreter, cached data disappears mid-task. Prediction: with session identity mismatched, a retained variable raises `NameError` instead of returning stale data. The replay contract removes the silence — the cell header reports a new identity and a reset cell counter — so the remaining failure case to test is that the header is produced even when the cell dies.
- **Namespace leakage between concurrent sessions.** Two agents must not share globals. Prediction: writing a marker in session A is invisible in session B.
- **Output framing across cells.** Observation IDs are per-process; a resumed process restarts at 1. Do not let a diff header point at an observation number from a previous process without an explicit full observation.
- **Deadline semantics.** Native/blocking browser calls may not be interrupted; a timeout leaves outcome uncertain.
- **Emitting a stale baseline.** If model context no longer contains a prior emission, the agent must request full output rather than trust a diff.

## Validation plan

- Test-first at the execution seam: sequential cells, retained bindings, per-cell output/error capture, reset, timeout, busy rejection, session isolation, crash/stale-session detection, cleanup.
- Owner-death reaping: the interpreter exits after the process that created it disappears, without a heartbeat or an equivalent keepalive.
- Header identity: a replaced interpreter reports a new identity and a reset cell counter, and its first emission is full rather than a diff against a baseline the agent no longer holds.
- Live browser acceptance extending `tests/test_installed_workflow.py`: initialize once, act across several cells, replace the interpreter, reattach to the surviving browser thread, close.
- Rerun the retention-focused benchmark from #22: larger dataset, unrevealed follow-up queries, equivalent batching/retention guidance for every mode, files allowed for fresh scripts, counterbalanced order, distributions and uncertainty-side-effect recovery reported separately.
- Confirm the ordinary file/stdin launcher and project import paths still work unchanged.

## References

- `skills/surf/SKILL.md` — agent workflow; `skills/surf/docs/python-api.md` — Thread/Browser contracts; `skills/surf/docs/launcher.md` — execution/release.
- `packages/surf-agent/src/surf_agent/thread.py`, `snapshots.py` — emission framing and baselines.
- `benchmarks/persistent.py` — benchmark-only prototype, do not promote.
- `tests/test_installed_workflow.py`, `tests/test_skill_launcher.py` — existing installed-skill coverage.
