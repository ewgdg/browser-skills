# Code-mode pilot: fresh scripts lead; persistence is not yet demonstrated

Date: 2026-09-16. Baseline browser code: `0e1976cd7844f4b2073ec15badcc50316238c639`.

## Decision

Continue evaluating the Python `Thread` interface. Do not promote the benchmark's persistent worker into production based on this run, and do not remove CLI workflows based on one sequence.

Fresh Python scored **98.0/100**, persistent Python **92.1/100**, and CLI **78.7/100**. Fresh Python used **45.6% fewer cumulative model tokens than CLI** and **7.2% fewer than persistent Python**. Both Python runs passed the stored-state checks; CLI failed exact multiline storage.

The most important qualification: **the persistent participant did not meaningfully use retained interpreter state**. It reconstructed its `Thread` handle in each browser cell and answered the later data question from conversation, just like the other modes. This measures the agent's realized workflow with persistence available, not the potential benefit of deliberately exploiting persistence. Likewise, CLI optimization was permitted, not achieved: the participant used many separate commands and full snapshots.

## What ran

Three fresh agents used `openai-codex/gpt-5.6-luna`, high reasoning, the same tools and permissions, and four staged requests each. Their conversations continued between requests. Run order was CLI, fresh Python, persistent Python. Each arm had its own local fixture, browser profile, bridge port and named thread.

Environment: Python 3.13.15, Patchright 1.60.1, Google Chrome 152.0.7977.82. Browser bridges and initial pages were prestarted equally. No user profile, cookie import or external website was used.

1. Read heading/link/daily code; select the form type and reveal conditional fields without submitting.
2. Submit exact multiline/Unicode text once; collect and aggregate all eight records across three pages.
3. Answer a new region question without rereading browser pages.
4. Reattach after execution-state loss, answer another record question and close the thread. Re-observation was permitted for recovery.

Before persistent turn 4, the controller stopped and replaced the worker process, while preserving the independently owned browser, model conversation and permitted scratch files. The worker PID changed from 2158617 to 2183404; browser liveness was verified. This was **between-task interpreter replacement**, not an interrupted submit or an uncertain side-effect test.

The controller verified form state through a bearer-protected oracle, checked revealed fields directly, audited the no-reread turn, checked recovery observation outputs, and verified final browser closure. The oracle does not prevent a hostile agent with local filesystem access; source/oracle access was forbidden and traces were audited.

## Measured results

| Metric | CLI | Fresh Python | Persistent Python |
|---|---:|---:|---:|
| Ordinary checks passed | 4/5 | 5/5 | 5/5 |
| Recovery checks passed | 3/3 | 3/3 | 3/3 |
| Cumulative provider tokens | 655,546 | **356,608** | 384,108 |
| Uncached input tokens | 131,665 | 102,193 | 94,853 |
| Cached input tokens | 517,632 | 249,344 | 283,904 |
| Output tokens | 6,249 | 5,071 | 5,351 |
| Reported reasoning tokens, already included in output | 2,290 | 1,919 | 1,138 |
| Model requests | 28 | **17** | 21 |
| Active stage time, seconds | 189.401 | **134.057** | 135.352 |
| Provider-reported cost estimate, USD | 0.04418 | 0.03151 | 0.03107 |
| Model-visible tool-result characters | 115,925 | 89,246 | 83,322 |
| Generated execution-code characters | 11,526 | 8,620 | 12,437 |

Tokens are the sum of provider `totalTokens` across every model request, including cached and repeatedly processed history. They are **not unique task-text tokens**. Reasoning is reported separately but never added again. Tool characters are a diagnostic, not token estimates. Controller implementation, verification and review tokens are excluded equally; these figures measure participant task execution only.

Stage time runs from request delivery to the participant's answer call, excluding controller gaps and setup. It includes participant execution waits and repair attempts. Sequential cache warming and service latency are uncontrolled, so cost and elapsed time are descriptive, not intrinsic interface performance. Persistent Python's estimated cost was about 1.4% lower than fresh Python despite using 7.7% more tokens, illustrating why these measures must remain separate.

### Tokens by stage

| Stage | CLI | Fresh Python | Persistent Python |
|---|---:|---:|---:|
| Lookup and conditional fields | 87,282 | 107,035 | 66,616 |
| Exact submission and aggregation | 417,959 | 156,222 | 203,614 |
| Retained-data question | 35,760 | 30,605 | 27,811 |
| Recovery and cleanup | 114,545 | 62,746 | 86,067 |

The modes do not rank identically on every stage. Shared prompt/history costs and agent strategy matter substantially.

### Correctness findings

- CLI flattened the Notes line breaks before submitting. The oracle confirmed exactly one stored request and no duplicates, but wrong text. Its transcript shows an agent-authored flattened value; this is **not evidence that the CLI cannot preserve multiline text**. Reading the DOM text through `eval` remained available.
- Both Python modes stored the exact expected payload, including newlines and one literal backslash. Their textual answers displayed two backslashes before `path`; the frozen check grades exact stored state/receipt, not character-perfect final-answer transcription. Do not generalize the pass to every character of their prose.
- All modes returned the correct aggregates and region IDs. All turn-3 responses came directly from conversation with no browser calls. All recovered the existing page, returned the requested records, avoided further submissions and closed their browser thread.
- Fresh Python incurred a failed text-target click and a local header-row parsing error before recovery. CLI incurred quoting/interaction repairs. These costs are included; successful runs were not cherry-picked after retries.

