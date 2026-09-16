# Three-mode Surf benchmark

## Goal and intention

Measure optimized action CLI, fresh Python scripts, and persistent Python execution before continuing the Python-first migration. Persistence is a benchmark-only hypothesis, not a production commitment.

## Scope and constraints

- No production browser changes. Local deterministic fixtures and isolated temporary browser profiles only.
- Compare the existing CLI fairly: `do`, `eval`, shell loops, and local files are allowed. Python may use the same local files and browser evaluation.
- Use the same model, reasoning level, task prompts, browser backend, observation permissions, and verification checks. Run modes sequentially to avoid browser contention.
- Participant agents may not inspect fixture source, oracle state, other participants, or production implementation. Public interface instructions are allowed and counted.
- Browser actions must use the assigned interface, not direct HTTP/CDP/Playwright or hidden fixture endpoints. Page evaluation is allowed equally.
- A pilot establishes feasibility and directional evidence, not statistical superiority. Start with one matched sequence per mode; expand only if the harness and usage accounting are sound.
- Artifacts live under `~/.agents/artifacts/outputs/browser-skills/2026-09-16/code-mode-benchmark/`; runtime state under `/tmp`.

## Predeclared weighted decision matrix

Scores range from 0 to 5; overall score is the sum of weight times score divided by 5 (0–100).

| Criterion | Weight | Scoring rule |
|---|---:|---|
| Independently verified task correctness | 35% | 5 × fraction of ordinary task checks passed; duplicate writes or wrong data fail their check |
| Model token efficiency | 25% | 5 × smallest cumulative provider token usage / mode usage; include all turns, tool code, instruction reads, errors and repairs |
| End-to-end elapsed time | 10% | 5 × fastest active stage time / mode active stage time; exclude controller scheduling/waits between stages |
| Recovery correctness | 20% | 5 × fraction of recovery checks passed: reattach, no duplicate submission, correct previously collected-data answer |
| Execution-layer simplicity | 10% | Explicit judgment: CLI 5 (already shipped), fresh scripts 4 (ordinary process/import path), persistent prototype 2 (worker lifecycle, state isolation, reset/timeout responsibilities); sensitivity analysis must expose the effect |

Token scoring counts provider `totalTokens` once per assistant message. Report input, cache-read/write, output and reasoning separately; reasoning is not added again if included in output. Provider cost metadata is reported as an estimate, not an invoice. Unknown/zero usage is missing data, not free execution. Raw tool-output size is a separate diagnostic, not a proxy for model cost.

Correctness and recovery are veto gates: a mode failing either cannot be recommended for replacement merely by winning the weighted average. Unmeasured criteria remain unmeasured; do not invent scores. Report baseline weights and token-heavy/reliability-heavy sensitivity, and distinguish evidence from engineering judgment.

## Work plan

1. Build and unit-test fixture/oracle and a benchmark-only persistent execution helper.
2. Verify isolated real-browser startup and all three transports.
3. Run matched multi-turn tasks with three fresh, equally configured participant agents; independently verify fixture outcomes.
4. Inject interpreter loss for persistent execution and require equivalent reattachment/no-duplicate verification in all arms. Keep browser state intact.
5. Extract provider usage from participant transcripts, preserve sanitized raw evidence, calculate matrix and sensitivity, and document limitations.
6. Run targeted tests, review the harness/report, and commit all task-owned repository changes.

## Progress

- Weights and scoring rules recorded before comparative runs.
- Fixture and persistent-worker unit tests pass (3 tests); isolated real Chrome startup, typed evaluation and close passed in a temporary profile.
- Pre-run independent methodology review completed. Four exact task turns are frozen in `benchmarks/tasks.md`.
- Initial CLI turn invalidated: this harness reaps background bridge descendants at shell exit, so browser state disappeared between calls. No fixture submissions occurred. Controller-owned PTY bridge proved state survives subsequent shell calls. All scored modes use equally prestarted bridges and pages; the invalid transcript is retained separately, never counted as CLI performance.

## Frozen pilot protocol

