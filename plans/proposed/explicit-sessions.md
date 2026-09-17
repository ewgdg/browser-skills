# Explicit sessions: create, reuse, expire

Tracking issue: [#22](https://github.com/ewgdg/browser-skills/issues/22), still open.

Status: proposed, nothing implemented. Supersedes every earlier revision of this plan and the keying recommendation in `plans/proposed/session-identity.md`, which stays for its identity analysis only. Shape: sessions are created explicitly, addressed by an id the launcher prints, and die on an idle timeout or an explicit kill.

## Goal

One mechanism decides which interpreter a call reaches and how long it lives: an id that `--new-session` returns and later calls pass back. No process-ancestry walking, no harness environment variables, no implicit reuse of a caller-chosen name.

## Intention

The interpreter already existed; the complexity came from how sessions were addressed. Sessions were created implicitly by using a name, names were keyed by a harness environment variable that is often absent, and lifetime was inferred by walking `/proc` to guess which durable process owned the call. Measured consequences:

- With no `PI_SESSION_ID`, the key degenerated to the bare name — every socket digest verified as `sha256(name)[:16]` — so two sessions using `--session surf` shared one interpreter and its emission baseline. A diff could then be headed against a frame the second agent never received.
- Ownership guessed wrong outside pi: the chain `python → bash → node(agent) → login shell → systemd` has no `pi`, so the owner resolved to the login shell and the interpreter outlived the agent session. Five workers from a single conversation measured 35.5 MB each, 177.5 MB total, alive 30–47 minutes; nothing in the code reaps an idle interpreter.
- Roughly 110 lines of runtime plus owner-walk tests, a Linux-only `/proc` dependency, and harness-specific behaviour existed to make *implicit* sessions safe. Explicit creation removes the problem instead of compensating for it.

## Scope & constraints

- `--new-session [--name SLUG] [--ttl SECONDS] -` creates an interpreter and prints its id as a launcher frame on stderr, next to the existing identity frames: `--- session surf-7f3a91c2 (created; idle timeout 1800 s) ---`. stdout stays ordinary script output.
- `--session ID -` reuses exactly that interpreter. An unknown id is an error listing live sessions: no implicit creation, no bare-name reuse, so a typo cannot silently start a fresh interpreter.
- **TTL**: idle timeout measured between cells. A running cell is never interrupted by it — that is `--timeout`'s job. Default `DEFAULT_SESSION_IDLE_TIMEOUT_S` = 1800, overridable per session at creation.
- `--kill-session ID` kills immediately. A cell in flight dies with it and its side effects are unknown, exactly as with a timeout.
- `--list-sessions` prints live sessions with id, pid, started, idle for, and working directory. The directory is how an agent recognises its own session after losing the id from context, and it needs no ancestry.
- The socket files in `$XDG_RUNTIME_DIR/surf-agent` are the registry; listing is a directory scan that skips stale entries by liveness. No separate state file.
- **Deleted**: `resolve_owner`, `ancestor_chain`, `process_alive`, `read_process`, `OwnerRef`, the owner-watch thread, `session_identity`, the identity composition in `session_key`, and the `HARNESS_COMMS`/`SESSION_MANAGER_COMMS`/`DEAD_STATES`/`OWNER_WATCH_INTERVAL_S` constants.
- **Kept**: `--reset`, per-cell `--timeout`, busy rejection, per-cell byte capture, `emit()` framing (ADR-0003), and the recovery-cell guidance.
- Interpreter state is not persisted; see the deferred section.

## Decisions

1. **Id format**: `<slug>-<8 hex>` when `--name` is passed, else `<8 hex>`. Ids are opaque; only ids that exist are accepted.
2. **TTL default 1800 s**, per-session override. Rationale: 35.5 MB measured per live interpreter, so an idle session should not outlive a task by much, while a human handoff inside one task should survive. Shorter favours memory, longer favours handoffs.
3. **Discovery is required** (`--list-sessions`), because an opaque id cannot be re-derived from memory the way a name could. Working directory plus idle time is the identification hint.
4. **Kill is immediate and documented as such**; clean shutdown removes the socket, and the listing skips sockets with no listener.

## Rejected

- **Owner-keyed sockets with a tightened `/proc` anchor.** Correct, but it exists to make implicit sessions safe. Explicit ids make collisions impossible without an anchor rule, an env fallback, or a harness-specific behaviour matrix — and they drop the Linux-only dependency.
- **Owner-mismatch guard.** Dominated by keying, and dominated even harder by explicit ids: more code, a refusal instead of a namespace, and no coverage when the anchor is wrong.
- **Harness identity variables** (`PI_SESSION_ID`, `PI_SESSION_FILE`). No longer read; with explicit ids there is nothing to namespace, and the "identity is inert" bug class disappears with them.
- **Checkpoint and replay of interpreter state.** Deferred, unchanged reasons: live `Thread` handles cannot be restored (the bridge and page ids belonged to the dead process), the emission baseline must never be restored, Python helpers belong in an importable module, and browser actions must never be replayed. Reference implementation examined is `pi-codex-conversion`'s notebook checkpoints: v8 structured clone into a payload plus a manifest, functions re-created from source, promises and natives skipped with reasons, identity-scoped directory with GC, ~25 KB of code. If this is ever built, copy the skip-report shape.

## Work plan

1. Test-first, before implementation:
   - creating a session returns an id and prints it in the frame;
   - reusing that id attaches and the cell counter continues;
   - an unknown id errors and lists live sessions;
   - idle past the TTL → the interpreter is gone and the id is unknown;
   - a cell running longer than the TTL is not interrupted;
   - `--kill-session` stops a live interpreter and removes its files;
   - `--list-sessions` skips stale sockets;
   - two sessions never share globals; busy rejection stays per interpreter;
   - `--reset` discards bindings without replacing the interpreter.
2. Implement create, reuse, kill and list; delete the ownership machinery; replace the owner-watch thread with the idle timer.
3. Docs: `skills/surf/docs/launcher.md` (CLI, TTL, discovery), `skills/surf/SKILL.md` (create once per task, keep the id, expect expiry), and any `--session NAME` examples in `README.md` or the skill docs.
4. Release gate: publish a runtime revision, update `skills/surf/runtime-revision`, confirm `tests/test_skill_distribution.py`. Requires explicit authorization.

## Validation

- `uv run pytest -q`: session suite `packages/surf-agent/tests/test_session.py`, launcher cases `tests/test_skill_launcher.py`, distribution guard `tests/test_skill_distribution.py`.
- Acceptance: create → three cells reusing state → kill → id unknown; create → idle past a short TTL → id unknown; create two sessions, interleave cells, confirm isolation.
- Optional live check: extend `tests/test_installed_workflow.py` session acceptance.

## Risks

- **Lost id** (context compaction, a long interruption): loud — `unknown session` plus a live list — and the cost is rebuilding state, never contamination. Mitigation: print the id in every frame, and recommend the agent write the id next to its task notes for long tasks.
- **TTL expiry during a human handoff**: the recovery cell covers it, at the cost of one cell. `--ttl` exists for tasks that involve a human.
- **Leak bounded by TTL rather than by owner death**: a crashed agent leaves one interpreter until it idles out. Accepted, bounded and measured.
- **Migration**: every existing name-keyed session becomes unreachable; those interpreters expire on their own, and no cleanup step is needed because deleting a live session's socket would break it.

## Trigger to revisit checkpoints

If TTL expiry or a lost id is observed to cost real re-derivation in practice, the deferred checkpoint design becomes worth its ~25 KB, using the notebook's skip-report shape. Until then, derived data belongs in the agent's own `json.dump` and helpers in an importable module.

## Progress

- [ ] CLI: create, reuse, kill, list, TTL.
- [ ] Ownership machinery deleted.
- [ ] Docs updated.
- [ ] Runtime published and pin updated.

## Surprises and discoveries

- Identity and ownership were separate mechanisms that disagreed in practice: this session's shells carry no `PI_SESSION_ID`, while ownership resolved to `pi` through `python3 → exec_bridge → pi → zsh → systemd`.
- Ancestor stability is harness-specific: consecutive tool calls gave different `python3` pids but the same `exec_bridge` and `pi`, so "key on the parent pid" is not portable.
- The owner guard detour looked like scope reduction and was the opposite: more code than keying, a refusal instead of a namespace, and no coverage of the wrong-anchor case.
- The entire ownership apparatus existed to compensate for implicit session creation. Making creation explicit deletes it rather than fixing it.

