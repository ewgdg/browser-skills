# Issue 20: Pass the live ChatGPT compatibility gate

## Goal

Add and pass one explicit, serial, privacy-safe live acceptance flow against the current ChatGPT UI.

## Intention

Exercise the public `surf-chatgpt` workflow from short-lived processes while a dedicated Patchright profile owns the browser state. Keep prompts, responses, visible titles, DOM, non-public URLs, and raw browser failures in memory only; report content-free checkpoints and always attempt explicit teardown.

## Scope and constraints

- Implement GitHub issue #20 only; the deterministic implementation from issues #13 through #19 remains fail-closed.
- Treat the public CLI/JSON behavior and observable unfocused/non-interfering browser behavior as the confirmed acceptance seams.
- Require an explicit `live_chatgpt` opt-in and an operator-supplied authenticated Surf profile. Use a separate temporary clean profile for the logged-out case.
- Submit one generated nonce prompt through stdin. Never print it or any response/title payload.
- Run serially. Stop/restart only the dedicated gate bridge/profile and explicitly abandon both the disposable session and logged-out handoff page.
- Do not retain screenshots, DOM, prompt/response/title data, non-public URLs, or raw browser errors.
- Do not add compatibility, migration, or weakened fallback behavior.

## Work plan

1. Add a skipped-by-default live marker/option and a content-redacting command harness.
2. Add deterministic tests for opt-in, redaction, teardown, and the serial workflow before wiring real commands.
3. Implement the single live flow: retained submit, second-process observations, repeated result, recent discovery, restart recovery with an instrumented unrelated local page, and logged-out handoff.
4. Run the gate once with a dedicated authenticated profile and diagnose any current-UI drift without weakening classifiers. Do not repeat submission automatically.
5. Run full validation, independently review the final diff against the issue and repository standards, move this plan to `plans/done`, and create one semantic commit that closes #20.

## Validation

- Normal test runs collect but skip the live gate unless explicitly enabled.
- Deterministic harness tests prove private subprocess output and prompts cannot enter failures or reports, and cleanup runs on partial failure.
- The live run reports only content-free acceptance checkpoints.
- Full pytest, Ruff, changed-production type analysis, both Python builds, npm dry-run packaging, and `git diff --check` pass.

## Progress

- [x] Issue #20, accepted specification, current docs, profile controls, restart command, and existing test infrastructure inspected.
- [x] Live harness tests and implementation complete.
- [x] Opt-in live gate passed.
- [x] Deterministic validation complete.
- [x] Final review complete.
- [x] Semantic commit finalized.

## Decisions

- Issue #20 and the accepted specification confirm the public acceptance seams.
- The gate will be durable and rerunnable, not an unreviewable sequence of shell commands.
- A local heartbeat page will provide content-free evidence that restart recovery leaves an unrelated restored page loaded and alive.
- Explicit visible rate-limit UI is a first-class terminal state. Pre-send limits fail as `rate_limited`; post-send limits without an ID remain `submission_outcome_indeterminate` with an allow-listed `rate_limited` cause; no path retries submission.
- The single 2026-07-30 live run passed authenticated preflight, retained submission, exact-page reuse, status/result observation, repeated result, and recent discovery. Immediate post-restart status encountered fail-closed `inspection_failed` during page hydration, so the run stopped before restart recovery and the logged-out tail check.
- Restart recovery now retries only the read-only `session status` observation while the restored page hydrates. `rate_limited` and every other operational failure remain non-retryable. After the first failed run, no second live submission was launched until the user explicitly authorized it.
- The user authorized one final authenticated submission. That run passed submission, exact-page reuse, status/result observation, repeated result, recent discovery, restart recovery, unrelated-page preservation, and teardown.
- Logged-out focus validation uses the focused Niri window ID rather than `document.hasFocus()`, because document focus does not prove desktop activation. The no-send logged-out tail passed separately: compositor focus was unchanged, zero user turns were rendered, the handoff was abandoned, and the bridge stopped.
