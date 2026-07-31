# Surf ChatGPT selection inspection

## Goal

Add an explicit no-send diagnostic that affirms ChatGPT model and thinking picker
selection through the same pre-send browser path used by `ask`.

## Intention

Expose `surf-chatgpt selection inspect` as a narrow diagnostic interface instead of
adding a mode flag that negates `ask` submission. Keep picker mechanics inside the
existing browser lifecycle module and document the diagnostic separately from the
always-needed skill workflow.

## Scope and constraints

- Require at least one of `--model` or `--thinking`.
- Never inject a prompt, click send, or create a ChatGPT conversation.
- Use a dedicated temporary browser thread by default and close it only after terminal
  JSON is flushed.
- `--retain` keeps the diagnostic page open and returns its thread for manual
  inspection or explicit abandonment.
- `--thread` retries only an exact preserved selection-inspection page after a human
  gate.
- Reuse the production readiness and picker-selection program.
- Do not add the diagnostic command to `skills/surf-chatgpt/SKILL.md`.
- Put durable diagnostic documentation under `docs/`.

## Work plan

1. Add a failing public CLI test for typed `selection inspect` dispatch and JSON.
2. Add failing browser lifecycle tests for no-send selection, post-output cleanup,
   retained inspection, exact-thread retry, and human gates; implement one vertical
   slice at a time.
3. Add the diagnostic to the opt-in live compatibility gate using explicitly supplied
   live picker queries so volatile account choices are not hard-coded.
4. Add standalone diagnostic documentation and remove the obsolete no-command claim
   from the normal skill.
5. Run focused and full validation, then review the final diff against the interface
   contract and repository standards.

## Validation

- Focused CLI, browser lifecycle, DOM, and live-gate harness tests pass.
- Full repository tests, Ruff, type analysis, builds, and diff integrity pass.
- The opt-in real ChatGPT gate remains opt-in and is not run without authorization.

## Progress

- [x] Public and browser lifecycle seams agreed.
- [x] CLI contract implemented test-first.
- [x] Browser diagnostic lifecycle implemented test-first.
- [x] Opt-in live gate coverage implemented.
- [x] Standalone documentation completed.
- [x] Full validation and review completed.

## Decisions

- The public name is `selection inspect`; `ask --test` is rejected because it obscures
  the invariant that `ask` submits exactly once.
- The lifecycle gets one typed selection-inspection request rather than a second
  picker implementation.

## Outcomes and retrospective

- `selection inspect` now affirms requested model and thinking labels without filling
  or sending a prompt.
- Temporary pages close after JSON flush; retained and human-gated pages return an
  exact diagnostic thread.
- The opt-in live gate covers both picker dimensions, focus preservation, and an
  unrelated-page canary. It was not run because real ChatGPT use requires explicit
  authorization.
- Tests were deliberately reduced to the public CLI contract, no-send cleanup, and
  exact retained-thread safety rather than duplicating every error mapping.
- Focused tests, the full non-live suite, Ruff, production type analysis, CLI help,
  and diff integrity passed.
