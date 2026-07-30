# Issue 14: Owned-page foundation and proactive login

## Goal

Give `surf-chatgpt` a capability-gated owned-page bridge that can prepare an unfocused protected ChatGPT page and inspect only an explicitly addressed live owned thread.

## Intention

Patchright proves the narrow authority required by `surf-chatgpt`; AXI fails before browser work. Owner, protection, and page identity remain live bridge metadata. `login` and `session current` use one production lifecycle implementation that can also run against an in-memory bridge.

## Scope and constraints

- Implement GitHub issue #14 only; submission, durable-session recovery, observation, cleanup/capacity, and recent discovery remain in issues #15 through #19.
- Test the real CLI JSON/exit contract, the typed owned-page interface, and the Patchright runtime operation boundary.
- Never focus or activate a page. Owned allocation uses target-specific background window creation and does not reuse, close, navigate, or inspect unrelated pages.
- `session current` never starts a missing bridge, allocates, navigates, recovers, or focuses.
- Keep owner, protection, page token, exact URL, profile, window, and target metadata out of public JSON.
- Treat the accepted specification at commit `8177189` as normative.

## Work plan

1. Add a typed `surf-agent` owned-page protocol with explicit capability, allocation, inspection, rebind, and protection contracts.
2. Add failing Patchright tests, then implement bridge-memory ownership/protection, non-activating dedicated allocation, guarded inspection, atomic rebind, and guarded protection.
3. Add failing CLI lifecycle tests through an in-memory owned-page bridge, then implement proactive login and exact-thread current-session inspection.
4. Verify AXI rejection occurs before every owned-page operation and that private metadata cannot enter normal public JSON.
5. Run focused tests and type analysis throughout, then complete repository validation once.
6. Commit, run the required independent Standards and Spec reviews, address findings, revalidate, and amend the semantic commit.

## Validation

- Focused owned-page, Patchright runtime, and surf-chatgpt lifecycle tests pass.
- All surf-agent and surf-chatgpt tests pass.
- Repository Ruff, type analysis, package builds, npm packaging dry-run, full tests, and diff integrity pass.
- Independent Standards and Spec reviews report no unresolved findings.

## Progress

- [x] Issue #14, downstream boundaries, current architecture, and accepted specification inspected.
- [x] Public and bridge TDD seams confirmed from the ticket.
- [x] Owned-page protocol and Patchright primitives complete.
- [x] Proactive login and exact-thread current inspection complete.
- [x] Full validation complete.
- [x] Independent review complete.
- [x] Semantic commit finalized.

## Surprises and discoveries

- `session current` must use a non-starting bridge call; the generic Patchright client would otherwise launch a browser while trying to inspect a missing thread.
- Existing generic `new` deliberately reuses a startup page and closes unbound restored pages. Owned allocation needs a separate path because issue #14 forbids both behaviors.
- Patchright owned inspection, rebinding, and protection must be dispatched before browser startup. These operations concern an already-live binding and must not create browser state when the binding or bridge is absent.
- Protection values may enter guarded mutation requests as expected/new values, but current protection never leaves the runtime response. Reuse succeeds only when the live protection already matches the request.
- URL shape alone cannot affirm `not_ready`. The exact owned page runs a metadata-only classifier whose output is restricted to four coarse states before the lifecycle projects a result.
- Target identity alone is insufficient during allocation. The created page's resulting URL must still satisfy the requested scope immediately before binding; a redirected out-of-scope target is closed without acquiring ownership.
- Bridge health must prove both Patchright backend identity and the configured profile without exposing a machine-specific path. Producer and verifier share one normalized-path fingerprint payload.
- Parsed URL components are insufficient for canonical rebinding because empty delimiters and case variants normalize away. Rebind compares the original URL to the reconstructed canonical form.

## Decisions

- The owned-page contract is an internal Python/bridge protocol, not a new public `surf-agent` command group.
- Issue #14 implements only the operations needed to truthfully affirm its listed capabilities and serve `login`/`session current`; later lifecycle behavior stays out of scope.
- The production bridge and the in-memory test bridge implement the same `OwnedPageBridge` protocol; the lifecycle has no test-only branch or second implementation.
- Login uses a pre-session-only URL scope and cannot reuse a conversation or arbitrary ChatGPT route bound to the login thread.
- Capability, scope, protection, inspection-state, and bridge-error values each have one typed definition shared by the client and Patchright runtime.
- Capability support remains a non-starting implementation contract. Live Patchright backend/profile identity is separately proven by health immediately before every operation.
- Patchright runtime remains the authority over bindings, page tokens, and protection; the owned-page transaction service validates policy and calls narrow synchronous mutation methods.

## Outcomes and retrospective

- Issue #14 now provides one typed owned-page lifecycle seam for production and in-memory use, with proactive unfocused login and exact-thread current-session inspection.
- Patchright allocation, inspection, rebinding, protection, bridge identity, metadata privacy, canonical URL, and no-focus/non-interference guarantees have behavior-level regression coverage. AXI fails before owned-page browser work.
- Final validation passed 324 tests and 28 subtests, full Ruff, both Python package builds, npm dry-run packaging, changed-production `ty`, and diff integrity. Full-repository `ty` still reports five existing diagnostics in unchanged surf-agent CLI interfaces; they remain outside issue #14.
- Independent Standards and Spec reviews completed with no unresolved findings.
