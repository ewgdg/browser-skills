# Explicit sessions: create, reuse, expire

Tracking issue: [#22](https://github.com/ewgdg/browser-skills/issues/22), still open.

Status: active; implemented in the runtime and the launcher, with the test suites passing. The release gate is not done: `skills/surf/runtime-revision` still names the previous runtime, so the repository's skill is ahead of its pin until a runtime revision is published and pinned (requires explicit authorization).

Supersedes every earlier revision of this plan and the keying recommendation in `plans/proposed/session-identity.md`, which stays for its identity analysis only. Shape: sessions are created explicitly, addressed by an id the launcher prints, and die on an idle timeout or an explicit kill.

## Goal

One mechanism decides which interpreter a call reaches and how long it lives: an id that `--new-session` returns and later calls pass back. No process-ancestry walking, no harness environment variables, no implicit reuse of a caller-chosen name.

## Intention

The interpreter already existed; the complexity came from how sessions were addressed. Sessions were created implicitly by using a name, names were keyed by a harness environment variable that is often absent, and lifetime was inferred by walking `/proc` to guess which durable process owned the call. Measured consequences:

- With no `PI_SESSION_ID`, the key degenerated to the bare name — every socket digest verified as `sha256(name)[:16]` — so two sessions using `--session surf` shared one interpreter and its emission baseline. A diff could then be headed against a frame the second agent never received.
- Ownership guessed wrong outside pi: the chain `python → bash → node(agent) → login shell → systemd` has no `pi`, so the owner resolved to the login shell and the interpreter outlived the agent session. Five workers from a single conversation measured 35.5 MB each, 177.5 MB total, alive 30–47 minutes; nothing in the code reaps an idle interpreter.
- Roughly 110 lines of runtime plus owner-walk tests, a Linux-only `/proc` dependency, and harness-specific behaviour existed to make *implicit* sessions safe. Explicit creation removes the problem instead of compensating for it.

## Scope & constraints

- `--new-session [--name SLUG] [--ttl SECONDS] -` creates an interpreter and appends a `--- BEGIN session metadata ---` … `--- END session metadata ---` block to stdout as the final output of that call. Later calls print no metadata at all.
- `--session ID -` reuses exactly that interpreter. An unknown id is an error listing live sessions: no implicit creation, no bare-name reuse, so a typo cannot silently start a fresh interpreter.
- **TTL**: idle timeout measured between cells. A running cell is never interrupted by it — that is `--timeout`'s job. Default `DEFAULT_SESSION_IDLE_TIMEOUT_S` = 1800, overridable per session at creation.
- `--kill-session ID` kills immediately. A cell in flight dies with it and its side effects are unknown, exactly as with a timeout.
- `--list-sessions` prints live sessions with id, pid, started, idle for, and working directory. The directory is how an agent recognises its own session after losing the id from context, and it needs no ancestry.
- The socket files in `$XDG_RUNTIME_DIR/surf-agent` are the registry; listing is a directory scan that skips stale entries by liveness. No separate state file.
- **Deleted**: `resolve_owner`, `ancestor_chain`, `process_alive`, `read_process`, `OwnerRef`, the owner-watch thread, `session_log_path` and the log-open path, `session_identity`, the identity composition in `session_key`, and the `HARNESS_COMMS`/`SESSION_MANAGER_COMMS`/`DEAD_STATES`/`OWNER_WATCH_INTERVAL_S` constants.
- **Kept**: `--reset`, per-cell `--timeout`, busy rejection, per-cell byte capture, `emit()` framing (ADR-0003), and the recovery-cell guidance.
- Interpreter state is not persisted; see the deferred section.

## Decisions

1. **Id format**: `<slug>-<8 hex>` when `--name` is passed, else `<8 hex>`. Ids are opaque; only ids that exist are accepted.
2. **TTL default 1800 s**, per-session override. Rationale: 35.5 MB measured per live interpreter, so an idle session should not outlive a task by much, while a human handoff inside one task should survive. Shorter favours memory, longer favours handoffs.
3. **Discovery is required** (`--list-sessions`), because an opaque id cannot be re-derived from memory the way a name could. Working directory plus idle time is the identification hint.
4. **Kill is immediate and documented as such**; clean shutdown removes the socket, and the listing skips sockets with no listener.
5. **One metadata block, create call only**: the id is reported once, as a delimited key-value block appended to stdout at the end of the create call. No later call and no stderr frame repeats it.
6. **No session log file, and no other artifact.** The worker's stdout and stderr start on a pipe the launcher owns and drains during the readiness window, then point at `/dev/null` once the interpreter is listening.

### Metadata block

```text
--- BEGIN session metadata ---
session_id: surf-7f3a91c2
idle_timeout_s: 1800
--- END session metadata ---
```

- **Why stdout**: a consumer that captures only stdout, or routes stderr into its own channel, would lose the one value the caller cannot re-derive. pi's bash tool returns both streams together, so either would be readable there, but stdout is the safer bet across harnesses.
- **Why last**: ordering between the streams is not stable — the same command shape produced stderr-first once and interleaved output another time — so the block anchors itself at the end of the cell's own stdout rather than depending on a position among frames. It is written after the cell's output, so it is still the final stdout content when the first cell raises.
- **Why BEGIN/END rather than bare `---` fences**: the same tool result can contain observation frames and unified diffs, whose headers are `--- observation 1` and `+++ observation 2`. A bare fence would read as a diff header; named delimiters match the existing observation convention and make a truncated block detectable.
- **Why not on every call**: the id is already in the caller's command line, which is in its context. Repetition adds noise without adding recoverability; `--list-sessions` is the recovery path after context loss.
- **Why key-value rather than prose**: fields can be added later without a new format, and a caller that parses has exactly one shape to parse.
- **Not an escaping protocol**: page text is arbitrary, so a cell can print a lookalike block. The rule is that the *final* block on stdout of the create call is the launcher's and nothing else can be trusted, which is the same standing the frames have today.
- Printing every frame on stdout stays rejected: each cell's stdout would carry launcher noise and `run.py --session ID - | jq` would stop working.

### Why the log file goes away

The log exists for one hard reason and two soft ones. Hard: a detached worker must never hold the per-call stdout pipe, or the caller never sees EOF and the tool call hangs until the session ends — this was observed, not deduced. Pointing fd 1 and 2 at `/dev/null` once the worker is listening satisfies that requirement with no file, and a startup pipe keeps a failure explainable. Soft: raw `os.write(1, …)` and subprocess output, and the worker's own note when it exits on a deadline, currently land there rather than being lost.

Neither is worth a file:

- **Startup diagnostics live in memory, not on disk.** The launcher spawns the worker with a pipe for stdout and stderr, drains it during the readiness window, and the worker re-points its own fd 1 and 2 at `/dev/null` immediately after it starts listening. That pipe is a fresh one the launcher owns, not the caller's capture pipe, so nothing can hang; releasing it is still required, or a later write from the worker would fail against a pipe whose reader is gone. When the worker fails, the launcher kills it if needed, reads whatever the pipe holds, and puts that text into the error it raises. No file is created on success or on failure.
- **Everything else is dropped, deliberately.** Only Python-level `print()` and `emit()` output reaches the agent. A cell that shells out or writes to fd 1 produces nothing visible, and the worker's deadline note disappears with the log; the client's replacement report stays the only signal, which is the contract anyway. If fd-level output ever matters, the answer is capturing it into the cell's own output, which risks flooding a tool result when a cell shells out — not a file.

## Rejected

- **Owner-keyed sockets with a tightened `/proc` anchor.** Correct, but it exists to make implicit sessions safe. Explicit ids make collisions impossible without an anchor rule, an env fallback, or a harness-specific behaviour matrix — and they drop the Linux-only dependency.
- **Owner-mismatch guard.** Dominated by keying, and dominated even harder by explicit ids: more code, a refusal instead of a namespace, and no coverage when the anchor is wrong.
- **Harness identity variables** (`PI_SESSION_ID`, `PI_SESSION_FILE`). No longer read; with explicit ids there is nothing to namespace, and the "identity is inert" bug class disappears with them.
- **Checkpoint and replay of interpreter state.** Deferred, unchanged reasons: live `Thread` handles cannot be restored (the bridge and page ids belonged to the dead process), the emission baseline must never be restored, Python helpers belong in an importable module, and browser actions must never be replayed. Reference implementation examined is `pi-codex-conversion`'s notebook checkpoints: v8 structured clone into a payload plus a manifest, functions re-created from source, promises and natives skipped with reasons, identity-scoped directory with GC, ~25 KB of code. If this is ever built, copy the skip-report shape.

## Work plan

1. Test-first, before implementation:
   - creating a session appends a metadata block whose `session_id` is the final stdout content, including when the first cell raises;
   - a reusing call writes no metadata block and nothing extra to stdout, so cell output stays pipeable;
   - a cell that prints its own lookalike `--- BEGIN session metadata ---` block does not change the rule: the final block is the launcher's;
   - no log file is created in the runtime directory, and a cell that writes with `os.write(1, …)` or runs a subprocess produces no agent-visible output and leaves nothing behind;
   - a worker that cannot start puts its own startup text into the launcher's error, and leaves no file behind;
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

- **Lost id** (context compaction, a long interruption): loud — `unknown session` plus a live list — and the cost is rebuilding state, never contamination. The id is reported once, in the create call's metadata block; `--list-sessions` is the recovery path, and a long task should write the id next to its notes.
- **TTL expiry during a human handoff**: the recovery cell covers it, at the cost of one cell. `--ttl` exists for tasks that involve a human.
- **Leak bounded by TTL rather than by owner death**: a crashed agent leaves one interpreter until it idles out. Accepted, bounded and measured.
- **fd-level output is discarded**: subprocess output and raw fd writes no longer reach the agent or a file. Documented, and the deliberate cost of dropping the log; the deferred alternative is capturing fd writes into the cell's output, which risks flooding a tool result when a cell shells out.
- **Startup diagnostics are in-memory only**: if the worker dies before writing anything to the pipe, the launcher can only report that the interpreter did not become ready. Accepted — nothing is kept on disk.
- **Migration**: every existing name-keyed session becomes unreachable; those interpreters expire on their own, and no cleanup step is needed because deleting a live session's socket would break it.

## Trigger to revisit checkpoints

If TTL expiry or a lost id is observed to cost real re-derivation in practice, the deferred checkpoint design becomes worth its ~25 KB, using the notebook's skip-report shape. Until then, derived data belongs in the agent's own `json.dump` and helpers in an importable module.

## Progress

- [x] CLI: create, reuse, kill, list, TTL (`packages/surf-agent/src/surf_agent/session.py`, `skills/surf/scripts/run.py`).
- [x] Ownership machinery deleted: ancestry walk, owner reference, harness identity, session key, log file and lock file.
- [x] Tests: `packages/surf-agent/tests/test_session.py` (36 tests) and `tests/test_skill_launcher.py`; full suite 293 passed, 4 skipped.
- [x] Docs updated: `skills/surf/SKILL.md`, `skills/surf/docs/launcher.md`, `README.md`, and the session-addressing bullets in `plans/active/persistent-interpreter.md`.
- [ ] Runtime published and pin updated.

## Surprises and discoveries

- A stop request is handled on its own thread, so the accept loop must check the stopping flag on its timeout path as well: the first implementation cleared it only where a connection had just been accepted, and a stopped worker then lived until its idle timeout. The kill test caught it.
- A worker from an earlier runtime answers hello without the new fields. `--list-sessions` ignores such interpreters with a frame on stderr and `--kill-session` refuses them with an explanation rather than pretending they are absent, which matters only during the transition but would otherwise hide a live process.

- Streams: pi's bash tool documents that it returns stdout and stderr, and this session's exec tool merges them into one field, but their relative order is not stable — the same shape of command produced stderr-first once and chronological interleaving another time. Labelled values survive that; positional ones do not.

- Identity and ownership were separate mechanisms that disagreed in practice: this session's shells carry no `PI_SESSION_ID`, while ownership resolved to `pi` through `python3 → exec_bridge → pi → zsh → systemd`.
- Ancestor stability is harness-specific: consecutive tool calls gave different `python3` pids but the same `exec_bridge` and `pi`, so "key on the parent pid" is not portable.
- The owner guard detour looked like scope reduction and was the opposite: more code than keying, a refusal instead of a namespace, and no coverage of the wrong-anchor case.
- The entire ownership apparatus existed to compensate for implicit session creation. Making creation explicit deletes it rather than fixing it.

