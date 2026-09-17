# Session ownership: keying, anchor and lifetime bounds

Tracking issue: [#22](https://github.com/ewgdg/browser-skills/issues/22), still open.

Status: proposed, nothing implemented. Supersedes the recommendation in `plans/proposed/session-identity.md`, which stays for reference because it holds the identity analysis and the weighted matrix. Shape: key each interpreter on the resolved session identity, tighten anchor selection, bound weak owners' idle lifetime, build nothing else.

Revision note: an earlier revision of this file dropped keying and replaced it with an owner-mismatch guard. That was wrong — the guard costs more code and produces a worse outcome, and it fails in the one case it was meant to cover. See "Rejected: the owner guard".

## Goal

Each session gets its own interpreter **by construction**: the socket, lock and log paths it derives already differ from every other session's, so no call can reach or adopt another session's interpreter, and there is nothing to detect or refuse. Lifetime is bounded by the session that created the interpreter under every harness, with no harness cooperation, no new caller inputs, and no persisted interpreter state.

## Intention

Two measured problems, both in the ownership path.

**The leak.** `resolve_owner()` walks `/proc` ancestors; loop one takes the nearest ancestor whose `comm` is in `HARNESS_COMMS` (`{"pi"}`), loop two takes the outermost non-`systemd`/`init` ancestor. Under Claude Code or Codex the chain is `python → bash → node(agent) → login shell → systemd`: no `pi`, so loop two picks the **login shell**, and the interpreter outlives the agent session for as long as that shell lives — days on a desktop, indefinitely under a service or tmux. Measured on this machine: five workers from one conversation, 35.5 MB RSS each, 177.5 MB total, alive 30–47 minutes; they reap correctly only because their owner resolves to `pi`. Yesterday's `installed-session-*` sockets have no live process behind them, so reaping works exactly where the owner is right, and the leak exists exactly where the owner is a guess. No idle path exists in the code today: `_watch_owner` polls owner liveness every two seconds and nothing else.

**Silent sharing.** With no harness identity the key is the bare session name, so a second session using the same name derives the *same* socket and attaches to the first session's interpreter, including its per-handle emission baseline — a diff against a frame the second agent never received. Keying on the owner removes the collision instead of detecting it: a different owner derives a different path, and a path is not something a caller can ignore.

## Scope & constraints

- **Key on the resolved identity**: `session_identity()` when the harness supplies one, else `{owner_pid}:{owner_start}`. The stem becomes `<slug>-<sha256(f"{identity}:{name}")[:16]>`, and the lock and log paths follow, so two owners using the same `--session` name cannot see each other's files.
- **Tighten anchor selection**: prefer the nearest ancestor below the session manager that is not a known shell, trusting unknown `comm` values, and mark the result **weak** when it came from the outermost fallback — which today means every ancestor below the session manager was a shell or a session manager. Anchoring too low re-creates the interpreter, which is loud and covered by the documented recovery cell; anchoring too high shares one interpreter silently.
- Keying and the anchor rule are one change: keying is only as isolating as the anchor is per-session, so an anchor that resolves to a terminal would hand every session in that terminal the same key.
- **Weak owners get an idle timeout**: the worker exits after `DEFAULT_SESSION_IDLE_TIMEOUT_S` (proposed 1800) with no cell. Idle means between cells; a running cell is never interrupted. Strong owners keep today's unbounded-but-reaped lifetime.
- Keep `session_identity()` as the first input. Do not add `SURF_SESSION_KEY`, do not add flags, do not add wire fields. A harness that supplies an identity keeps winning, which is what separates lanes in a client-server harness that ancestry cannot see.
- Interpreter state is not persisted; see the deferred decision below.
- `emit()` keeps writing its own frame (ADR-0003); nothing here changes framing.
- Honest limit to document: the browser layer is the real concurrency gate. `SURF_AGENT_HOME` is unset by default, `threads/<name>.json` is shared per thread name, and ports 9335/9336/9346 are fixed defaults, so two concurrent sessions need separate homes and ports before interpreter naming even matters.

## Rejected: the owner guard

An earlier revision proposed a check instead of keying: the worker reports the owner it was created with, and a caller whose resolved owner differs refuses to attach and fails loudly.

- **It costs more.** The worker has to store the owner, the hello reply has to carry new fields (`_hello_reply` currently reports only the interpreter's own `pid`, `start_time` and `cells`), the client needs a comparison and a new error path, and all of it needs tests. Keying changes one hash input.
- **It delivers less.** The guard refuses a second session that could simply have had its own interpreter. Keying gives isolation with no error, no message for a caller to interpret, and no name bookkeeping.
- **It fails in the case it was invented for.** When the anchor is wrong — a client-server harness where every lane resolves to the same server — the guard's comparison matches, and the two sessions share silently, exactly as they would with no guard at all. So the guard only helps when the anchor is already right, and when the anchor is right, keying solves the problem outright.
- **It carries a footgun.** The refusal message must not mention `--reset`, since that would let a second session discard the real owner's bindings; keying removes the need for the message altogether.

## Deferred: checkpoint and replay of interpreter state

Proposal considered: write interpreter state to a cache directory on exit and reload it into a fresh interpreter, so a weak-owner timeout or a host reload is cheap. Reference implementation examined: `pi-codex-conversion`'s notebook mode, which does exactly this for a Deno kernel.

What that implementation actually does, from `dist/tools/notebook-mode`: top-level bindings are serialised with v8 `serialize` (structured clone) into one payload file plus a manifest holding per-entry offset, length and kind; functions are stored as **source text** and re-created by evaluating them; promises, weak collections, natives and bound functions are skipped, as is anything over a per-variable or total cap; restore returns `{restored, skipped[{name, reason}]}`; the directory is identity-scoped (`project`, `session`, `agentDir`) with a schema version and garbage collection of superseded sessions; caps are heap-relative (8 MiB minimum, 256 MiB maximum, 10 000 entries). Cost: roughly 25 KB of checkpoint code plus a journal that materialises cell sources, plus health and recovery modules.

Why it does not transfer to a Surf interpreter:

1. **The valuable state is live handles.** A `Thread` holds a backend bridge connection and page ids belonging to the dead process. Structured clone and pickle both refuse; those bindings are exactly what would land in `skipped`. Restoring a fabricated handle that looks live is worse than losing it.
2. **The emission baseline must never be restored.** A restored baseline gives the next `emit()` a diff base whose frame the agent no longer holds — the stale-baseline class the framing contract forbids. Correct behaviour after a replacement is already specified: the first emission is full.
3. **Helper functions belong in a file.** Python functions carry `__globals__`, so the notebook's source-text trick needs its imports re-established anyway; an importable `.py` module is the same idea done properly, and the launcher already runs ordinary files.
4. **Action replay stays forbidden.** The replay contract says a cell's browser side effects are never inferred from a fault, and a submission is never blind-retried. A journal of cell sources is only useful if cells are safe to re-run, which browser cells are not.

What replaces it, at zero new code and one documentation line: derived data goes in the agent's own `json.dump` when the agent decides it matters, helpers go in a module the cell imports, and the browser is the durable layer for page state. Revisit only if re-derivation after a replacement is observed to be a real cost — and if it is, the notebook's skip-report shape is the right one to copy.

## Work plan

1. Extract anchor selection into a pure function over an injected chain, returning the owner reference plus a weak flag, so the rule is testable without spawning process trees. Keep `resolve_owner()` as the `/proc`-reading wrapper.
2. Test-first, before wiring:
   - a chain with a durable non-shell ancestor above a per-call shell selects that ancestor, strong;
   - a chain of nothing but shells and a session manager selects the outermost non-session-manager ancestor, weak;
   - a chain containing `pi` selects `pi`, strong — current behaviour preserved;
   - pid reuse: same `pid`, different `start_time`, is not the same identity;
   - two owners, one `--session` name → different socket paths, and a cell in one never sees the other's globals;
   - one owner, one name across calls → same interpreter, cell counter continuing;
   - an env identity outranks the owner, and two identities never share a socket. Existing coverage to extend: `test_session_key_is_scoped_to_the_harness_identity` in `packages/surf-agent/tests/test_session.py`.
3. Wire it: the client resolves the owner before deriving paths, and `session_socket_path` takes the resolved identity so the socket, lock and log stems stay consistent — including for `--reset`, which must derive the same path as the cells it resets. Callers and tests in `session.py`, `test_session.py` and `tests/test_skill_launcher.py` update in the same change.
4. Weak-owner idle timeout in the worker's watcher thread, plus a test with a short timeout: an idle interpreter exits and the next call reports a replacement; a call inside the timeout attaches.
5. Docs: `skills/surf/docs/launcher.md` (owner kinds, weak-owner timeout, what the resolved identity means for socket naming, what concurrent sessions require), `skills/surf/SKILL.md` (recovery cell unchanged; cross-invocation data belongs in files).
6. Release gate: publish a runtime revision, update `skills/surf/runtime-revision`, confirm `tests/test_skill_distribution.py`. Requires explicit authorization.

## Validation

- `uv run pytest -q`; session suite `packages/surf-agent/tests/test_session.py` (28 tests), launcher cases `tests/test_skill_launcher.py`.
- Isolation acceptance: two owners, one name → two interpreters, neither seeing the other's globals; then the same owner twice → the same interpreter.
- Lifetime acceptance: kill the anchor and the interpreter exits; idle past the weak-owner timeout and it exits, with the next call reporting a replacement rather than reattaching silently.
- Optional live check: extend `tests/test_installed_workflow.py` session acceptance with a real browser.

## Risks

- **Anchor too low** (a per-call wrapper with an unknown `comm` is trusted) → the interpreter is replaced every call. Loud: every frame reports `created`.
- **Anchor too high without a harness identity** (a shared service owning several logical sessions) → those sessions still share one interpreter. The env identity is the only fix; record it as the known limit of the fallback rather than pretending the anchor rule covers it.
- **Weak-owner timeout too short** → a replacement during a human handoff. Recovery is the documented init cell, so the cost is one cell; the default should be generous.
- **Migration**: interpreters created under the old bare-name key become unreachable and are reaped by their owners at exit. No cleanup step — deleting another session's socket while it is live would break it.
- **Signature change**: `session_socket_path` gains the resolved identity; every caller and test moves in the same commit.

## Progress

- [ ] Anchor rule extracted and unit-tested.
- [ ] Identity-aware socket, lock and log paths.
- [ ] Weak-owner idle timeout.
- [ ] Docs updated.
- [ ] Runtime published and pin updated.

## Surprises and discoveries

- Identity and ownership were separate mechanisms and disagreed in practice: this session's shells carry no `PI_SESSION_ID` (key degenerates to the bare name, digests verified as `sha256(name)[:16]`) while ownership resolved correctly to `pi` through the chain `python3 → exec_bridge → pi → zsh → systemd`.
- Ancestor stability is harness-specific: two consecutive tool calls gave different `python3` pids but the same `exec_bridge` (2692696) and `pi` (1935921), so "key on the parent pid" is not portable while "key on the resolved durable owner" is.
- The guard detour is recorded because it looked like scope reduction and was the opposite: more code than keying, a worse outcome for the caller, and no coverage of the case it was invented for — a wrong anchor makes both sessions agree on the owner, which is exactly when a comparison cannot help.
- Expensive review went into an isolation guarantee for a concurrency scenario the browser layer already refuses; fixing ownership is the change that pays.

