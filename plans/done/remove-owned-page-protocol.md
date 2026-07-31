# Remove the owned-page protocol

## Goal

Delete the `owned-*` bridge protocol and place all ChatGPT behavior in
`surf-chatgpt`, using only thread-addressed generic Patchright operations.

## Intention

Keep the persistent bridge responsible only for live browser pages and generic
browser primitives. Give `surf-chatgpt` one deep browser module that owns its DOM
programs, submission lifecycle, session recovery, observation, cleanup, and public
errors. A thread is the page address.

## Scope and constraints

- Remove all `owned-*` commands, types, adapters, capabilities, guards, capacity,
  protection, and retained-page policy rather than renaming them.
- Preserve the useful public `surf-chatgpt` commands and compact JSON behavior.
- Keep at-most-once caller behavior: once a Send request may have crossed the
  transport, never retry it automatically.
- Add only one generic bridge primitive that is actually missing: rename a live
  thread without replacing its page.
- Do not retain compatibility or migration logic.
- Use only the user-confirmed test seams: public `surf-chatgpt`, its browser port,
  generic thread rename, and existing semantic DOM fixtures.
- Keep new tests minimal; delete owned-protocol tests instead of translating them.
- Remove the uncommitted filesystem burst gate. Revisit burst protection only
  after this cleanup has a stable seam.

## Work plan

1. Remove the uncommitted burst-gate patch.
2. Add one failing generic thread-rename test and implement the primitive.
3. Add one failing `surf-chatgpt` browser-port lifecycle tracer and implement the
   generic bridge adapter.
4. Move lifecycle operations from `ChatGptOwnedPages` to the new browser module in
   vertical slices while keeping public tests green.
5. Delete the owned protocol, retained-page implementation, and their internal
   tests; simplify the bridge/runtime types.
6. Update the glossary and skill documentation to describe the resulting design.
7. Run focused validation, then full tests, Ruff, type analysis, builds, packaging,
   and diff review.

## Validation

- New tests: one thread-rename behavior and one browser-port lifecycle tracer.
- Existing public `surf-chatgpt` and semantic DOM tests provide regression coverage.
- Full repository tests, Ruff, changed-production type analysis, both Python builds,
  npm packaging dry-run, and diff integrity pass.

## Progress

- [x] Design and test seams confirmed by the user.
- [x] Uncommitted burst patch removed.
- [x] Generic thread interface complete.
- [x] ChatGPT browser module complete.
- [x] Owned protocol deleted.
- [x] Documentation and validation complete.

## Decisions

- Thread identity is sufficient for page addressing in the simplified design.
- Removed guarantees are not recreated through replacement guard fields.
- Test count is intentionally reduced with the removed protocol surface.

## Surprises and discoveries

- Generic `fill` and `click` already accepted selectors, so `rename-thread` was
  the only missing bridge primitive.
- The owned protocol had accumulated more than twelve thousand lines across
  implementation, adapters, and coupled tests. Removing the seam eliminated the
  entire retained-page policy rather than relocating it.

## Outcomes and retrospective

`surf-agent` now stores only generic thread/page bindings and dispatches generic
browser primitives. `surf-chatgpt` owns the ChatGPT DOM programs and lifecycle
through a small browser port. The public capacity and ownership errors are gone,
as are automatic sweeping and live protection metadata.

The remaining test suite is intentionally smaller: one in-memory lifecycle tracer,
one generic rename test, the public CLI/JSON tests, and semantic DOM fixtures. Full
validation passed with 381 tests, 1 skipped live test, 28 subtests, Ruff, changed-code
type analysis, both Python builds, npm packaging dry-run, and diff integrity checks.
