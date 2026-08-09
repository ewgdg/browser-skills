# Surf Google Search

## Goal

Add a model-invoked `surf-google-search` skill and Python CLI that searches rendered Google pages through Surf and returns compact structured organic results.

## Intention

Make Google Search a deep module: callers provide one query and a bounded page span; the implementation owns browser navigation, DOM classification, filtering, rank assignment, invocation-scoped deduplication, pacing, profile-scoped serialization, challenge handoff, cleanup, and compact JSON.

## Scope and constraints

- Version one retrieves structured results only; it does not open or synthesize destination pages.
- Use rendered `google.com` and never substitute an API, another search engine, or another Surf backend.
- Honor Surf's selected Patchright or AXI backend; Patchright is the default and initial live-test target.
- CLI: `surf-google-search [--page N] [--page-count N] [--thread THREAD] QUERY`.
- `page` is one-based; `page-count` is 1–3; both default to 1.
- One invocation emits one compact JSON object and either succeeds completely, affirms exhaustion, or fails without partial results.
- Extract primary organic results only. Return rank, title, cleaned destination URL, normalized nullable snippet, and raw nullable displayed date.
- Deduplicate only across pages visited in one invocation. Rank is assigned first, so removed duplicates leave gaps.
- Strip Google wrappers, known Google-added tracking, and text-highlight directives; preserve meaningful query parameters and named/media fragments.
- Serialize invocations per Surf browser profile with a blocking interprocess file lock. Use natural pacing before navigation.
- Preserve a single profile-scoped challenge thread; queued searches return its handoff without navigating until it is resolved or gone.
- Referrer-preserving destination clicks are outside version one.
- Deterministic tests cross the approved CLI and browser-page seams. Live Google compatibility is opt-in and excluded from normal CI.
- Implement vertical TDD slices: one failing behavioral test, minimal implementation, then continue.

## Work plan

Review remediation is deliberately narrow: make exhaustion classification fail closed, execute the DOM classifier against deterministic browser fixtures, enforce observation cardinality/state invariants, reject direct Google destinations, and guarantee deferred cleanup on output/interruption failures. Do not add exact pagination-offset arithmetic or elaborate challenge-marker recovery; those failures are recoverable and the accepted design follows rendered Next destinations.

1. Add the package skeleton and a failing CLI contract test for the default one-page successful search.
2. Implement the small public contracts, CLI parsing, browser-page port, and one-page lifecycle sufficient for the tracer test.
3. Add vertical slices for page spans, Next validation, rank gaps, invocation-scoped URL deduplication, exhaustion, and cleanup.
4. Add vertical slices for URL normalization, snippets, displayed dates, organic filtering, blockers, no-results affirmation, and fail-closed UI classification.
5. Add vertical slices for profile-scoped `flock`, natural pacing, challenge markers, queued handoff, stale-marker recovery, and thread resumption.
6. Add the model-invoked skill, install/package metadata, README guidance, and opt-in live compatibility test.
7. Run focused tests after each slice, then full tests, Ruff, package builds, npm package dry-run, and a live Patchright smoke test when safe.
8. Review the complete diff against the agreed contracts and project standards; fix findings and revalidate.

## Validation

- `uv run pytest packages/surf-google-search/tests`
- `uv run pytest`
- `uv run ruff check packages`
- Build every Python workspace package with `uv build --package <name>`.
- `npm pack --dry-run` includes the new skill and package payload.
- Opt-in live smoke returns compact valid JSON without account/UI noise and remains materially smaller than generic page text and snapshots.

## Progress

- [x] Product, interface, domain language, test seams, and ADR agreed.
- [x] Tracer CLI slice passes.
- [x] Search-page extraction and page-span behavior pass.
- [x] Serialization, pacing, and challenge behavior pass.
- [x] Skill and package integration complete.
- [x] Independent-review remediation complete.
- [x] Final validation after remediation complete.

## Surprises and discoveries

- A generic `a:has(h3)` extractor included AI source cards. Current primary organic containers require stricter structural affirmation.
- Google offset pages can contain fewer than ten eligible organic results and can overlap dynamically. Page spans therefore preserve nominal page rank slots and deduplicate only within one invocation.
- Text-fragment directives can be long, transient, and irrelevant to server retrieval, so returned URLs omit them while retaining named/media fragments.
- Surf's Patchright close operation prints its acknowledgement to stdout; the adapter must suppress that internal output so the CLI emits exactly one JSON object.
- Patchright emits direct JSON for evaluation while AXI uses its own result envelope. The browser adapter serializes the DOM observation explicitly and accepts both established Surf forms.

## Decisions

- `docs/adr/0002-use-rendered-google-search.md` records rendered Google as the search source.
- Public pagination is a one-based start page plus page count, not item offsets or quotas.
- `fcntl.flock` provides profile-scoped cooperative serialization; generic Surf and manual browser work do not participate.
- Referrer fidelity belongs to a later stateful research capability that clicks rendered results.

## Outcomes and retrospective

- Added the `surf-google-search` package, CLI, and model-invoked skill.
- The CLI returns compact structured organic results, supports one-to-three-page spans, honors Surf backend selection, and keeps Google-specific DOM knowledge out of generic `surf-agent`.
- Profile-scoped `fcntl.flock` serialization, natural pacing, and one retained challenge marker prevent cooperating search processes from concurrently hammering Google.
- Final deterministic validation passes: 428 tests, 2 skipped opt-in tests, and 28 subtests. Ruff, all Python builds, npm package dry-run, skill discovery, and the opt-in live Patchright smoke test pass.
- Live probes affirmed standard result extraction, AI-source exclusion, text-fragment removal, second/third-page rank gaps, compact single-object JSON, and affirmed no-results behavior.
- Independent-review remediation replaced generic no-results inference with an explicit rendered card, requires recognizable pagination before treating a result page as terminal, executes the extraction script against deterministic headless-browser fixtures, enforces result-state cardinality, rejects direct Google navigation destinations, and guarantees cleanup after output or interruption failures.
- Exact pagination arithmetic, resolved-marker close ordering, and malformed-marker recovery remain intentionally excluded: rendered Next is the product seam, and those marker failures are recoverable.
