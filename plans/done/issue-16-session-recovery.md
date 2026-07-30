# Issue 16: Recover sessions, hand off, and submit follow-ups

## Goal

Make a durable ChatGPT session actionable across short-lived callers and live browser-bridge restarts. Known sessions resolve through their deterministic thread, handoff establishes live retained protection, and `ask --session` submits exactly one follow-up through the established submission safety path.

## Intention

The Patchright bridge owns one serialized exact-URL resolution transaction. It reuses only an exact deterministic binding, adopts exactly one unowned restored exact match, creates one dedicated unfocused page when none exists, and fails closed on ambiguity or binding conflict. The surf-chatgpt lifecycle consumes the resolved page and private live-protection state without adding a persistent session or run registry.

## Scope and constraints

- Implement GitHub issue #16 only. Latest-attempt observation, cleanup/capacity, abandonment, and recent discovery remain in later tickets.
- Treat the real CLI JSON/exit contract, typed owned-page protocol, and semantic submission DOM programs as the pre-agreed TDD seams established by the accepted specification.
- Never inspect restored-page DOM or titles during recovery. Never adopt, navigate, focus, or close unrelated or conflicting pages.
- Preserve deterministic thread and canonical URL identity; browser page tokens remain live-bridge-only and may change after restart.
- Handoff returns only durable session identity and the `inspect_browser` action with deterministic thread.
- Follow-ups reuse readiness, picker selection, one-send, assignment, and phase-aware interruption/error behavior.
- Do not add persistent session/run state or backward-compatibility logic.

## Work plan

1. Add a typed owned-page resolution request/result and Patchright client transport, including an allow-listed ambiguity error.
2. Add focused Patchright runtime tests and implement exact live reuse, single restored-page adoption, no-match unfocused creation, conflict failure, and ambiguous-match failure without unrelated-page mutation.
3. Add real CLI lifecycle tests for handoff and `ask --session`, then generalize the existing submission orchestration to resolved session pages without creating a second send path.
4. Add phase-safety cases for known-session gates, bridge loss, and interruption while proving one send and durable recovery identity.
5. Update project/skill documentation only where issue #16 makes a user-facing technique newly usable.
6. Run focused tests and type analysis throughout, then full repository validation once.
7. Commit the implementation, run independent Standards and Spec reviews against the starting commit, address findings, revalidate, and amend the semantic commit.

## Validation

- Focused surf-agent owned-page and Patchright window tests pass after each bridge slice.
- Focused surf-chatgpt submission lifecycle and CLI tests pass after each lifecycle slice.
- Ruff and changed-production type analysis pass regularly.
- Full repository tests, package builds, npm packaging dry-run, and diff integrity pass once at the end.
- Independent Standards and Spec reviews report no unresolved findings.

## Progress

- [x] Issue #16, accepted specification, preceding implementation, and public seams inspected.
- [x] Typed session resolution protocol complete.
- [x] Patchright restart recovery and non-interference complete.
- [x] Handoff and follow-up lifecycle complete.
- [x] Phase-safety validation complete.
- [x] Documentation and full validation complete.
- [x] First independent review complete; all findings resolved and revalidated.
- [x] Final independent review and semantic commit complete.

## Decisions

- Resolution returns current protection only inside the typed bridge result. This is required for subsequent compare-and-set protection and submission guards across short-lived callers; public JSON continues to forbid it.
- Recovery is one serialized bridge operation so page inventory, exact-match cardinality, adoption, and creation cannot race through separate generic commands.
- Follow-up submission will share the existing orchestration instead of introducing a second lifecycle implementation.
- Atomic rebind completes the initial one-send barrier. For a follow-up, only an affirmed same-session assignment completes that attempt's barrier. Browser loss, gates, invalid metadata, and unconfirmed assignment leave it locked against replay.
- Submission orchestration lives in a focused lifecycle module. Session observation, handoff, login, and pending later-ticket commands stay separate from the one-send state machine.

## Surprises and discoveries

- Issue #15's irreversible send marker intentionally stayed set after initial success. That safely blocked replay but also blocked every future follow-up on the same page. The completion boundaries now distinguish an affirmed finished handshake from an unresolved attempt without adding a run registry.
- Review exposed that dispatch completion is not assignment affirmation. Follow-ups remain `id_known_rebind_pending` after dispatch, and the bridge clears the send barrier only when the serialized assignment transaction affirms the requested canonical URL and matching session ID.

## Outcomes and retrospective

- Deterministic session resolution now reuses only an exact live binding, adopts one unowned restored exact match, creates one dedicated background page when absent, and fails closed on conflict or ambiguity.
- Handoff resolves the durable session and establishes live explicit retention before returning its compact manual-inspection payload.
- Follow-ups share the existing readiness, selection, send, assignment, and interruption path. Real signal, kill, and disconnect tests prove one-send behavior with known durable recovery identity.
- The first independent Standards review found a monolithic submission method, broad exception handling, duplicated follow-up switching, an undocumented barrier transition, and a hidden mutating assignment responsibility. The lifecycle extraction, typed error projection, submission context, safety comment, and explicit completion URL resolved them.
- The first independent Spec review found that follow-up replay protection ended before exact-session affirmation and that `handshake_complete` was reported too early. Exact-URL bridge validation and the known-ID pending phase resolved both findings.
- The final review pass caught a duplicated canonical-URL constructor and incorrect use of the post-handshake `observing` phase during follow-up assignment. Canonical URL construction is now shared, and follow-ups remain `id_known_rebind_pending` until exact assignment is affirmed.
- The final independent Standards and Spec reviews reported no findings and no scope creep.
- Validation after review fixes passed 462 tests and 28 subtests, repository Ruff, changed-production type analysis, both Python package builds, npm dry-run packaging, and diff integrity. Full-repository `ty` still reports five existing diagnostics in unchanged surf-agent CLI interfaces.