## Predeclared weighted decision matrix

Scores range from 0 to 5; weighted total is `sum(weight × score / 5)`. Correctness/recovery scores are passing fractions × 5. Token/time scores are `5 × best observed value / mode value`. Incomplete runs receive no efficiency credit and cannot set efficiency minima.

| Criterion | Weight | CLI score | Fresh Python score | Persistent Python score |
|---|---:|---:|---:|---:|
| Verified ordinary correctness | 35% | 4.00 | 5.00 | 5.00 |
| Cumulative-token efficiency | 25% | 2.72 | 5.00 | 4.64 |
| Active elapsed time | 10% | 3.54 | 5.00 | 4.95 |
| Recovery correctness | 20% | 5.00 | 5.00 | 5.00 |
| Execution-layer simplicity, judgment | 10% | 5.00 | 4.00 | 2.00 |
| **Weighted total /100** | **100%** | **78.68** | **98.00** | **92.11** |
| Empirical subtotal /90, no simplicity judgment | | 68.68 | **90.00** | 88.11 |
| Passes pilot correctness/recovery gates | | **No** | Yes | Yes |

The simplicity scores were fixed before the runs: shipped CLI 5, ordinary process/import path 4, persistent worker lifecycle/reset/isolation responsibilities 2. These are judgments about the execution layer, not measured maintenance effort or total architecture quality. Removing this judgment does not change the observed ordering.

Correctness and recovery are veto gates: all checks must pass. CLI is ineligible in this pilot regardless of its score. An eligible pilot score is not authorization to remove other interfaces.

### Sensitivity to weights

Weight order: correctness / tokens / time / recovery / simplicity.

| Scenario | Weights | CLI | Fresh Python | Persistent Python |
|---|---|---:|---:|---:|
| Baseline | 35/25/10/20/10 | 78.68 | **98.00** | 92.11 |
| Token-heavy | 25/40/10/20/5 | 73.84 | **99.00** | 94.04 |
| Reliability-heavy | 40/15/5/30/10 | 83.70 | **98.00** | 92.88 |

These fixed alternatives leave the ordering unchanged. Sensitivity across weights does not substitute for repeated runs or quantify statistical uncertainty.

## Excluded run and harness findings

The initial CLI turn was excluded before continuing: this shell runner reaped an automatically spawned browser bridge at command exit. Browser state disappeared between tool calls. It made no submissions. To avoid rewarding persistence for an environment artifact, every scored arm used a controller-owned PTY bridge and equally prestarted browser page. The excluded transcript and usage summary remain in the evidence directory.

The persistent helper is trusted-code benchmark infrastructure, not a sandbox or production runtime. Its namespace `reset` retains prior objects and differs from replacing the process; the pilot used actual process replacement. Timeout delivery cannot reliably preempt native blocking code and never implies browser rollback.

Independent review also found and corrected two analysis defects before final scoring: nested Pi tool-result text was initially counted as zero, and incomplete runs could distort efficiency normalization. Regression tests cover both. Neither changes the provider usage totals in this completed three-arm pilot.

## What this does not establish

- One sequence per mode, one model, one fixture and fixed order cannot establish a general superiority or success rate.
- The persistent agent did not exploit cross-cell values/helpers/baselines. The CLI agent did not realize the best available batch/filter strategy. This is an end-to-end availability pilot, not a controlled capability ablation.
- All eight records were printed into conversation. That makes the follow-up a weak test of interpreter memory and exposes why the next experiment must use enough data to make programmatic retention worthwhile.
- Public documentation differs: CLI loaded its broader skill, Python loaded its narrower API guide. Those real onboarding costs are included, but documentation and interface effects are not isolated.
- No claim covers cold installs, provider-cache independence, image tokens, live sites, blocked login, AXI, back-navigation, compaction, multi-agent isolation, or interruption during a side effect.
- Browser RPC counts and interpreter startup latency were not instrumented. Model calls and active stage time are not substitutes.

## Next decision-quality experiment

Keep the tested Python seam and benchmark worker separate. Before implementing production persistence:

1. Use a larger dataset and multiple unrevealed follow-up queries. Give every arm the same instruction to retain complete data programmatically and emit only requested results; allow CLI/fresh scripts disk storage and persistent Python memory.
2. Compare equally tuned fixed scripts to isolate transport/retention overhead, then repeat autonomous agent runs to measure discoverability and repair costs. Do not mix the two result sets.
3. Repeat with fixture variations and counterbalanced mode order. Report per-task distributions and uncertain-side-effect recovery separately, not one aggregate score.

## Reproduction and evidence

- Runner instructions: `benchmarks/README.md`.
- Exact task turns: `benchmarks/tasks.md`.
- Frozen weights/protocol and retrospective: `plans/done/code-mode-benchmark.md`.
- Evidence directory: `~/.agents/artifacts/outputs/browser-skills/2026-09-16/code-mode-benchmark/`.
- Evidence includes `environment.json`, `manifest.json`, `matrix.json`, per-mode sanitized usage/actions, independent oracle/page/cleanup observations, and `recovery-observations.json`. Original transcripts remain private at the paths recorded by the manifest; no encrypted reasoning was copied.
- Validation: `uv run pytest benchmarks -q` — **12 passed**; `uv run ruff check benchmarks` and `git diff --check` passed. Production code was unchanged.
