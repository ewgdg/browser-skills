# Surf AXI Persistent Backend

## Goal
Replace the flaky `surf` JavaScript/CDP path with a `chrome-devtools-axi` backend using a long-lived bridge and a dedicated skill-local Chrome profile.

## Intention
Keep the existing `surf-agent` UX and one-thread/one-page mental model, but route browser operations through AXI instead of direct `surf` commands. AXI must be started once and kept alive; normal cleanup closes pages only, not the bridge, so Chrome setup/approval is not repeatedly requested.

## Evidence Driving This Plan
- Direct `surf` on an empty default page alternated `js 'return 1'` between 30s timeout and success, while `page.state` stayed ~35-40ms. Fault is in surf's JS execution bridge, not X.com or `surf-agent` wrapper.
- Playwright CLI extension worked for automation, but did not reliably control existing user tabs/profile state; closing its banner/connect surface killed the bridge.
- `chrome-devtools-axi` produced stable snapshots and repeated `eval 1` without hangs. Follow-up safety work moved the default from real-profile auto-connect to a dedicated profile so AXI page listing does not touch hostile/user-owned tabs.

## Scope
In scope:
- Add AXI backend support to `skills/surf/src/surf_agent/cli.py`.
- Preserve existing command shape where reasonable:
  - `go/open` -> AXI `newpage` or `open`
  - `page.read` / `read` -> AXI `snapshot`
  - `js` -> AXI `eval`
  - `click`, `type`, `scroll`, `screenshot`, `back`, `wait` -> AXI equivalents
  - `state`, `list`, `close`, `reset`, `close-matching` -> page/thread state management
- Persist per-thread page IDs under `.state/`.
- Add explicit bridge lifecycle commands only if needed, but normal cleanup must not stop AXI.
- Add command timeouts and fail-fast errors.
- Update `skills/surf/SKILL.md` with AXI setup and approval semantics.
- Add/adjust tests for backend command translation and state behavior.

Out of scope:
- Reimplementing AXI or chrome-devtools-mcp.
- Automating Chrome approval prompts.
- Supporting Playwright backend.
- Keeping legacy surf backend or `SURF_AGENT_BACKEND` fallback.

## Constraints
- Use `trash-put`, not `rm`.
- Durable docs belong in `docs/` if broader than skill instructions; this change likely only needs `skills/surf/SKILL.md` plus this plan.
- Do not call `chrome-devtools-axi stop` during normal thread cleanup.
- If AXI bridge is down or approval is needed, fail clearly. Do not spin/retry indefinitely.
- Use hard subprocess timeouts lower than outer harness timeout.
- Avoid large unbounded snapshots in tests.

## Proposed Design

### Backend selection
AXI-only. Legacy direct `surf` CLI fallback and `SURF_AGENT_BACKEND` selection have been removed to avoid backend drift and accidental old-window semantics.

Useful AXI env defaults embedded by `surf-agent`:

```bash
CHROME_DEVTOOLS_AXI_PORT=9335
CHROME_DEVTOOLS_AXI_BROWSER_URL=http://127.0.0.1:9336
CHROME_DEVTOOLS_AXI_MCP_PATH=... # optional speed-up, not required
```

Dedicated profile is default so AXI global page listing does not enumerate the user's main Chrome tabs.

### State model
Use AXI page state only:

```json
{
  "backend": "axi",
  "page_id": 22,
  "url": "https://x.com/explore",
  "title": "Explore / X"
}
```

Old direct-surf window state is not readable. Valid AXI state is just `backend: "axi"` plus `page_id`; stale pages are detected by failed `select_page` and then forgotten.

### AXI process wrapper
Add a small `AxiBackend` abstraction:

```text
_run_axi(args, timeout_s) -> CompletedProcess
_run_axi_text(args, timeout_s) -> str
_parse_pages(output) -> list[Page]
```

Command base:

```bash
npx -y chrome-devtools-axi <command> ...
```

Allow override:

```text
SURF_AGENT_AXI_BIN="chrome-devtools-axi" or "npx -y chrome-devtools-axi"
```

Use `subprocess.run(..., timeout=...)` and return structured `SurfAgentError` on timeout:

```text
AXI command timed out after Ns: eval ...
Bridge may be down or waiting for Chrome approval.
```

### Thread/page creation
For a thread with no saved page:
- `go <url>`: run `newpage <url>`, parse selected page ID from `pages` after command if AXI output does not expose ID directly.
- `new`: create `newpage about:blank` or lightweight data URL, save page ID.
- `state`: if no saved page, print `{open:false}`. Do not create page.

For an existing page:
- Before command, verify page exists via `pages`.
- Select it with `selectpage <id>` if command is page-scoped.
- If missing, forget state and either create only for browser commands that imply creation (`go`) or fail for read/actions.

### Command mapping
Initial minimal mapping:

| surf-agent command | AXI command |
|---|---|
| `go <url>` / `open <url>` | `newpage <url>` for new thread, `open <url>` after selecting existing page |
| `page.read`, `read` | `snapshot` |
| `page.text` | `eval document.body.innerText` |
| `page.state` | `eval JSON.stringify({title:document.title,url:location.href})` or parse `snapshot` header only |
| `js <code>` | `eval <expr>` |
| `click @uid` | `click @uid` |
| `type <text>` | `type <text>` |
| `key` / `press` | `press <key>` |
| `scroll down/up` | `scroll down/up` |
| `screenshot --output <path>` | `screenshot <path>` |
| `back` | `back` |
| `wait <ms>` | `wait <ms>` |

