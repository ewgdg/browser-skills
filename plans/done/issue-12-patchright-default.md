# Issue 12: Patchright default and explicit AXI

## Goal

Make Patchright Surf's default backend and retain AXI as the explicitly selected generic alternative.

## Intention

Surf has one implicit browser-automation choice. Fresh configuration uses Patchright without an additional selection step. Users choose AXI explicitly when they need its generic Chrome DevTools behavior.

## Scope and constraints

- Implement GitHub issue #12 only.
- Patchright and AXI are the complete supported backend set.
- Test observable CLI and configuration behavior through existing public seams.
- Preserve generic Surf commands under explicit AXI selection.
- Use `uv` for Python dependency and lockfile work.
- Keep packaging, runtime construction, setup, cleanup, tests, lock data, and user guidance aligned with the supported set.

## Work plan

1. Establish the public behavior and backend architecture.
2. Add a failing deterministic test proving fresh configuration resolves to Patchright, then implement the default.
3. Add failing CLI contract coverage for the complete supported set, then derive configuration and help from one ordered definition.
4. Consolidate packaging, runtime adapters, setup, cleanup, tests, lock data, and guidance around Patchright and AXI.
5. Run focused tests and static checks throughout, then the complete repository validation suite.
6. Review the committed diff independently against repository standards and issue #12, fix all findings, revalidate, and amend the semantic commit.

## Validation

- Focused surf-agent configuration and CLI tests pass.
- Full repository suite passes: 293 tests and 31 subtests.
- Ruff, all Python package builds, npm package dry-run, and diff integrity pass.
- Working-tree search confirms source, dependencies, tests, lock data, and guidance contain only the supported design.
- Independent Spec review: 0 findings.
- Independent Standards review: 4 findings, all addressed before final validation.

## Progress

- [x] Issue #12 acceptance criteria confirmed.
- [x] Public TDD seams confirmed from the ticket and existing CLI tests.
- [x] Patchright default slice complete.
- [x] Supported backend and help surface slice complete.
- [x] Packaging, runtime, test, lock, and guidance consolidation complete.
- [x] Full implementation validation complete.
- [x] Independent two-axis review complete.
- [x] Review fixes validated and final commit amended.

## Surprises and discoveries

- `DEFAULT_BACKEND` previously doubled as the AXI backend identity. A separate `AXI_BACKEND` identity prevents a default-selection change from redirecting AXI-specific lifecycle and cookie-import behavior.
- Cookie-family validation that depends on AXI executable identity must select AXI explicitly.

## Decisions

- `SUPPORTED_BACKENDS` is the ordered source of truth for validation and CLI choice formatting.
- Patchright is the only implicit choice; AXI selection is always explicit.

## Outcomes and retrospective

- Fresh configuration resolves to Patchright.
- Explicit AXI selection preserves generic Surf command behavior.
- Configuration, help, packaging, runtime construction, tests, lock data, and guidance now describe one coherent two-backend design.