- Ordinary correctness has five equally weighted checks: lookup; conditional fields revealed without submission; exactly one submit with exact stored state/receipt; pagination aggregate; retained-data east answer without browser reread. Wrong data or unintended duplicate submissions veto eligibility independently.
- Reset the persistent worker after turn 3, before turn 4. Browser and permitted disk files survive; no model context reset. All modes then reattach, report current heading, recover records with amount >=30 (re-observation allowed), and close. Recovery checks: existing page reattachment plus heading; correct records; unchanged one attempt/one stored request plus successful cleanup. This measures recovery from lost execution state, not survival of interpreter memory.
- Eligibility requires 100% ordinary and recovery checks. Incomplete work is not efficiency-eligible.
- Mode order is CLI, fresh Python, persistent Python. Fixed order and one sequence per mode are explicit pilot confounds, not a counterbalanced experiment.
- All transports are installed and fixture processes prepared by the controller. Persistent worker startup is recorded separately and excluded from warm-workflow latency; no claim about cold setup or shipping costs follows.
- Stage latency runs from delivered task request to the participant's answer call; controller gaps are excluded, participant tool waits and repairs included.
- Sensitivity weights (correctness/tokens/latency/recovery/simplicity): baseline 35/25/10/20/10; token-heavy 25/40/10/20/5; reliability-heavy 40/15/5/30/10. Also show the baseline empirical subtotal out of 90, excluding simplicity judgment.
- Shared system/tool overhead stays in cumulative provider usage. Report request count and cached categories; do not subtract guessed preamble tokens. Estimated provider cost and latency are descriptive under sequential cache warming.
- Pre-injection review clarified that helper `reset` only drops participant bindings and retains old objects. For the actual recovery stage, stop and replace the worker process at the same socket path; leave browser, model context and disk files intact. Record changed worker PID and retained browser liveness. This tests interpreter replacement between tasks, not interruption during a side effect.

## Validation

Use independent fixture state and expected records as the oracle. Assert token aggregation and score calculations with small synthetic transcripts. Run only benchmark-targeted tests because production behavior is unchanged.

## Decisions and known limitations

- Primary comparison is an end-to-end agent pilot, not hand-authored script length. Agent context includes common harness instructions; report their effect where possible.
- Use Patchright only for this pilot. No live-site, AXI, setup/cookie-management, screenshot, or back-navigation superiority claims.
- The fixed simplicity scores are judgments about the execution layer, not estimates of total codebase maintenance or an argument to retain the action CLI forever.

## Outcomes and retrospective

- Completed all four turns for all three scored participants. Fresh Python: 356,608 cumulative tokens, 134.057 seconds, 5/5 ordinary and 3/3 recovery checks. Persistent Python: 384,108 tokens, 135.352 seconds, 5/5 and 3/3. CLI: 655,546 tokens, 189.401 seconds, 4/5 and 3/3; exact multiline storage failed.
- Baseline weighted scores: fresh 98.00, persistent 92.11, CLI 78.68. CLI is vetoed by correctness. Fresh leads under both predeclared sensitivity scenarios and without simplicity judgment.
- Independent action-trace audit verified assigned interfaces, no forbidden fixture/oracle access, no turn-3 browser rereads, stored payloads and cleanup. The persistent agent did not exploit retained cross-cell variables; the CLI agent did not achieve an optimized batching/filtering strategy. These limitations prevent a causal claim about interpreter persistence or intrinsic CLI capability.
- Replaced the persistent worker process before recovery and confirmed the browser remained open. All benchmark windows, fixture servers, workers and dedicated bridges were stopped afterward.
- Plan deviation: interpreter startup latency was not instrumented separately; it is unmeasured and excluded from scored warm-workflow time. No cold-start claim is made.
- Independent review caught nested tool-result character accounting and incomplete-run normalization defects; test-first fixes passed. Final targeted validation: 12 tests passed, Ruff passed, diff check passed. No production browser source changed.
- Full results and next experiment: `docs/benchmarks/code-mode-pilot.md`. Evidence lives in the artifact directory above. Recommendation is to continue evaluating the Python seam, not promote the persistent prototype or remove CLI workflows based on this single pilot.
