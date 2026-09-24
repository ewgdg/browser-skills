# Agent recovery primitives: error codes, conditional wait, scoped text

## Goal

Give agents writing Surf Python three primitives borrowed from opencli-mcp's design:

1. Stable error codes on `SurfAgentError` (`error.code`), with actionability failures classified by probing the element.
2. `Thread.wait()` conditions with a caller-chosen timeout: text appears, text is gone, URL matches.
3. `Thread.text(target)`: visible text of one region chosen by snapshot ref or CSS selector.

## Intention

Agents recover better when they can branch on a stable code and when a failure names what the page actually showed. They read less noise when they can scope text to the region they care about. Comparison and decision matrix: conversation of 2026-09-24 (opencli-mcp v0.0.21, commit `6a26476`).

## Scope & Constraints

- Patchright only. AXI raises `SurfAgentError` with code `unsupported` for new capabilities (`text(target)`, `wait(gone=|url=|timeout_ms=)`). Existing AXI behavior is unchanged.
- `wait(ms)` stays a sleep; `wait("text")` keeps its meaning. No `expect()` verb.
- Codes are a closed `StrEnum`; branch on `code`, never message text.
- Actionability classification probes element state after Playwright's action times out. Messages are not parsed.
- Action timeout must be shorter than the client transport timeout so failures return classified instead of as transport timeouts.
- `runtime-revision` pin is written in the commit after the runtime change (AGENTS.md).

## Design

- `errors.py`: `ErrorCode` StrEnum; `SurfAgentError(message, exit_code=1, code=None)`.
- Bridge: `BridgeCodedError(code, message)`; handler returns `{"error", "code"}`; closed-target failures map to `page_closed`.
- Client: `BridgeToolError.code` from payload; transport timeout → `outcome_unknown`; connection failure → `bridge_unavailable`.
- Action failure probe order: element missing (`stale_ref` for refs, `not_found` for selectors) → `not_visible` → `not_enabled` → `not_editable` (fill) → hit-test blocker `intercepted` (message names blocker) → `action_timeout`.
- `wait-for` bridge command polls conditions (AND) until `timeout_ms`; failure `wait_timeout` lists unmet conditions plus current URL and title. Client transport timeout for this call extends by `timeout_ms`.
- URL condition: `fnmatch` glob over the full URL.

## Work Plan

1. Red tests: live Patchright codes/wait/text; bridge JSON roundtrip; Thread argument validation; AXI unsupported.
2. Error codes through bridge, client, Thread.
3. Conditional wait.
4. Scoped text.
5. Docs: shipped `python-api.md`, `SKILL.md` touchpoints.
6. Commit runtime; commit pin.

## Validation

- `uv run pytest packages/surf-agent/tests -q`
- `SURF_TEST_LIVE_PATCHRIGHT=1 uv run pytest packages/surf-agent/tests/test_patchright_agent_primitives.py -q`
- `uv run ruff check packages tests benchmarks`

## Progress

- [x] Red tests (unit transport/Thread contract; live Patchright codes, wait, scoped text)
- [x] Error codes
- [x] Conditional wait
- [x] Scoped text
- [x] Docs
- [x] Commits and pin (local; not pushed)
- [x] Installed acceptance against a built wheel and packed skill, then against the published pin
- [x] Follow-ups: long `wait(ms)` outlasts transport timeout; `Browser().import_cookies_for(domain)` with login-wall consent flow

## Surprises & Discoveries

- Playwright's default 30s action timeout exceeded the client's 15s transport timeout, so a blocked click previously surfaced as a transport timeout. `ACTION_TIMEOUT_MS = 5000` fixes that and is what makes classification observable.
- `wait-for` polling across a navigation needed no special handling in Patchright 1.60.1 (`filter(visible=True).count()` survived the context swap in the live test).
- The Patchright bridge is single-threaded: a long `wait(timeout_ms=...)` blocks other threads' calls for its duration. Known limitation; unchanged from the old fixed 10s text wait except that callers can now choose longer.

## Decisions

- `text(target)` chosen over main-content extraction, paged read, or dropping (weighted matrix 4.70 vs 3.65 / 3.10 / 3.60).
- Extend `wait` rather than add `expect`: waiting and expecting are the same poll-or-raise operation.

## Outcomes & Retrospective

- Shipped error codes, conditional `wait`, scoped `text(target)`, the long-sleep transport fix, and consent-driven single-site cookie import (restart-based option A; live injection rejected because it needs Chrome cookie decryption).
- Installed acceptance exposed a stale exact-stdout assertion from the session-metadata change; fixed in the test.
- macOS remains unverified; tracked in a GitHub issue.
