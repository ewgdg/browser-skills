# Live cookie import

## Goal

Implement ADR-0001 so Surf Agent can refresh selected login cookies from a normal, still-running Chrome profile without cloning the profile, stopping the source browser, adding a backend, or copying unrelated browser data.

## Source of truth

- Domain terms: `CONTEXT.md`
- Accepted architecture: `docs/adr/0001-import-cookies-from-live-browser-profile.md`
- Baseline commit: `7c9b0bd`

If this plan conflicts with the ADR, follow the ADR and record the discrepancy before coding.

## User-visible contract

```bash
surf-agent profile cookie-source show
surf-agent profile cookie-source set \
  --source ~/.config/google-chrome \
  --source-profile Default \
  --domain github.com \
  --domain openai.com
surf-agent profile cookie-source set \
  --source ~/.config/google-chrome \
  --source-profile Default \
  --all-domains
surf-agent profile cookie-source reset
surf-agent profile import-cookies
```

Rules:

- Import is opt-in. `set` requires either one or more `--domain` values or explicit `--all-domains`; they are mutually exclusive.
- `import-cookies` always performs a fresh source backup and bypasses fingerprint suppression.
- Automatic import occurs only before starting an inactive AXI/Patchright profile and only when the configured source fingerprint changed.
- Camoufox rejects cookie import/configuration as unsupported.
- Existing destination cookies are never deleted merely because they are absent from the source.
- No time-based refresh heuristic exists.

## Scope and constraints

- Linux V1; unsupported platforms fail closed.
- The source Chrome profile may remain running and locked.
- The destination Surf Chrome-family profile must be inactive while importing.
- Source and destination must belong to the same browser family and OS user.
- Existing destination `Local State.os_crypt` must equal the source metadata. A new destination may initialize only this metadata from the source.
- Copy cookie rows without decrypting or transforming encrypted values.
- Import cookies only. Do not copy Local Storage, IndexedDB, passwords, autofill, history, extensions, caches, service-worker data, tabs, or profile preferences.
- Preserve current backend priority and environment override behavior.
- Preserve unknown config keys when changing backend or cookie-source configuration.
- No mandatory real-Chrome test.

## Intended module structure

Keep `cli.py` as orchestration, not a new monolith.

### `surf_agent/config.py` — new

Own persisted configuration:

- Atomic load/write with user-only permissions.
- Existing backend preference currently implemented in `cli.py`.
- `CookieSourceConfig` and `CookieScope` parsing/serialization.
- Config mutations preserve unrelated top-level keys.

A persisted cookie source records the resolved user-data directory, profile name, detected browser family, and normalized scope. Do not store cookie values.

### `surf_agent/cookie_import.py` — new deep module

Expose one primary interface, such as:

```python
CookieImporter.run(force: bool) -> CookieImportResult
```

Own:

- Platform, owner, browser-family, profile-path, encryption-metadata, schema, and destination-inactivity validation.
- Source Cookies path discovery. Support the browser-family-compatible candidates `Cookies` and `Network/Cookies`; fail on ambiguity rather than guessing.
- SQLite online backup from the live source into a temporary staging database.
- Domain filtering and transactional upsert into the inactive destination.
- New-destination initialization from the filtered staging database.
- Source fingerprint calculation and persisted successful-import state.

### `surf_agent/chrome_lifecycle.py` — new

Own Chrome-family lifecycle coordination:

- Interprocess lifecycle lock around import/start and zero-page stop/recheck decisions.
- Destination profile activity detection, including manual `profile open` Chrome processes.
- Pre-start cookie import ordering.
- Two-second zero-user-visible-page grace policy.
- Test injection seams for clock, sleeper, process inspection, and importer.

### Existing backend integration