Compatibility aliases can be thin. Do not overfit all surf CLI flags in first pass.

### Bridge lifecycle
- `close`: close selected page (`closepage <id>`) and forget thread.
- `close-matching`: close matching pages only.
- `close-all`: close remembered pages only.
- New optional command: `bridge-stop` -> AXI `stop`, documented as destructive because next use may need approval.
- Never call `stop` automatically.

### Error handling
Classify common failures:
- AXI command timeout -> bridge/page hang; print recovery hint.
- approval/handshake failure -> ask user to approve Chrome prompt and retry.
- page missing -> state stale; forget and report.
- parse failure -> include short raw output prefix.

## Work Plan
1. Add backend config and AXI wrapper classes/functions in `cli.py`.
2. Add page state dataclass and parsers for AXI `pages` output.
3. Implement AXI `state`, `list`, `new`, `go`, `close`, `focus` equivalent if possible.
4. Implement minimal browser commands: `page.read`, `page.text`, `page.state`, `js`, `click`, `type`, `key/press`, `scroll`, `screenshot`, `back`, `wait`.
5. Preserve forbidden command guard for tab/window commands or remap them to managed page operations.
6. Add tests with fake AXI runner:
   - no state does not create on `state`
   - `go` creates/saves page
   - existing command selects page first
   - `close` closes page but does not call `stop`
   - timeout raises clear error
   - stale page forgets state
7. Update `skills/surf/SKILL.md`:
   - AXI setup env
   - first-use approval requirement
   - persistent bridge rule: do not stop
   - known recovery steps
8. Run tests.
9. Live smoke test:
   - `state` with no thread
   - `go https://x.com/explore`
   - `js 'JSON.stringify({title:document.title,dark:...})'`
   - `page.read`
   - `close`
   - verify bridge still alive with `pages`.

## Validation
Automated:

```bash
cd skills/surf
uv run python -m unittest discover -s tests
```

Manual smoke:

```bash
uv run surf-agent --thread axi-test go https://x.com/explore
uv run surf-agent --thread axi-test js 'JSON.stringify({title:document.title,bg:getComputedStyle(document.body).backgroundColor})'
uv run surf-agent --thread axi-test page.read
uv run surf-agent --thread axi-test close
npx -y chrome-devtools-axi pages # bridge should still be alive
```

Expected:
- No recurring Chrome approval while bridge remains alive.
- No 30s alternating JS timeouts on empty page.
- X/browser state comes from the dedicated `chrome-profile/` profile; log in there once if needed.

## Risks
- AXI output format is human-readable, not guaranteed stable. Mitigation: keep parser small, fail with raw output prefix, consider direct MCP later.
- Dedicated profile may require approval/setup after bridge death. Mitigation: persistent bridge policy and clear error.
- Many existing surf command flags may not map exactly. Mitigation: implement minimal set first; document unsupported flags.
- Existing tests assume surf window IDs. Mitigation: either split surf-vs-axi tests or rewrite around backend abstraction.

## Decisions
- Backend trial target: `chrome-devtools-axi`, using official `chrome-devtools-mcp` under the hood.
- Normal cleanup closes pages, not bridge.
- No scripted approval bypass.
- Playwright CLI extension rejected for fragile current-tab/control-surface behavior.

## Progress
- Implemented AXI default backend in `skills/surf/src/surf_agent/cli.py`.
- Removed legacy direct `surf` backend and `SURF_AGENT_BACKEND` selection; surf-agent is AXI-only.
- Added fake AXI tests for state, command mapping, cleanup lifecycle, timeout handling, stale state, and AXI pages parsing.
- Updated `skills/surf/SKILL.md` for AXI setup, first-use approval, persistent bridge rule, and recovery.

## Outcomes & Retrospective
- Automated validation passes: `cd skills/surf && uv run python -m unittest discover -s tests`.
- Review loop found parser and whitespace issues; both fixed.
- Live Chrome smoke test found an AXI snapshot UID false-positive in page id extraction; fixed with stricter extraction and regression test.
- Live smoke passed for `go https://example.com`, `state`, `js document.title`, `page.read`, and `close`; bridge remained alive.
- Follow-up changed wrapper defaults so `surf-agent` launches a dedicated Chrome profile at `<skill-dir>/chrome-profile` with remote debugging port `9336` and `--class=surf-agent`, then embeds AXI `BROWSER_URL=http://127.0.0.1:9336` plus bridge port `9335`. Each new thread opens a normal Chrome `--new-window` seeded with exact title `Surf Agent` before navigation; raw `--app` remains an option if bare app-shell behavior becomes preferable.
- Review loop required safety hardening: newly detected pages must verify exact bootstrap identity (`document.title` and `location.href`) before adoption. Later cleanup removed redundant local-only `owner`/`token` markers from persisted AXI state.
- Cleanup pass removed direct `surf` CLI fallback code/tests/docs and legacy `window-id`/`id` management aliases; `page-id` remains the AXI page-id command. Current automated validation: 21 unit tests pass.
