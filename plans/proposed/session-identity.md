# Generic session identity for the persistent interpreter

Tracking issue: [#22 — Add persistent Python execution for multi-turn Surf workflows](https://github.com/ewgdg/browser-skills/issues/22), still open. The session interpreter itself is released; this plan changes how it is keyed and who owns it.

Status: proposed. Nothing is implemented. Two answers are needed first; both are under "Open decisions".

## Goal

One generic rule decides which interpreter a call attaches to: the session owner's process identity (`pid` plus start time). Persistence, isolation and lifetime then hold under any harness that spawns the launcher as a descendant, without harness cooperation, and no session can adopt an interpreter created for another owner.

## Intention

The identity layer is pi-specific and, in at least one real environment on this machine, inert. Measured while reviewing the current design:

- This session's exec shells carry no `PI_SESSION_ID` or `PI_SESSION_FILE`, only `PI_CODING_AGENT=true`. `session_key()` therefore fell back to the bare name, and every socket digest matched `sha256(name)[:16]`: `probe` → `ba9c736f19e7f60b`, `alpha` → `8ed3f6ad685b959e`, `beta` → `f44e64e75f3948e9`.
- Lifetime still resolved correctly there because the `/proc` chain contained `pi`: `python3 → exec_bridge → pi → zsh → systemd`. Identity came from the environment, liveness from process ancestry, and the two disagreed.
- Driving the environment variable proved the mechanism: `PI_SESSION_ID=demo` and `PI_SESSION_ID=other` with the same `--session probe` produced `probe-4a7a88bd54593f2f` and `probe-3ee56ead7fbde796`, two interpreters, and `NameError: y` in the second.
- Ancestor stability is harness-specific: two consecutive tool calls returned different `python3` pids but the same `exec_bridge` (`2692696`) and `pi` (`1935921`). Under pi's bash tool the parent is a fresh shell per call, so "use the parent pid" is not portable.

A harness architecture can invalidate ancestry outright: under a client-server or multilane design several clients share one server, the server spawns the tool-call shells, and the client is not in the ancestor chain at all. Every lane then resolves to the same ancestor, so ancestry cannot separate them. Only the server knows which session a call belongs to, and that knowledge reaches the interpreter through its environment rather than through `/proc`.

The guarantee stated in `plans/active/persistent-interpreter.md` — two concurrent agent sessions cannot share globals even when they pick the same name — therefore holds only where the harness exports the identity. The failure case to design against is compound: an unnamespaced key plus owner resolution that falls back to the terminal (any non-pi harness) allows agent B to attach to agent A's still-running interpreter. That interpreter holds A's per-handle emission baseline, so B's first `emit()` diffs against a frame B never received — the stale-baseline class the framing contract exists to prevent, reappearing across sessions.

## Scope & constraints

- The socket, lock and log stem becomes `sha256(f"{identity}:{name}")[:16]`, where `identity` is the harness-supplied session key when present and `{owner_pid}:{owner_start}` otherwise. Length is unchanged: the digest is fixed-width, so `SOCKET_PATH_LIMIT` behaviour is untouched.
- When the identity falls back to the owner, identity and lifetime are the same reference, and a mismatched attach stops being a detected condition and becomes an unrepresentable one: a different owner derives a different path. When the harness supplies the identity, isolation is stronger still, but lifetime still belongs to the owner — an explicit path fixes sharing, not a weak anchor.
- The owner anchor is chosen by an explicit rule over the ancestor chain, biased so that a wrong anchor fails loudly. Anchoring too low re-creates the interpreter, which the frame reports as `created` and the documented recovery cell handles. Anchoring too high shares one interpreter between sessions silently.
- Identity resolution is explicit-first: an identity the harness supplies wins, and the owner anchor is the fallback. Ancestry guesses which durable process owns a call, and a client-server harness makes it guess wrong for every lane at once, so the fallback must never outrank knowledge the harness already holds.
- The explicit path is one generic environment variable (working name `SURF_SESSION_KEY`) plus whatever harness variables are accepted as inputs. A CLI flag is not part of it: anything that can inject a flag into the command can set the environment more cheaply.
- `HARNESS_COMMS` stays as a hint, never a requirement: the mechanism must work when no known harness appears in the chain.
- `emit()` writes its own frame and returns `None`; ADR-0003 settles that and this plan does not reopen it.
- No `--new-session` command. See the dominance argument below.
- Publishing a runtime revision and updating the skill's `runtime-revision` pin is a separate, explicitly authorized step, as in #21.

## Decision

Weighted matrix. Weights follow project priorities: silent corruption outranks ergonomics. Options: **A** status quo (`PI_SESSION_ID` only, bare-name fallback) · **B** explicit identity first, owner-keyed fallback · **C** `--new-session` returns a token, `--session NAME` still allowed · **D** B + C.

| Criterion | Weight | A | B | C | D |
|---|---|---|---|---|---|
| Isolation correctness (no shared globals or baselines) | 0.30 | 2 | 5 | 3 | 5 |
| Lifetime correctness (no stale adoption, no leak) | 0.20 | 3 | 5 | 3 | 5 |
| Harness independence (no cooperation needed) | 0.15 | 2 | 4 | 5 | 4 |
| Agent workflow simplicity | 0.15 | 5 | 5 | 3 | 4 |
| Implementation cost and risk | 0.10 | 5 | 3 | 3 | 2 |
| Observability and recovery | 0.10 | 4 | 4 | 5 | 5 |
| **Weighted total** | | **3.15** | **4.55** | **3.50** | **4.40** |

Score rationale, in the order it matters:

- **A scores 2 on isolation because it was measured that way**, not assumed: with no environment identity the key degenerates to the bare name.
- **B scores 5 on isolation and lifetime** because the socket path carries the resolved identity, so adopting another session's interpreter cannot be expressed, and because the harness-supplied identity is read before ancestry — which is what holds under a server architecture.
- **C scores 3 on isolation** because uniqueness is a convention: an agent that hand-types `--session surf` still collides. It scores 3 on lifetime because it changes nothing there — the interpreter still reaps on the terminal, and once the token leaves the context the leaked process is also unreachable.
- **C's 5 on harness independence is real**: no environment, no ancestry, works even where a daemon dispatches calls.
- **C scores 3 on workflow**: an extra create step, an opaque handle to carry, and a rebuild when context compaction eats it.

**D never beats B under a defensible weighting.** B is better or equal on five criteria; D's only edge is a recognizable printed handle, which is worth `0.10 × 1`. D wins only if `w(observability) > w(workflow) + w(cost)`, that is, only if a printed token matters more than the extra step and the extra code combined. So `--new-session` is not worth adding as an API; what it buys is provided by printing the owner key in the frame.

Settled by this decision:

1. Key on the resolved session identity: the harness-supplied identity when one exists, otherwise the owner reference.
2. Anchor rule, in order: (a) the nearest ancestor whose `comm` is a known harness; (b) failing that, the nearest ancestor below the session manager that is not a known shell — unknown comms are trusted here, because an unstable anchor fails loudly; (c) failing that, the outermost non-session-manager ancestor, which preserves today's behaviour for a human running the launcher from a terminal.
3. Keep the explicit identity path ahead of the chain walk, and keep it generic rather than pi-specific.
4. Print the resolved identity and the owner reference in the creation and attach frame, so identity is read, not inferred.
5. Treat a hello reply whose recorded owner differs from the caller's anchor as an error, not as something to attach to. Under this keying it can only arise from a digest collision or a mis-specified override, so failing is correct.

### Rejected alternatives

**`--pid PID` supplied by the caller.** Rejected: the pid a caller can discover is the same for every lane under one server, because the shell's parent is the server rather than the session, so two lanes would hand over the same value and share a namespace silently — the failure this plan exists to remove. It also puts `start_time` out of reach, and a bare pid cannot distinguish a live process from a recycled number, so the guard that protects the chain walk cannot be applied by hand. The caller would additionally have to rediscover the value whenever its context drops it.

**An opaque session token (`--new-session`)** stays rejected on the dominance argument above. The one thing it adds, a value the caller can print and keep, is served instead by printing the resolved identity in the frame.

**A `--key STRING` flag** is acceptable only if a caller exists that can inject a flag but not an environment variable, for example a human typing commands. It is not part of v1: it duplicates the environment path without adding a capability, and every identity input is another way to select the wrong namespace.

## Open decisions

1. **Which explicit identity inputs to accept.** Recommendation, revised after the client-server objection: read a generic `SURF_SESSION_KEY` first and *keep* `PI_SESSION_ID` and `PI_SESSION_FILE` as accepted inputs rather than deleting them. Pi is the harness whose client-server direction breaks ancestry, so deleting the one identity pi actually supplies would trade a working path for a heuristic. The mechanism stops depending on pi either way, since a harness that sets the generic variable gets identical behaviour.
2. **Is the environment variable needed in the first version at all?** It costs one read and one branch, and it is the only answer for client-server, daemon-dispatched and in-process-multiplexed harnesses. Recommendation: keep it; the client-server case is the reason it is primary rather than optional.

## Work plan

1. Refactor the anchor choice into a pure function over an injected chain (`select_anchor(chain) -> OwnerRef`), so the rule is testable without spawning process trees. Keep `resolve_owner()` as the `/proc`-reading wrapper.
2. Test-first, before changing keying:
   - two distinct anchors with the same `--session` name resolve different sockets;
   - the same anchor and name attaches to the same interpreter, cell counter continuing;
   - pid reuse: identical `pid`, different `start_time` → different socket;
   - an explicit identity outranks ancestry: with the variable set and no known harness in the chain, the socket is namespaced by the variable, not by the ancestor;
   - rule order: a chain with a known harness above an unknown wrapper anchors on the harness; a chain with only a human shell anchors on the shell, not on the session manager;
   - the override variable, when set, produces its own namespace and two different values do not share globals;
   - regressions: reset, per-cell timeout and replacement, busy rejection, concurrent session isolation, stale socket handling.
3. Implement keying and the override; update the hello reply and frame text.
4. Pre-implementation checks (must be answered, not assumed):
   - spawn two durable sub-agents and compare their ancestor chains — if they are separate `pi` processes, anchor keying separates them automatically; if they share one process, the override is required for them;
   - establish how pi's client-server or multilane mode is expected to deliver session identity per lane: whether `PI_SESSION_ID` stays per-session or becomes per-server. That answer decides whether the explicit path is sufficient for pi's next architecture or whether the launcher of each call must supply the identity.
5. Documentation: `skills/surf/docs/launcher.md` (what owns the interpreter, what the override is for), `skills/surf/SKILL.md` only if guidance changes (it should not: the agent still picks one name per task), and a note wherever `PI_SESSION_ID` is mentioned.
6. Release gate: publish a runtime revision, update `skills/surf/runtime-revision`, and confirm `tests/test_skill_distribution.py` passes. Requires explicit authorization.

## Validation

- `uv run pytest -q` for the package and skill suites; the session cases live in `packages/surf-agent/tests/test_session.py`, launcher cases in `tests/test_skill_launcher.py`.
- Isolation acceptance: two anchors, one name, concurrent cells — the second must not see the first's globals, and neither may read the other's socket.
- Lifetime acceptance: kill the anchor process; the interpreter must exit and its socket must be gone.
- Optional live check: `tests/test_installed_workflow.py` with a real browser, extending the existing session acceptance.

## Risks and failure cases

- **Anchor too high** (a shared terminal or daemon) → silent sharing wherever no explicit identity is supplied, the failure this plan exists to remove. Mitigated by the explicit path, by rule order, and by tests that assert anchoring below a known harness.
- **Isolation without lifetime.** An explicit identity gives the right namespace even when the anchor is the terminal, so the interpreter can still outlive the session that created it and keep running. Only the owner reference bounds that, so a harness wanting both properties must supply the identity *and* be the anchor.
- **Anchor too low** (a per-call wrapper with an unknown `comm`) → the interpreter is replaced every call. Loud: the frame reports `created` each time, and the recovery cell already covers reattaching.
- **Client-server or multilane harness**: the client is not an ancestor, so every lane under one server resolves to the same anchor and would share one interpreter. The explicit identity path is the only defence, and a harness that supplies no identity forces each caller to set the generic variable. This is the case that makes the explicit path primary rather than optional.
- **Context loss of the session name** is unchanged and handled as today: `--session NAME` is re-derivable from memory, which is a reason not to adopt opaque tokens.
- **Migration**: interpreters created under the old key become unreachable and are reaped by their owners at exit. No cleanup step: deleting another session's socket while it is live would break it.

## Progress

- [ ] Anchor rule extracted and unit-tested.
- [ ] Owner-keyed sockets, override, frame text.
- [ ] Pre-implementation checks answered.
- [ ] Docs updated.
- [ ] Runtime published and pin updated.

## Surprises and discoveries

Recorded here rather than in the ADR because they are implementation observations: identity is inert in this session while ownership resolves correctly; the immediate parent is stable under the code-mode exec tool (`exec_bridge`) but not under the bash tool; `emit()`-level framing is unaffected by any of this.
