# Issue 18: Abandon pages and enforce retained-page capacity

## Goal

Give `surf-chatgpt abandon` the only automatic authority to stop active generation and release retained pages, while every new owned-page allocation performs one privacy-safe opportunistic sweep and enforces a live-bridge limit of ten surf-chatgpt pages.

## Intention

Keep stop/confirm/close and sweep/classify/close inside serialized owned-page bridge transactions. Public commands provide explicit authority and bounded recovery identity; automatic cleanup receives only metadata, never conversation content, and preserves any page whose ownership, scope, protection, classification, stop, or closure cannot be affirmed.

## Scope and constraints

- Implement GitHub issue #18 only. Recent-session discovery remains issue #19.
- Treat public CLI/JSON, the typed owned-page bridge, semantic DOM fixtures, and Patchright runtime operations as the confirmed TDD seams named by the issue and accepted specification.
- Abandon accepts exactly one durable session or explicit pre-session thread. Active generation receives one stop request, must affirm `stopped`, and then closes. Terminal attempts and affirmed non-generating pre-session/human-gate pages close directly.
- Age, inactivity, observation timeouts, caller exit, and process death never abandon a page.
- Every allocation runs one single-pass owner-scoped sweep. A sweep skips bridge-protected pages without DOM inspection; inspects each other page at most once with metadata-only classification; and never navigates, recovers, adopts, reloads, scrolls, clicks, types, stops, waits, polls, focuses, or emits UI events.
- Capacity is ten live pages owned by `surf-chatgpt`. Failure reports one bounded entry per remaining page with only a durable session or necessary pre-session thread and one of `generating`, `human_intervention`, `inspection_failed`, or `explicitly_retained`.
- Do not add compatibility or migration behavior.

## Work plan

1. Add semantic DOM tests and one metadata-only cleanup classifier plus explicit stop action.
2. Add typed abandon/capacity bridge contracts and strict privacy-safe wire decoding.
3. Add Patchright runtime tests and implement guarded abandonment plus single-pass pre-allocation sweeping/capacity enforcement.
4. Add public CLI lifecycle tests and implement session/thread abandonment and capacity error projection.
5. Update the project glossary and surf-chatgpt skill documentation.
6. Run focused validation throughout, then full tests, lint, changed-production type analysis, package builds, packaging dry-run, and diff integrity.
7. Review the final diff against repository standards and issue #18, resolve findings, revalidate, and create one semantic commit.

## Validation

- Each vertical slice demonstrates red before green at a public or semantic seam.
- Scripted bridge tests cover active, terminal, human-gated, unclassifiable, ownership/scope/stop/close failure, ambiguous addressing, and non-abandonment triggers.
- Patchright tests prove one stop request, stop affirmation before closure, one inspection per eligible page, protected-page inspection skips, no forbidden page actions, and eleventh-allocation success/failure behavior.
- Privacy scans reject content-bearing or unexpected bridge metadata and public capacity fields.
- Full repository validation and diff integrity pass once at the end.

## Progress

- [x] Issue #18, accepted specification, privacy policy, preceding implementation, and public seams inspected.
- [x] Cleanup DOM classifier and stop action complete.
- [x] Typed abandon/capacity bridge contract complete.
- [x] Patchright abandonment and sweep/capacity behavior complete.
- [x] Public lifecycle and CLI behavior complete.
- [x] Documentation and full validation complete.
- [x] Final review and semantic commit complete.

## Decisions

- The issue explicitly requires scripted and Patchright bridge coverage; together with the accepted specification, that confirms the public CLI/JSON, typed bridge, semantic DOM, and Patchright runtime test seams.
- Allocation policy is passed through the typed request as a limit and metadata-only program so surf-agent remains owner-agnostic and no generic browser operation learns ChatGPT DOM details.
- Capacity diagnostics are bridge-produced typed metadata but surf-chatgpt-owned public JSON; no raw bridge values or full URLs cross the public boundary.

## Surprises and discoveries

- A visible composer is not pre-session evidence on a durable conversation page; ChatGPT may keep it visible during generation. Cleanup classifies the latest attempt first on `/c/<id>` pages so active generation cannot bypass stop-first abandonment.
- Revalidation must occur before the stop action, immediately after it, after every stop-confirmation classification, and before closure. Tests proved that page replacement or scope change at either browser-program boundary otherwise risks acting on stale authority.
- A temporary submission thread may already have a durable `/c/<id>` URL before deterministic rebinding. Capacity output therefore exposes the durable session only when its deterministic thread owns the page; otherwise it returns the necessary temporary thread.
- Retained-page policy made the existing Patchright owned-page implementation exceed a practical navigation size. The final design keeps the external bridge interface unchanged and moves sweep/abandon complexity into one deep internal retained-page module.

## Outcomes and retrospective

- `abandon` now closes terminal and affirmed human-gated pages directly, stops active generation exactly once before affirming `stopped`, and preserves pages on every unproven guard or mutation result.
- Every allocation performs one owner-scoped metadata-only sweep, skips protected-page DOM, closes only affirmed terminal pages, and enforces the ten-page live-bridge limit with bounded privacy-safe diagnostics.
- Scripted public tests and Patchright runtime tests cover both eleventh-allocation outcomes, the four capacity reasons, temporary-thread recovery identity, privacy rejection, stop/close failures, inaccessible/changed pages, and stale-authority races.
- Final validation passed 544 tests and 28 subtests, Ruff, changed-production type analysis, both Python package builds, npm packaging dry-run, and diff integrity.