- `backends/axi.py`: centralize startup preflight; actual page inventory drives idle shutdown.
- `backends/local_bridge.py`: accept a narrow pre-start callback/coordinator rather than importing cookie policy.
- `backends/patchright/backend.py`: supply the active Patchright profile and pre-start coordinator.
- `backends/patchright/bridge.py`: track a shutdown deadline on the bridge/runtime thread; never call Playwright objects from a `threading.Timer`.
- `backends/bridge_common.py`: if needed, add a server `service_actions()` seam that checks Patchright’s deadline after responses.
- `cli.py`: command parsing/dispatch and construction only.

## Work plan

### Checkpoint 0 — Confirm clean baseline

Before implementation:

```bash
git status --short
git log -1 --oneline
uv run pytest -q
```

Expected baseline commit is `7c9b0bd`; working tree should contain only this active plan.

### Checkpoint 1 — Prefactor persisted config without behavior change

Write `packages/surf-agent/tests/test_config.py` first.

Tests must lock down:

- Backend preference remains environment → persisted config → default.
- Backend set/reset preserves unrelated keys.
- Atomic write failure leaves the old file intact.
- Written config is user-only.
- Malformed config produces a clear `SurfAgentError`.

Then move config responsibilities from `cli.py` into `config.py`. Keep compatibility exports only where existing tests/public imports require them; do not retain duplicate implementations.

Checkpoint validation:

```bash
uv run pytest packages/surf-agent/tests/test_config.py packages/surf-agent/tests/test_cli.py -q
```

### Checkpoint 2 — Cookie-source configuration and CLI

Add failing CLI/config tests for:

- `profile cookie-source set|show|reset` dispatches without constructing or probing a browser.
- Exactly one scope form is required.
- Repeated domains normalize, deduplicate, and sort.
- Reject schemes, paths, ports unless explicitly supported, wildcards, empty values, IP/public-suffix scopes, and `--domain` combined with `--all-domains`.
- Use a maintained Public Suffix List implementation; do not hard-code suffixes.
- Resolve the source root/profile, enforce containment, reject symlink/junction profile roots, and detect browser family on Linux.
- `show` never exposes secrets.
- `reset` preserves backend configuration and imported destination cookies.
- `profile import-cookies` fails clearly when no source is configured.

Update `cli.py` help only after tests describe the final syntax.

### Checkpoint 3 — Manual live-source import tracer bullet

Add `packages/surf-agent/tests/test_cookie_import.py` using real temporary SQLite databases and a live source connection.

Required observable tests:

- Online backup reads the latest committed cookie while the source connection remains open in WAL mode.
- Domain `example.com` matches `example.com`, `.example.com`, and true subdomains but not `badexample.com`.
- Scoped source rows are inserted.
- Matching destination cookie identities are updated.
- Destination-only cookies survive.
- Out-of-scope source cookies remain absent.
- `--all-domains` still performs upsert-only merging.
- Partitioned cookie columns are preserved.
- Any row/schema failure rolls back the complete destination transaction.
- Cookie values never appear in output or error text.

Implementation requirements:

1. Open the source database read-only without SQLite `immutable=1`.
2. Use `sqlite3.Connection.backup()` into a temporary on-disk database; keep the source browser untouched.
3. Validate staging with `PRAGMA integrity_check` and an expected `cookies` table.
4. Compare source and destination table columns plus relevant unique indexes. Fail on incompatible schemas.
5. Build SQL from validated identifiers; never assume only `(host_key, name, path)` is unique because current schemas may include partition/source columns.
6. Merge selected rows in one destination transaction using all schema columns and SQLite conflict handling.
7. Never issue deletion for missing source rows.
8. On failure, rollback and remove staging.

New/empty destination behavior:

- If the destination has no Cookies database, filter the validated source backup in staging and atomically install it as the initial destination database.
- Initialize only `Local State.os_crypt` from the source when destination metadata is absent.
- If destination metadata exists and differs, fail before creating or replacing cookie data.
- Do not invent a Chromium cookie schema.

### Checkpoint 4 — Compatibility, destination inactivity, and fingerprinting

Add focused tests for:

