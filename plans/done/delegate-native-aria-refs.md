# Delegate native ARIA refs

## Goal

Use Playwright/Patchright AI snapshot refs directly for browser actions. Remove custom `pr`/`cf` ref generation, rebinding, duplicate snapshot output, and sanitization.

## Intention

Snapshot refs already identify exact elements through the driver-provided `aria-ref` selector. Browser adapters should delegate that behavior instead of maintaining a second reference system.

## Scope & Constraints

- Patchright and Camoufox adapters.
- Preserve AI-mode snapshots, selectors, action commands, stale-ref error text, depth, and box options.
- Accept main-frame refs (`eN`) and iframe refs (`fNeN`), with optional `@` prefix.
- No compatibility layer for removed `pr`/`cf` refs unless explicitly requested.
- Test seam confirmed by requested design: public snapshot/action behavior through each runtime adapter.

## Work Plan

1. Patchright red/green slice: snapshot exposes native refs; fill/click delegate through `aria-ref`; stale refs fail clearly.
2. Camoufox red/green slice with same behavior.
3. Remove obsolete custom ref state, indexing, fingerprints, CSS-path helpers, sanitizers, limits, and tests.
4. Run focused tests, full suite, lint, and real Patchright smoke test including iframe ref.

## Validation

- `uv run pytest packages/surf-agent/tests/test_cli.py -q`
- `uv run pytest -q`
- `uv run ruff check .`
- Live Patchright AI snapshot: click `eN` and `fNeN`; removed refs become stale.

## Progress

- [x] Native `aria-ref` behavior verified against current Patchright driver, including iframe refs and stale elements.
- [x] Patchright slice.
- [x] Camoufox slice.
- [x] Dead-code removal.
- [x] Full validation.

## Decisions

- Native refs are snapshot-scoped exact element identities. Do not fingerprint-rebind them after DOM replacement.
- Keep AI mode because its refs are now actionable rather than sanitized.

## Outcomes & Retrospective

- Both adapters return native AI snapshots unchanged and resolve `eN`/`fNeN` refs through `aria-ref`.
- Removed custom ref maps, actionable indexing, fingerprint rebinding, CSS paths, sanitizer regexes, duplicate snapshot nodes, and indexing limits.
- Full suite: 196 tests and 29 subtests passed. Ruff and diff checks passed.
- Live Patchright smoke test clicked main-frame and iframe refs; removed element ref produced the stale-snapshot error.
