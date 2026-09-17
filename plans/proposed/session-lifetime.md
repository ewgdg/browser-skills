# Session lifetime bounds and the owner guard

Tracking issue: [#22](https://github.com/ewgdg/browser-skills/issues/22), still open.

Status: proposed, nothing implemented. This plan supersedes the recommendation in `plans/proposed/session-identity.md`, which stays for reference because it holds the identity analysis and the weighted matrix; that plan's keying proposal is not being built. Shape agreed so far: protect the existing env-keyed identity path, add lifetime bounds, add an owner guard, build nothing else.

## Goal

Bound a session interpreter's lifetime to the agent session that created it under every harness, and make sure a call never attaches to an interpreter belonging to another session — without harness cooperation, without an identity layer, and without persisting interpreter state.

## Intention

Two measured problems, both in the ownership path rather than the identity path.

**The leak.** `resolve_owner()` walks `/proc` ancestors; loop one takes the nearest ancestor whose `comm` is in `HARNESS_COMMS` (`{"pi"}`), and loop two takes the outermost non-`systemd`/`init` ancestor. Under Claude Code or Codex the chain is `python → bash → node(agent) → login shell → systemd`: no `pi`, so loop two picks the **login shell**, and the interpreter outlives the agent session for as long as that shell lives — days on a desktop, indefinitely under a service or tmux. Measured on this machine: five workers from one conversation, 35.5 MB RSS each, 177.5 MB total, alive 30–47 minutes; they reap correctly only because their owner resolves to `pi`. Yesterday's `installed-session-*` sockets have no live process behind them, so reaping works exactly where the owner is right, and the leak exists exactly where the owner is a guess. There is no idle path in the code: `_watch_owner` polls owner liveness every two seconds and nothing else.

**Silent sharing.** With no harness identity the key is the bare session name, so a second session using the same name attaches to the first session's interpreter, including its per-handle emission baseline — a diff against a frame the second agent never received. The interpreter records its owner when it starts and watches it for lifetime, but `_hello_reply` reports only the interpreter's own `pid`, `start_time` and `cells`, and `_WorkerState` does not hold the owner at all. Detecting this needs the worker to report the owner it was started with; that is a small addition, not existing data.

## Scope & constraints

- Keep `session_identity()` and the existing key composition. It costs a few lines, it is already written, and it is the only mechanism that separates sessions where a harness supplies an identity — including a client-server harness whose lanes ancestry cannot see. Where it supplies nothing, the owner guard below is the protection.
- Tighten anchor selection: prefer the nearest ancestor below the session manager that is not a known shell, trusting unknown `comm` values, because anchoring too low re-creates the interpreter (loud, and the recovery cell covers it) while anchoring too high shares one interpreter silently. Mark the result **weak** when it came from the outermost fallback, which today means every ancestor below the session manager was a shell or a session manager.
- Weak owners get an idle timeout: the worker exits after `DEFAULT_SESSION_IDLE_TIMEOUT_S` (proposed 1800) with no cell. Idle means between cells; a running cell is never interrupted by it. Strong owners keep their current unbounded-but-reaped lifetime.
- Owner guard: a cell may attach only when the interpreter's recorded owner matches the caller's resolved owner. Otherwise fail loudly, naming the owner, instead of attaching. The error must not suggest `--reset`, which would let a second session discard the real owner's bindings.
- Consequence to document: the session name is exclusive per machine/profile. Concurrent sessions need separate `SURF_AGENT_HOME`, separate debug ports and different names — and today the browser layer would collide first anyway, since `SURF_AGENT_HOME` is unset by default, `threads/<name>.json` is shared per thread name, and ports 9335/9336/9346 are fixed defaults.
- Interpreter state is **not** persisted. See the deferred decision below.
- `emit()` keeps writing its own frame (ADR-0003); no framing change here.

## Deferred: checkpoint and replay of interpreter state

Proposal considered: write interpreter state to a cache directory on exit and reload it into a fresh interpreter, so a weak-owner timeout or a host reload is cheap. Reference implementation examined: `pi-codex-conversion`'s notebook mode, which does exactly this for a Deno kernel.

What that implementation actually does, from `dist/tools/notebook-mode`: top-level bindings are serialised with v8 `serialize` (structured clone) into one payload file plus a manifest holding per-entry offset, length and kind; functions are stored as **source text** and re-created by evaluating them; promises, weak collections, natives and bound functions are skipped, as is anything over a per-variable or total cap; restore returns `{restored, skipped[{name, reason}]}`; the checkpoint directory is identity-scoped (`project`, `session`, `agentDir`) with a schema version and garbage collection of superseded sessions; size caps are heap-relative (8 MiB minimum, 256 MiB maximum, 10000 entries). Cost: roughly 25 KB of checkpoint code plus a journal that materialises cell sources, plus health and recovery modules.

Why it does not transfer to a Surf interpreter:

1. **The valuable state is live handles.** A `Thread` holds a backend bridge connection and page ids belonging to the dead process. Structured clone (and pickle) cannot restore that, and the skip-report would report exactly the bindings the agent wants. Restoring a fabricated handle that looks live is worse than losing it.
2. **The emission baseline must never be restored.** A restored baseline gives the next `emit()` a diff base whose frame the agent no longer holds — the stale-baseline class the framing contract forbids. Correct behaviour after a replacement is already specified: the first emission is full.
3. **Helper functions belong in a file.** Python functions carry `__globals__`, so the notebook's source-text trick needs its imports re-established anyway; an importable `.py` module is the same thing done properly, and the launcher already runs ordinary files.
4. **Action replay stays forbidden.** The replay contract says a cell's browser side effects are never inferred from a fault, and a submission is never blind-retried. A journal of cell sources is only useful if cells are safe to re-run, which browser cells are not.

What replaces it, at zero new code and one documentation line: derived data goes in the agent's own `json.dump` when the agent decides it matters, helpers go in a module the cell imports, and the browser is the durable layer for page state. Revisit only if re-derivation after a replacement is observed to be a real cost — and if it is, the notebook's skip-report shape is the right one to copy.

## Work plan

1. Extract anchor selection into a pure function over an injected chain, returning the owner reference plus a weak flag, so the rule is testable without spawning process trees. Keep `resolve_owner()` as the `/proc`-reading wrapper.
2. Test-first, before wiring:
   - a chain with a durable non-shell ancestor above a per-call shell selects that ancestor, strong;
   - a chain of nothing but shells and a session manager selects the outermost non-session-manager ancestor, weak;
   - a chain containing `pi` selects `pi`, strong — the current behaviour is preserved;
   - pid reuse: same `pid`, different `start_time`, does not match a live owner.
3. Weak-owner idle timeout in the worker's watcher thread, plus a test with a short timeout: an idle interpreter exits, and the next call reports a replacement; a call inside the timeout attaches.
4. Owner guard: store the `OwnerRef` in `_WorkerState`, add `owner_pid` and `owner_start` to the hello reply, and compare them in the client against the caller's resolved owner. Mismatch fails loudly without offering `--reset`; match attaches. Test both.
5. Docs: `skills/surf/docs/launcher.md` (owner kinds, weak-owner timeout, name exclusivity, what concurrent sessions require), `skills/surf/SKILL.md` (keep the recovery cell as-is; add the line about keeping cross-invocation data in files).
6. Release gate: publish a runtime revision, update `skills/surf/runtime-revision`, confirm `tests/test_skill_distribution.py`. Requires explicit authorization.

## Validation

- `uv run pytest -q`; the session suite is `packages/surf-agent/tests/test_session.py` (28 tests), launcher cases in `tests/test_skill_launcher.py`.
- Temporal acceptance: kill the anchor process and the interpreter exits; idle past the weak-owner timeout and it exits, with the next call reporting a replacement rather than reattaching silently.
- Sharing acceptance: two owners, one name — the second call fails with the owner in the message, and neither shares globals.
- Optional live check: extend `tests/test_installed_workflow.py` session acceptance with a real browser.

## Risks

- **Anchor too low** (a per-call wrapper with an unknown `comm` is trusted as an anchor) → the interpreter is replaced every call. Loud: every frame reports `created`.
- **Anchor too high remains reachable** for a harness that dispatches calls from a shared service → silent sharing unless the harness supplies an identity. The owner guard narrows this to harnesses that cannot express identity at all.
- **Weak-owner timeout too short** → a replacement during a human handoff. Recovery is the documented init cell, so the cost is one cell; the value should be generous by default.
- **Deferred checkpoint** means a weak-owner timeout costs re-derivation rather than nothing. Accepted deliberately; the alternative is a subsystem that cannot restore the bindings that matter.

## Progress

- [ ] Anchor rule extracted and unit-tested.
- [ ] Weak-owner idle timeout.
- [ ] Owner guard.
- [ ] Docs updated.
- [ ] Runtime published and pin updated.

## Surprises and discoveries

- Identity and ownership were separate mechanisms and disagreed in practice: this session's shells carry no `PI_SESSION_ID` (key degenerates to the bare name, digests verified as `sha256(name)[:16]`) while ownership resolved correctly to `pi` through the chain `python3 → exec_bridge → pi → zsh → systemd`.
- Ancestor stability is harness-specific: two consecutive tool calls gave different `python3` pids but the same `exec_bridge` (2692696) and `pi` (1935921), so "key on the parent pid" is not portable while "key on the resolved durable owner" is.
- Expensive review was spent on the identity layer, whose isolation guarantee protects a concurrency scenario the browser layer already refuses: shared state directory, shared per-thread state file, fixed default ports. Fixing ownership is the change that actually pays.
