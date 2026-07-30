# Issue 15: Submit once and bind durable session identity

## Goal

Make a plain `surf-chatgpt ask`, or an explicit retry through its preserved pre-session thread, submit at most once and return the durable ChatGPT session ID only after the exact submitted page is atomically rebound to the deterministic session thread.

## Intention

The CLI owns phase-aware orchestration and public error projection. The live owned-page bridge owns guarded browser mutations on its serialized runtime thread. Readiness, picker selection, send, assignment observation, protection changes, and rebinding all operate on one owner-tagged page without generic browser authority or local session state.

## Scope and constraints

- Implement GitHub issue #15 only. Durable-session restart recovery and follow-ups remain in issue #16; result observation remains in issue #17; cleanup/capacity remains in issue #18.
- Treat the real `surf-chatgpt ask` JSON/exit contract and the typed owned-page bridge protocol as the public TDD seams established by the ticket.
- Use the accepted specification at commit `8177189` as normative.
- Preserve at-most-once submission across signals, caller death, bridge disconnects, gates, assignment timeout, and rebind failure.
- Never focus a page, replay send, expose prompt/DOM/browser details, or manufacture retry permission after send may have occurred.
- Keep owner, protection, page token, exact URL, and barrier state private.

## Work plan

1. Add semantic DOM fixture tests for readiness, requested picker affirmation, guarded prompt submission, canonical session assignment, and post-send gates.
2. Extend the typed owned-page protocol with guarded submission preparation, one-send mutation, and bounded assignment observation; decode only allow-listed metadata.
3. Add in-memory bridge CLI tests for plain positional/stdin ask, preserved-thread retry, protection transitions, terminal-during-handshake output, timeout/gate/disconnect failures, and exact-page rebind.
4. Implement the submission lifecycle as explicit phases: `before_send`, `send_may_have_occurred_id_unknown`, `id_known_rebind_pending`, and `handshake_complete`.
5. Add deterministic subprocess barriers and deliver real `SIGINT`, `SIGTERM`, and `SIGKILL` at each barrier; assert only public output and durable bridge/page side effects, with no timing sleeps.
6. Run focused tests and static checks throughout, then full repository validation once.
7. Commit, run the required independent Standards and Spec reviews against the starting commit, address findings, revalidate, and amend the semantic commit.

## Validation

- Focused surf-chatgpt submission DOM, lifecycle, CLI, and surf-agent owned-page tests pass during each slice.
- Real subprocess signal and bridge-disconnect cases prove one-send behavior and phase-correct recovery.
- All repository tests pass once at the end.
- Ruff, Python type analysis, package builds, npm packaging dry-run, and diff integrity pass.
- Independent Standards and Spec reviews report no unresolved findings.

## Progress

- [x] Issue #15, downstream ticket boundaries, accepted specification, and public seams confirmed.
- [x] Submission DOM and owned-page bridge operations complete.
- [x] One-send lifecycle and phase-aware recovery complete.
- [x] Real interruption and disconnect validation complete.
- [x] Full validation complete.
- [x] Independent review complete.
- [x] Semantic commit finalized.

## Decisions

- The ticket's real CLI and owned-page protocol acceptance criteria are the pre-agreed TDD seams; tests will not target lifecycle helper internals.
- Submission uses the issue #14 compare-and-move rebind primitive rather than adding another page registry or rebind implementation.
- A preserved `--thread` is never allocated or recovered implicitly; it must resolve to the exact live owner-tagged pre-session page.
- The bridge transport marks the caller phase only after a healthy live bridge can dispatch the irreversible request. A bridge known down before dispatch remains a retryable pre-send operational failure; later transport uncertainty is indeterminate.

## Outcomes and retrospective

Plain asks and preserved pre-session retries now cross one irreversible bridge-owned send barrier, observe a canonical session identity within the bounded handshake, and rebind the same live page before success. Visible gates preserve phase-correct recovery, including known-ID handoff and retained pre-send retry. Deterministic subprocess tests cover caught signals, process death, and bridge disconnect at every barrier without replaying send.

The independent review found and resolved retained-gate protection mismatch, canonical-route gate masking, late assignment acceptance, duplicated click dispatch, and arbitrary picker scoring. The final Standards and Spec re-reviews reported no unresolved findings.