- Non-Linux rejection.
- Source/destination path overlap rejection.
- Source and destination filesystem ownership mismatch.
- Browser-family mismatch or unprovable family.
- Existing mismatched `os_crypt` rejection; equal or new-destination metadata succeeds.
- Explicit import refuses an active destination and never stops it automatically.
- Cookie database candidate discovery works for root `Cookies` and `Network/Cookies` and fails on ambiguity.
- Fingerprint includes presence, inode, size, `mtime_ns`, and `ctime_ns` for the selected Cookies database and existing WAL/journal sidecars.
- Unchanged fingerprint skips automatic import.
- Main DB, WAL, journal, source/profile/scope/destination identity changes invalidate the fingerprint.
- Explicit import always runs.
- Fingerprint is persisted only after a committed successful import.
- Fingerprint changes during source backup cause staging discard and bounded retry; exhaustion fails closed.

Persist automatic-import state separately from user configuration under the existing state root. Write atomically.

### Checkpoint 5 — Serialize automatic import before Chrome-family startup

Add `packages/surf-agent/tests/test_chrome_lifecycle.py` first.

Required behavior:

- Determine the actual active destination profile: AXI uses `chrome_profile_dir`; Patchright uses `patchright_profile_dir`; Camoufox is unaffected/rejected.
- Generic process discovery recognizes any Chrome root using the destination `--user-data-dir`, including manual `profile open`, not only debug-port/pipe processes.
- Automatic changed-cookie import completes before any AXI Chrome/bridge or Patchright bridge process starts.
- Import failure prevents launch.
- Unchanged fingerprint reaches launch without opening destination SQLite.
- Concurrent startup attempts serialize through an interprocess `fcntl.flock`; the second rechecks health and does not import/start twice.
- Lock release is guaranteed on success and failure.

Refactor AXI’s duplicated startup routes into one preflight path before adding the import hook. Add a narrow `before_start` seam to `LocalBridgeClient`; keep generic bridge code independent of cookie concepts.

### Checkpoint 6 — Stop idle bridge after zero user-visible pages

Use the glossary definition in `CONTEXT.md`. Background targets and extension workers do not count; any normal tab/window counts even if Surf does not remember it.

AXI tests:

- Closing the final user-visible page starts exactly a two-second injected grace period.
- Re-list actual bridge pages after the grace period.
- An unmanaged visible page prevents shutdown.
- A page opened during the grace period cancels shutdown.
- `close-matching` performs one final recheck, not one sleep per page.
- Stop/open decisions share the lifecycle lock.

Patchright tests:

- Closing the final visible `context.pages` entry arms a deadline only after the close response can complete.
- A new/open page cancels the deadline.
- Unmanaged normal pages count; service workers/background targets do not.
- Deadline expiry closes the persistent context and requests HTTP bridge shutdown on its existing runtime/server thread.
- Context recovery or transient empty state is not mistaken for user-requested idle shutdown.

Implementation guidance:

- Do not use `threading.Timer` with Patchright’s Playwright objects or persistent `asyncio.Runner`; they are thread-affine.
- Prefer a deadline checked by the single-threaded HTTP server’s `service_actions()` or equivalent loop hook.
- Auto-stop failure should warn and leave the runtime alive; it must not turn a successful page close into a cookie-import failure.

This checkpoint intentionally replaces existing documentation/tests asserting that `close` always leaves the bridge alive.

### Checkpoint 7 — Documentation and integrated behavior

Update:

- `README.md`
- `skills/surf/SKILL.md`
- `skills/surf/docs/backends.md`
- `skills/surf/docs/axi-backend.md`
- `skills/surf/docs/patchright-backend.md`

Document:

- Explicit opt-in and domain exposure.
- Live locked source support via SQLite online backup.
- Same-family/user/encryption requirement.
- Upsert-only convenience semantics and non-propagated source logout.
- Fingerprint-driven automatic import with no time heuristic.
- Inactive destination requirement.
- Two-second zero-user-visible-page shutdown and next-start refresh.
- Failure behavior and recovery commands.

