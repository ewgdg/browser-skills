# Issue 19: Discover recent sessions and document resumable workflows

## Goal

Implement read-only `surf-chatgpt session recent` discovery against ChatGPT's rendered Chat history and document the complete resumable workflow using the exact public JSON contract.

## Intention

Treat discovery as a narrow, title-bearing owned-page operation. It reads only one affirmatively identified Chats section, returns bounded canonical candidates without selecting or recovering them, and keeps temporary-page allocation, human-gate preservation, and post-output closure inside existing owned-page lifecycle rules.

## Scope and constraints

- Implement GitHub issue #19 only; issue #18 is already complete on `main`.
- Treat the public CLI/JSON, typed owned-page bridge, semantic DOM fixtures, and Patchright runtime operations as the confirmed TDD seams established by issue #19 and the accepted resumable-session specification.
- Return at most ten unique canonical `/c/<id>` conversations in displayed Chats order. Titles may cross the browser boundary only for this explicit operation.
- Fail closed with `ui_changed` and no candidates when Chat history or Chats is missing or ambiguous. An affirmatively empty Chats section succeeds.
- Exclude Pinned, Projects, archived, duplicate, malformed, and out-of-section links. Do not infer timestamps.
- A no-thread request allocates a temporary unfocused owned page and closes it only after output is flushed. A human gate preserves that exact page; `--thread` may reuse only that protected discovery page.
- Never adopt, select, bind, navigate to, resolve, or recover a candidate conversation.
- Keep Patchright as Surf's default, reject AXI for surf-chatgpt before browser work, and document that Camoufox is unsupported without adding any Camoufox implementation or configuration.
- Do not add compatibility or migration behavior.

## Work plan

1. Add semantic DOM behavior tests and implement the bounded Chats-section discovery program.
2. Add typed owned-page discovery and guarded discovery-page closure contracts with strict title/ID allow-list decoding.
3. Add Patchright runtime tests and implement serialized discover/close behavior without navigation, binding, adoption, candidate recovery, or focus.
4. Add lifecycle tests and implement temporary allocation, exact gated-thread reuse, public success/error projection, and output-before-best-effort-close.
5. Rewrite the surf-chatgpt skill guidance around submit-first, optional wait, later observation, follow-ups, recovery, handoff, proactive login, user-only focus, and explicit abandonment with exact JSON examples.
6. Run focused validation throughout, then full tests, Ruff, changed-production type analysis, both package builds, npm packaging dry-run, and diff integrity.
7. Review the final diff against issue #19 and repository standards, resolve findings, revalidate, move this plan to `plans/done`, and create one semantic commit.

## Validation

- Each vertical slice demonstrates red before green at a public, semantic DOM, typed bridge, or Patchright runtime seam.
- DOM fixtures cover displayed order, duplicates, more than ten rows, Pinned, Projects, archived and out-of-section links, affirmed empty Chats, missing Chats, ambiguous Chat history/Chats, malformed links, hidden candidates, and privacy canaries.
- Bridge tests prove exact page guards, bounded strict decoding, non-interference, human-gate protection, and guarded close behavior.
- CLI/lifecycle tests prove temporary allocation, exact thread reuse, no candidate recovery, `ui_changed`, capacity projection, human handoff, and JSON flush before best-effort closure.
- The opt-in live ChatGPT gate is not run without explicit authorization because it would expose private visible conversation titles to the command output.

## Progress

- [x] Issue #19, dependency, accepted specification, prior implementation, current docs, and public seams inspected.
- [x] Chats DOM discovery complete.
- [x] Typed bridge discovery contract complete.
- [x] Patchright discovery runtime complete.
- [x] Public lifecycle complete.
- [x] Documentation and full validation complete.
- [x] Final review and semantic commit complete.

## Decisions

- The issue and accepted specification are the user's confirmation of the TDD seams; tests will not target private helper structure.
- Discovery gets a dedicated title-bearing bridge result instead of weakening metadata-only status/cleanup inspection.
- Successful temporary discovery captures candidates first and returns closure as private post-output cleanup, preserving the global flush-before-cleanup contract.

## Surprises and discoveries

- The rendered Chats label is not guaranteed to be an HTML heading. The DOM program accepts either a semantic section or one visible exact Chats label followed by an identifiable rendered list, while still failing closed on multiple histories, multiple labels, or an unscoped link sequence.
- Reusing a human-gated discovery page must preserve its `human_intervention` protection through inspection and discovery. The explicit `session recent --thread` retry supplies that exact expected protection to guarded post-output closure instead of weakening or silently clearing it.

## Outcomes and retrospective

- `session recent` now returns only the first ten unique canonical conversations from one affirmed rendered Chats section, exposes visible titles only through its dedicated typed path, and fails closed without candidates for ambiguous or changed UI.
- Temporary discovery uses existing non-activating owned-page allocation and guarded post-output closure. Login and challenge gates preserve the exact discovery thread under human protection; only an explicit retry of that exact protected thread can continue.
- Discovery has no candidate-resolution operation: it never calls session resolution, rebinding, navigation, selection, recovery, or focus. Strict bridge decoders reject content-bearing, unbounded, duplicate, malformed, or different-page results.
- The surf-chatgpt skill now documents the full resumable workflow and exact JSON examples. Repository docs identify Patchright as default and the only surf-chatgpt backend, AXI as generic-only, and Camoufox as unsupported.
- Final validation passed 580 tests and 28 subtests, Ruff, changed-production type analysis, both Python package builds, npm packaging dry-run, and diff integrity. The opt-in live ChatGPT gate was not run because it would expose private visible conversation titles without explicit authorization.
