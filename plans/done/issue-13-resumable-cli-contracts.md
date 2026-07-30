# Issue 13: Resumable CLI contracts and session addressing

## Goal

Expose surf-chatgpt's JSON-only resumable-session contract defined by the accepted specification.

## Intention

The CLI becomes a narrow process boundary. It parses and validates public input, converts commands into typed lifecycle requests, emits one safe JSON value, flushes it, and only then permits private cleanup. Browser orchestration remains behind one lifecycle protocol for later implementation tickets.

## Scope and constraints

- Implement GitHub issue #13 only.
- Treat real CLI invocation, stdout, stderr, exit status, and lifecycle requests as the public TDD seam.
- Accept only strict session IDs or exact canonical ChatGPT conversation URLs.
- Expose no text output mode, raw exception content, prompts, titles, URLs, or browser diagnostics.
- Keep browser behavior behind the lifecycle seam; issues #14 through #19 own its implementation.
- Preserve human-readable parser help.
- Use the accepted specification at commit `8177189` as the normative source.

## Work plan

1. Add strict session-address tests, then implement ID normalization, canonical URL derivation, and deterministic Surf-thread derivation.
2. Add typed contract and safe-projection tests, then implement request types, lifecycle protocol, safe errors/causes, command outcomes, and private cleanup.
3. Replace CLI tests one vertical slice at a time: grammar and dispatch, validation, one-object output, exit mapping, signal handling, and flush-before-cleanup.
4. Run focused tests and static checks throughout, then the full repository validation suite once.
5. Review the committed diff independently against repository standards and issue #13, address findings, revalidate, and amend the semantic commit.

## Validation

- Focused surf-chatgpt CLI, contracts, errors, and session-address tests pass.
- All surf-chatgpt tests and the complete repository test suite pass.
- Ruff, Python type analysis, package builds, and diff integrity pass.
- Independent Standards and Spec reviews report no unresolved findings.

## Progress

- [x] Issue #13, its downstream ticket boundaries, and accepted specification confirmed.
- [x] Public TDD seam confirmed from the ticket's observable CLI acceptance criteria.
- [x] Strict session addressing complete.
- [x] Typed contracts and safe projection complete.
- [x] JSON-only CLI replacement complete.
- [x] Full validation complete.
- [x] Independent two-axis review complete.
- [x] Semantic commit finalized.

## Surprises and discoveries

- The accepted specification lives on commit `8177189`, not on `main`.
- The repository has no configured Python typechecker; the implementation will use an isolated modern type-analysis command without changing project dependencies.
- The accepted specification demonstrates only `bridge_disconnected` as a stable cause type and enumerates only the five submission phases. The public cause vocabulary remains closed to those values until later lifecycle work establishes another required type.

## Decisions

- Issue #13 establishes the lifecycle boundary but does not implement browser behavior owned by issues #14 through #19.
- Valid commands without an injected lifecycle fail safely before browser work until issue #14 provides the default implementation.
- Bare `--wait` is normalized before argparse consumes positionals, so `ask --wait PROMPT` preserves the prompt while `--wait=SECONDS` remains the only explicit-value spelling.
- Public result validation owns the exact exit-code mapping and ID-only session schema, so an injected lifecycle cannot bypass process or privacy contracts.

## Outcomes and retrospective

- The public parser now exposes only the resumable ask, session, abandon, and login grammar.
- Strict addressing, typed requests, closed public JSON schemas, fixed safe errors, signal-aware exit semantics, and flush-before-cleanup all share one lifecycle seam.
- The active surf-chatgpt skill now documents the resumable contract and user-only browser handoff.
- Final validation passed: 280 tests and 28 subtests, Ruff, `ty`, both Python package builds, npm dry-run packaging, and diff integrity.
- Independent Standards and Spec review axes both completed with zero unresolved findings.