Do not document profile copying, browser suspension, Btrfs, origin storage, saved passwords, or an extension backend as part of the design.

## Error and recovery contract

- All expected filesystem, JSON, SQLite, validation, lock, and process errors become concise `SurfAgentError` messages; no raw traceback from normal CLI failures.
- Source Chrome is never stopped, suspended, modified, or launched.
- Destination mutation occurs only after all compatibility and staging validation passes.
- Existing-destination merge is one SQLite transaction; rollback leaves it unchanged.
- New-destination installation uses staging and atomic replacement in the destination directory.
- Failed imports do not update the fingerprint.
- Automatic import failure aborts backend startup rather than silently using stale cookies.
- Explicit import never kills an active destination to make itself succeed.
- Configuration/fingerprint writes are atomic and preserve the prior valid file on interruption.

## Validation

Run focused tests after each checkpoint. Final required validation:

```bash
uv run pytest packages/surf-agent/tests -q
uv --directory packages/surf-agent run python -m unittest discover -s tests
uv run ruff check packages/surf-agent
git diff --check
git status --short
```

No real-Chrome test gates completion. An optional manual smoke may be documented but must not touch the user’s normal profile automatically.

## Progress

- [x] Rejected profile-copy changes removed; repository returned to committed ADR baseline.
- [x] ADR and glossary committed at `7c9b0bd`.
- [x] Checkpoint 0 baseline verified: `7c9b0bd`; `uv run pytest -q` passed 196 tests and 29 subtests before implementation.
- [x] Checkpoint 1 config prefactor complete.
- [x] Checkpoint 2 cookie-source CLI complete.
- [x] Checkpoint 3 explicit cookie import complete.
- [x] Checkpoint 4 validation/fingerprinting complete.
- [x] Checkpoint 5 automatic pre-start import complete.
- [x] Checkpoint 6 zero-page shutdown complete.
- [x] Checkpoint 7 docs and full validation complete.

## Decisions already settled

Do not reopen these during implementation without explicit user approval and an ADR update:

- No new backend.
- No profile copying.
- No Local Storage/IndexedDB import.
- No source-browser suspension or filesystem snapshot dependency.
- No time-based refresh interval.
- No destination-cookie deletion in V1.
- No mandatory real-Chrome integration test.
- No automatic cookie import before explicit source/scope consent.

## Outcomes and retrospective

Completed July 12, 2026. Final validation: `uv run pytest packages/surf-agent/tests -q` passed 176 tests and 26 subtests; `uv --directory packages/surf-agent run python -m unittest discover -s tests` passed 126 tests; Ruff and `git diff --check` passed.

The importer accepts a `cookies` table only when source and destination have identical column sets and the same single usable, non-partial, column-backed unique-index tuple. This prevents partition/source identity columns from being collapsed during `ON CONFLICT`; ambiguous, expression, partial, absent, or mismatched identities fail before destination mutation.

No ADR deviation was made. New-destination publication is no-replace: Local State is created with exclusive creation and the staged Cookies database is published by same-filesystem hard link, so a concurrently appearing artifact is preserved; only the importer-created Local State is rolled back after a failed second publication. Runtime import revalidates configured source roots, profile leaves, cookie databases, and sidecars as regular non-symlink paths before and around backup.

AXI idle stop is evaluated only after Surf-observed `close` or `close-matching` operations using two successful user-visible-page inventories and one two-second recheck. It does not install a resident observer for independent manual closes; that remains outside V1. If AXI cannot provide a parseable page inventory, Surf leaves the bridge alive. Patchright closed-context recovery intentionally terminates the bridge instead of relaunching its persistent context in-process; the triggering request fails and the next request starts a fresh bridge through `LocalBridgeClient.before_start`, which reruns lifecycle preflight. Patchright also excludes its `--password-store=basic` and `--use-mock-keychain` defaults at persistent-context launch, because imported Linux v11 cookies require Chrome’s real OS password store/keychain. Future work, if approved, can add real-browser smoke coverage for supported Chrome-family releases.
