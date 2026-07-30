# Issue 17: Observe or wait for the latest response attempt

## Goal

Let callers classify or retrieve the latest ChatGPT response attempt without owning generation. `session status`, one-shot `session result`, waiting result, and `ask --wait` share one affirmative classifier and preserve terminal output before guarded cleanup.

## Intention

Keep response observation inside one session-lifecycle path. ChatGPT DOM programs return narrow typed metadata; only explicit result observation may cross the response-text boundary. Waiting repeatedly performs the same read-only observation until terminal evidence or its observer deadline, while terminal cleanup remains a private post-output action.

## Scope and constraints

- Implement GitHub issue #17 only. Abandonment, capacity cleanup, and recent-session discovery remain later tickets.
- Treat the public CLI/JSON contract and semantic DOM programs as the pre-agreed TDD seams established by issue #17 and the accepted resumable-session specification.
- Expose only affirmatively evidenced `generating`, `completed`, `stopped`, or `failed` attempt states. Unclassifiable, contradictory, stale, gated, replaced, or out-of-scope pages fail closed without an attempt state.
- Observation is read-only, repeatable, and non-consuming. It never sends, stops, edits, reloads, scrolls, focuses, or activates.
- Response text may leave the browser only for explicit `session result`; status, cleanup, and bridge validation reject content-bearing or unexpected fields.
- Emit and flush terminal JSON before best-effort guarded closure. `--retain` suppresses closure.
- Do not add compatibility or migration behavior.

## Work plan

1. Add semantic latest-attempt DOM fixtures and implement one affirmative metadata classifier plus explicit result extraction.
2. Extend the typed owned-page bridge with allow-listed read-only inspection and guarded terminal closure at the existing ownership/page-identity seam.
3. Add public lifecycle/CLI tests for status, one-shot result, waiting result, timeout, `ask --wait`, privacy enforcement, and output-before-cleanup.
4. Implement one observation orchestration path and route post-handshake `ask --wait` through it.
5. Update the surf-chatgpt skill documentation for the newly implemented observation commands.
6. Run focused validation throughout, then full repository tests, lint, type analysis, package builds, packaging dry-run, and diff integrity.
7. Review the final diff against repository standards and issue #17, resolve findings, revalidate, and create one semantic commit.

## Validation

- Focused semantic DOM tests pass after each classifier/extractor slice.
- Focused owned-page bridge and session lifecycle tests pass after each protocol/orchestration slice.
- Ruff and changed-production type analysis pass regularly.
- Full repository validation and diff integrity pass once at the end.
- Final self-review reports no unresolved standards, privacy, or issue-scope findings.

## Progress

- [x] Issue #17, accepted specification, preceding implementation, and public seams inspected.
- [x] Latest-attempt DOM classification and result extraction complete.
- [x] Typed bridge inspection and guarded terminal closure complete.
- [x] Lifecycle status/result/wait and `ask --wait` complete.
- [x] Documentation and full validation complete.
- [x] Final review and semantic commit complete.

## Decisions

- The issue and accepted specification are the user's confirmation of the public test seams; tests will not target private helper structure.
- Observation will reuse deterministic session resolution from issue #16 and will not introduce a persistent attempt registry.

## Surprises and discoveries

- A freshly recovered ChatGPT page initially fails closed while its conversation DOM hydrates. Reusing the retained exact page after hydration affirmed `completed` in the live browser without focus or content mutation.
- Active generation can be affirmed before ChatGPT creates an assistant turn: the newest user turn plus one current global stop control is sufficient affirmative evidence, while stale stop controls inside older turns remain excluded.
- Malformed content-bearing bridge metadata must project to known-session `inspection_failed`; allowing decoder `ValueError` to escape would lose durable session identity behind `internal_error`.

## Outcomes and retrospective

- Status, one-shot result, waiting result, and post-handshake `ask --wait` now share one latest-attempt classifier and one observation lifecycle.
- Patchright enforces separate metadata-only classification, explicit result extraction, and serialized reclassify/revalidate/close operations. Exact page token, URL, owner, scope, and protection guards preserve replaced or protected pages.
- Terminal output is serialized and flushed before best-effort cleanup; close failure cannot retract or replace the public JSON, and `--retain` establishes protection before observation.
- Semantic fixtures cover affirmative states, early generation, stale and contradictory markers, old turns, structured and empty results, refusals, gates, loading, ambiguity, and privacy canaries.
- Live unfocused validation affirmed a completed current ChatGPT response after hydration. Final validation passed 511 tests and 28 subtests, Ruff, changed-production type analysis, both Python package builds, npm dry-run packaging, and diff integrity. Repository-wide type analysis retains five existing diagnostics in unchanged surf-agent CLI code.
