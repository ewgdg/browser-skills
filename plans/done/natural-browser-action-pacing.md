# Natural browser-action pacing

## Goal

Reduce unrealistically bursty browser interaction sequences without slowing observation, polling, retries, cleanup, or standalone commands.

## User-visible contract

```bash
surf-chatgpt ask 'Question...'                  # natural pacing by default
surf-chatgpt ask --pace none 'Question...'      # deterministic opt-out
surf-agent do --pace natural open URL :: fill @e1 text :: click @e2
```

- `surf-chatgpt ask` accepts `--pace natural|none` and defaults to `natural`.
- `surf-agent do` accepts the same named profiles and defaults to `none`.
- Standalone Surf Agent commands are unchanged.
- Natural pacing uses one centrally defined uniformly sampled 0.4–1.0 second delay.
- Output schemas and prompt content are unchanged; `took_ms` may include pacing.

## Scope and constraints

- Pace semantic user-visible actions only.
- Surf Agent mutations: `open`, `new`, `back`, `fill`, `type`, `click`, `press`, and `scroll`.
- Before an eligible non-initial `do` step, pause only if the prior step succeeded and neither adjacent step is `wait`.
- Never pace observations, generic `eval`, retries, browser lifecycle, handoff, URL polling, or cleanup.
- ChatGPT pauses occur after successful model/thinking selection when requested, and after prompt injection before submit.
- Random sampling and sleeping must be injectable so tests never sleep.
- Describe pacing as reducing action bursts, not bypassing automation detection.

## Work plan

### Checkpoint 1 — Shared pacing primitive

Write `packages/surf-agent/tests/test_pacing.py` first. Implement a focused `surf_agent.pacing` module with named profiles, injected randomness/sleep, and a no-op `none` profile.

### Checkpoint 2 — Surf Agent composed commands

Write CLI sequencing/parser tests first. Extend `DoOptions`, parse invocation-level `--pace`, and invoke the shared pacer only at approved boundaries in `run_do`. Preserve fail-fast and output behavior.

### Checkpoint 3 — ChatGPT ask workflow

Write CLI, client, and browser workflow tests first. Propagate the profile through `AskOptions` and `ReusableAskOptions`, then insert only the two semantic pauses in the high-level ask orchestration.

### Checkpoint 4 — Documentation and validation

Update runtime help plus `skills/surf/SKILL.md` and `skills/surf-chatgpt/SKILL.md`. Run focused tests, full pytest, Ruff, and help checks. Move this plan to `plans/done/` only after validation succeeds.

## Validation

```bash
uv run pytest packages/surf-agent/tests/test_pacing.py -q
uv run pytest packages/surf-agent/tests/test_cli.py -q
uv run pytest packages/surf-chatgpt/tests/test_cli.py packages/surf-chatgpt/tests/test_client.py -q
uv run pytest packages/surf-chatgpt/tests/test_browser_chatgpt.py -q
uv run pytest
uv run ruff check packages
uv run surf-agent --help
uv run surf-chatgpt ask --help
```

## Progress

- [x] Shared pacing tests and implementation
- [x] Surf Agent `do` tests and implementation
- [x] ChatGPT ask tests and implementation
- [x] Documentation and full validation

## Decisions

- Use named profiles rather than exposing raw timing bounds.
- Keep pacing above browser backends so internal machine operations remain deterministic.
- Suppress automatic pacing around explicit waits to avoid double delays.
- Validate eligible mutation arguments before sleeping so malformed steps still fail immediately.

## Outcomes

- Shared named pacing profiles are deterministic under injected random/sleep callables.
- ChatGPT asks now use natural pacing by default with an explicit opt-out.
- Composed Surf Agent workflows can opt in without changing standalone commands or output schemas.
- Full validation passed: 312 tests and 31 subtests, plus Ruff and whitespace checks.
