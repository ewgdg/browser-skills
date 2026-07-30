# Resumable surf-chatgpt sessions

**Status:** Implementation-ready

**Implementation target:** `packages/surf-chatgpt`, with owned-page bridge support in `packages/surf-agent`

## Objective

Make a ChatGPT conversation recoverable as soon as ChatGPT assigns its durable session ID. A plain `surf-chatgpt ask` submits once and returns that ID without waiting for the answer. Waiting and later inspection are read-only observations of the latest response attempt.

The live Surf browser bridge retains the exact submitted page while it remains alive. Generating, human-blocked, unclassifiable, or explicitly retained pages survive short-lived CLI callers. After a bridge restart, the durable ChatGPT session remains recoverable under the same deterministic Surf thread, although recovery may use a new browser page.

## Scope

This specification covers:

- the public command and JSON interface;
- latest-attempt states and observation behavior;
- at-most-once submission and phase-aware interruption;
- deterministic session-thread rebinding and restart recovery;
- retained-page ownership, protection, cleanup, and capacity;
- rendered-UI recent-session discovery;
- privacy, focus preservation, and unrelated-page non-interference;
- implementation module seams; and
- deterministic and opt-in live acceptance tests.

It does not cover production rollout, compatibility with another command interface, undocumented ChatGPT HTTP endpoints, or multi-host orchestration.

## Browser backend baseline

- Patchright is the default surf-agent backend.
- Patchright implements the owned-page capabilities required by surf-chatgpt.
- AXI remains available for generic Surf use. surf-chatgpt fails before browser activity when AXI is selected because AXI does not provide the owned-page contract.
- Camoufox support is removed from surf-agent source, configuration, optional dependencies, tests, and documentation because its experimental fingerprint-resistance path has no active use.

## Domain model

The terms in `CONTEXT.md` are normative. In particular:

- A **ChatGPT session** is the durable conversation identified by `/c/<id>`.
- A **Surf thread** is a browser-page routing handle. It is not conversation identity.
- A **browser bridge** owns pages independently of short-lived CLI callers.
- An **observer** reads or waits on an already-recoverable session and never owns generation.
- A **retained page** is a user-visible page kept by the bridge for a submission, session, or human handoff.
- **Abandonment** is the only automatic path allowed to stop an active response attempt.

## Normative invariants

1. **Submission is at most once.** Once send may have been issued, no surf-chatgpt path automatically resubmits the prompt.
2. **Session identity precedes optional waiting.** Submission succeeds only after `send → observe /c/<id> → rebind exact page` completes.
3. **The latest response attempt is the observed entity.** A follow-up creates a new latest attempt. An affirmed terminal attempt does not regress.
4. **Every public attempt state requires affirmative current-page evidence.** There is no `unknown` attempt state.
5. **Observation is read-only, repeatable, and non-consuming.** `status`, `result`, and waiting may recover a page but do not send, stop, or mutate conversation content.
6. **A live rebind preserves exact page identity.** It changes only the bridge registry key. It does not navigate, recreate, close, or focus the page.
7. **Restart recovery preserves the ChatGPT session, not the browser page token.**
8. **Automatic page mutation requires ownership proof and current allowed-page scope.** URL similarity, title, content, or recency never establish ownership.
9. **Automatic inspection is metadata-only.** Conversation content crosses the browser extraction boundary only for explicit `session result`; visible titles cross it only for explicit `session recent`.
10. **surf-chatgpt never focuses or activates a browser page.** An agent may derive and present a focus command from the returned thread, but only the user may run it.
11. **Age, inactivity, timeout, caller exit, and process death do not imply abandonment.**
12. **No local session or run registry is added.** Recovery uses the durable session ID, deterministic Surf thread, exact canonical URL, or explicitly supplied preserved pre-session thread.

## Public command interface

```text
surf-chatgpt ask [--session ID_OR_URL | --thread SURF_THREAD]
                 [--model QUERY] [--thinking QUERY]
                 [--wait[=SECONDS]] [--retain]
                 [--pace natural|none] [--allow-logged-out]
                 [PROMPT]

surf-chatgpt session current --thread SURF_THREAD
surf-chatgpt session status  SESSION [--retain]
surf-chatgpt session result  SESSION [--wait[=SECONDS]] [--retain]
surf-chatgpt session handoff SESSION
surf-chatgpt session recent  [--thread SURF_THREAD]

surf-chatgpt abandon [SESSION | --thread SURF_THREAD]
surf-chatgpt login
```

`SESSION` accepts either a session ID or an exact canonical `https://chatgpt.com/c/<id>` URL. Output always uses the ID-only public session object.

### Global process contract

- Every non-help invocation writes exactly one compact JSON object followed by `\n` to stdout.
- There is no text-output mode.
- Parser help and usage remain human-readable.
- JSON invocations do not write tracebacks or content-bearing diagnostics to stderr.
- Exit `0` means a valid domain outcome, including `not_ready`, `timed_out`, `stopped`, and an affirmed failed ChatGPT attempt.
- Exit `1` means an operational failure.
- Exit `2` means invalid arguments or empty required input.
- A caught `SIGINT` emits one interruption object and exits `130`.
- A caught `SIGTERM` emits one interruption object and exits `143`.
- `SIGKILL` has no output or exit-code guarantee.
- The process flushes its JSON before running any post-output terminal-page cleanup.

### `ask`

Plain `ask` starts a new ChatGPT session. The prompt is the positional argument or stdin when the positional argument is absent. Whitespace-only input fails with `empty_prompt` and exit `2`. surf-chatgpt does not persist the prompt.

The command performs:

```text
resolve or allocate owned page
→ pre-send readiness and optional picker selection
→ issue send once
→ observe canonical /c/<id> within 30 seconds
→ atomically rebind the exact page to the deterministic session thread
→ return session ID
```

- `--session` resolves or recovers the addressed durable session and submits one follow-up.
- `--thread` is accepted only for an exact bridge-owned pre-session page preserved after login or challenge intervention. It is not a conversation-addressing shortcut.
- `--session` and `--thread` are mutually exclusive.
- `--retain` establishes live-bridge retained-page protection. Without it, generating and human-blocked pages remain protected by their state, while a later affirmed terminal page is eligible for cleanup.
- Picker selection runs before send. `selection` contains only requested dimensions and reports resolved visible labels rather than fuzzy input queries.
- Plain `ask` returns only `ok`, `session`, and optional `selection`, even when the response becomes terminal during the submission handshake.
- Bare `--wait` uses `DEFAULT_OBSERVATION_TIMEOUT_SECONDS = 2700`. `--wait=SECONDS` requires a positive number.
- `ask --wait` completes the same submission handshake first, then follows exactly the `session result --wait` observation path. It is not a second submission implementation.

Plain success:

```json
{"ok":true,"session":{"id":"abc123"}}
```

Success with requested picker dimensions:

```json
{"ok":true,"session":{"id":"abc123"},"selection":{"model":"GPT-5.6","thinking":"Pro"}}
```

### `session current`

`session current --thread THREAD` inspects only the exact page currently bound to `THREAD`.

- It never sends, navigates, creates a page, recovers another page, or focuses.
- A canonical `/c/<id>` returns the ID-only session object.
- A recognized pre-session or human-gate page without an assigned session returns successful `not_ready`.
- A missing thread, out-of-scope page, ownership mismatch, or ambiguous page fails without mutation.

Known session:

```json
{"ok":true,"session":{"id":"abc123"}}
```

ID not assigned yet:

```json
{"ok":true,"session":null,"observation":{"outcome":"not_ready"}}
```

### `session status`

`session status SESSION` resolves the session page and performs one metadata-only latest-attempt classification.

```json
{"ok":true,"session":{"id":"abc123"},"attempt":{"state":"generating"}}
```

For an affirmed failed attempt, status also makes the absence of a canonical result explicit:

```json
{"ok":true,"session":{"id":"abc123"},"attempt":{"state":"failed"},"result":null}
```

When status affirms a terminal state, it captures and emits the status before default cleanup. `--retain` protects the terminal page instead.

### `session result`

Without `--wait`, `session result SESSION` inspects once. With `--wait`, it repeatedly observes until the latest attempt is terminal or the observation deadline expires.

Generating, one-shot:

```json
{"ok":true,"session":{"id":"abc123"},"attempt":{"state":"generating"},"observation":{"outcome":"not_ready"},"result":null}
```

Generating when the wait deadline expires:

```json
{"ok":true,"session":{"id":"abc123"},"attempt":{"state":"generating"},"observation":{"outcome":"timed_out"},"result":null}
```

Completed:

```json
{"ok":true,"session":{"id":"abc123"},"attempt":{"state":"completed"},"result":{"text":"Answer","partial":false}}
```

Stopped:

```json
{"ok":true,"session":{"id":"abc123"},"attempt":{"state":"stopped"},"result":{"text":"Partial answer","partial":true}}
```

Failed:

```json
{"ok":true,"session":{"id":"abc123"},"attempt":{"state":"failed"},"result":null}
```

`completed`, `stopped`, and `failed` finish a wait. A refusal or undesirable but normally completed answer is `completed`. Text remaining beside explicit failure evidence is not returned as a result.

When a terminal result is captured, JSON is emitted and flushed before default cleanup. `--retain` protects the page instead.

### `session handoff`

`session handoff SESSION` resolves or recovers the exact canonical session page, establishes retained-page protection for the current bridge lifetime, and returns instructions for manual inspection. It does not inspect content, alter navigation, or focus.

```json
{"ok":true,"session":{"id":"abc123"},"handoff":{"action":"inspect_browser","thread":"surf-chatgpt-session-abc123"}}
```

The focus command is derived rather than repeated in JSON: `surf-agent --thread '<thread>' focus`. An agent may present it, but only the user may execute it. Handoff protection lasts until explicit abandonment, manual page closure, or bridge restart.

### `abandon`

Top-level `abandon` addresses either a durable session or an explicitly preserved pre-session Surf thread. The two address forms are mutually exclusive. A positional address is always a session ID or canonical URL; a Surf thread always requires `--thread`.

- If the latest attempt is generating, request stop exactly once, affirm `stopped`, then close.
- If the page is at an affirmed non-generating human gate, close directly.
- If the attempt is terminal, close directly.
- If stop, ownership, page scope, or classification cannot be affirmed, fail and preserve the page.
- Abandonment itself is explicit confirmation; there is no interactive prompt, `--yes`, or force path.

```json
{"ok":true,"session":{"id":"abc123"},"attempt":{"state":"stopped"}}
```

A pre-session abandonment returns the addressed thread rather than manufacturing a session:

```json
{"ok":true,"thread":"surf-chatgpt-submit-7f2a"}
```

### `session recent`

`session recent` reads ChatGPT's rendered **Chat history → Chats** section and returns at most the first ten unique canonical conversations in displayed order.

```json
{"ok":true,"sessions":[{"id":"abc123","title":"Visible title"}]}
```

- **Pinned**, **Projects**, archived links, and conversation links outside **Chats** are excluded.
- “Recent” means displayed order at inspection time. No timestamps are inferred.
- An affirmatively identified empty Chats section succeeds with `sessions: []`.
- Missing or ambiguous Chats UI fails with `ui_changed` and returns no candidates.
- Without `--thread`, discovery uses a temporary owned, unfocused page. It emits and flushes the captured candidate list, then closes the page best-effort.
- `--thread` may reuse only the exact discovery page preserved for a human gate.
- Discovery never claims ownership of a candidate, selects it, binds it, or recovers it. The caller explicitly chooses an ID and then uses `status`, `result`, or `handoff`.

### `login`

`login` allocates or reuses an owned pre-session ChatGPT page and returns a manual handoff without focusing it.

```json
{"ok":true,"handoff":{"action":"complete_login","thread":"surf-chatgpt-login"}}
```

The page counts toward retained-page capacity and remains protected until the user completes login and reuses it, abandons it by thread, closes it manually, or restarts the bridge.

## Latest-attempt lifecycle

```text
generating → completed | stopped | failed
```

- `generating`: ChatGPT is actively producing the latest response.
- `completed`: ChatGPT finished normally and a final result is retrievable.
- `stopped`: generation was intentionally stopped and any visible result is explicitly partial.
- `failed`: ChatGPT explicitly reports generation failure and has no canonical result.

`submitted` is an internal handshake phase, not a public state. Failure before a session ID exists is a submission error, not an attempt state.

Observation timeout, login gates, challenges, inaccessible pages, browser failures, and unclassifiable UI are outcomes or operational errors. They never become attempt states. When inspection cannot affirm a state, the error preserves known session identity and omits `attempt` entirely.

## Submission phases and recovery

Use these internal phases for signal handling, bridge-disconnect handling, and safe error projection:

```text
before_send
send_may_have_occurred_id_unknown
id_known_rebind_pending
handshake_complete
observing
```

`SESSION_ASSIGNMENT_TIMEOUT_SECONDS = 30` applies only to observing the first canonical `/c/<id>` after send.

| Phase at interruption or failure | Required behavior |
| --- | --- |
| `before_send` | Return `interrupted` for a signal or the actual operational error. No send occurred; a caller may retry. |
| `send_may_have_occurred_id_unknown` | Lead with `submission_outcome_indeterminate`, preserve the temporary thread, and never claim retry is safe. |
| `id_known_rebind_pending` | Return the interruption or actual phase error with `session.id`; `session_rebind_failed` also carries the preserved temporary thread. Never resend. |
| `handshake_complete` or `observing` | Stop only the caller/observer, return `session.id`, and leave generation and retained-page ownership intact. |

A browser, bridge, transport, or UI failure after send but before known session identity may appear only as the bounded `error.cause` under `submission_outcome_indeterminate`.

Read-only inspection may retry after bridge restart. Pre-send setup may retry only when the bridge affirms that send was not issued. Send and ambiguous post-send work are never replayed.

### Indeterminate submission

```json
{"ok":false,"error":{"type":"submission_outcome_indeterminate","message":"The prompt may have been sent, but no recoverable ChatGPT session ID was observed.","hint":"Complete any browser gate, then inspect the preserved thread with session current. Do not resubmit automatically.","cause":{"type":"bridge_disconnected","phase":"send_may_have_occurred_id_unknown","message":"Browser bridge connection ended."}},"thread":"surf-chatgpt-submit-7f2a"}
```

After user intervention, use `session current --thread THREAD`. If the temporary thread no longer exists, use `session recent` and require explicit candidate selection.

## Human intervention

All login, challenge, and manual-inspection requirements use `human_intervention_required` with one coarse action:

- `complete_login`;
- `complete_challenge`; or
- `inspect_browser`.

Before send, a login or challenge sends nothing and preserves a pre-session thread. The caller may retry the exact prompt through `ask --thread THREAD` only after the user confirms completion. Retry is never automatic.

After send:

- with a known ID, return the error with `session.id` and preserve the exact page;
- with an unknown ID, lead with `submission_outcome_indeterminate`, preserve the temporary thread, include the human handoff, and recover through `session current` after the user acts.

A handoff object contains only its coarse action and owned Surf thread. It contains no repeated command, prompt, result, title, URL, snapshot, page token, or raw browser detail. An agent derives any user-run focus command from the thread.

## Session addressing and deterministic threads

Normalize every accepted session reference into a validated `SessionId` before browser work.

```text
canonical URL:         https://chatgpt.com/c/<id>
deterministic thread:  surf-chatgpt-session-<id>
temporary submission: surf-chatgpt-submit-<random-safe-token>
```

A canonical session URL has HTTPS scheme, host `chatgpt.com`, path exactly `/c/<id>`, and no query or fragment. A session ID is one non-empty URL path segment containing only ASCII letters, digits, `_`, or `-`.

Thread derivation is a pure function. Session commands derive the same thread without reading a local registry.

## Browser-bridge owned-page interface

### Seam

Add one typed internal owned-page interface to surf-agent and one surf-chatgpt adapter over it. Do not construct lifecycle behavior by shelling through generic `open`, `eval`, `list`, and `close` commands: those commands cannot prove ownership, preserve metadata privacy, or serialize classification with mutation.

The interface is internal Python/bridge protocol, not a new user-facing surf-agent command group.

```python
class OwnedPageBridge(Protocol):
    def capabilities(self) -> OwnedPageCapabilities: ...
    def allocate(self, request: AllocateOwnedPage) -> OwnedPageRef: ...
    def resolve(self, request: ResolveOwnedPage) -> OwnedPageRef: ...
    def inspect(self, request: InspectOwnedPage) -> OwnedPageInspection: ...
    def rebind(self, request: RebindOwnedPage) -> OwnedPageRef: ...
    def protect(self, request: ProtectOwnedPage) -> None: ...
    def inspect_and_close(self, request: InspectAndCloseOwnedPage) -> CloseOutcome: ...
    def abandon(self, request: AbandonOwnedPage) -> AbandonOutcome: ...
    def sweep(self, request: SweepOwnedPages) -> SweepOutcome: ...
```

Every mutating request includes:

- owner namespace `surf-chatgpt`;
- source thread and, where known, expected page token;
- expected exact URL or allowed ChatGPT page-scope category;
- expected current protection metadata; and
- a metadata-only classifier/action program whose decoded output is allow-listed.

Every operation runs on the bridge's serialized browser thread. It verifies profile capability, ownership, live page identity, current URL scope, and operation-specific preconditions immediately before mutation.

### Bridge capabilities

surf-chatgpt requires the bridge to affirm all of:

- a dedicated, bridge-owned browser profile/context;
- user-visible-page inventory limited to page token and exact URL;
- non-activating dedicated-window creation;
- owner-tagged page bindings and in-memory protection metadata;
- compare-and-move atomic rebinding;
- serialized inspect/revalidate/close transactions; and
- no automatic adoption, closure, or navigation of unrelated pages.

The Patchright local bridge supplies these capabilities. surf-chatgpt checks the capability contract before browser activity. AXI fails that check with `unsupported_browser_capability`; no weaker generic-operation fallback exists.

### Owned page records

Extend the bridge's live page slot with bridge-memory-only metadata:

```python
@dataclass
class PageSlot:
    page: BrowserPage
    page_token: int
    owner: str | None = None
    protection: Literal["explicitly_retained", "human_intervention"] | None = None
```

`--retain` and successful `session handoff` set `explicitly_retained`. A login, challenge, or manual-inspection gate sets `human_intervention`; an explicit retry through that exact thread clears the gate protection only after readiness is affirmed. Protected handoff pages are not DOM-inspected by sweeps.

Only owner-tagged `surf-chatgpt` slots count toward capacity or participate in surf-chatgpt sweeps. Protection does not persist across bridge restart.

Normal success JSON never exposes page tokens, owner tags, protection fields, profile identity, window IDs, or target IDs.

### Allocation

Before creating an owned page:

1. run one opportunistic surf-chatgpt sweep;
2. count all live owner-tagged surf-chatgpt pages, including temporary submissions, deterministic session pages, discovery pages, login pages, and human-handoff pages;
3. if the count remains at `MAX_RETAINED_PAGES = 10`, fail without mutation;
4. otherwise create a dedicated, unfocused Surf window and tag its binding atomically.

Creation must not reuse, close, or insert a tab into an unrelated window. The Patchright adapter uses a target-specific background window creation path and verifies the created target before binding it.

### Atomic rebind

Rebind accepts source thread, destination deterministic thread, expected page token, and exact canonical URL.

In one bridge request:

1. source must own the expected live page at the exact URL;
2. destination must be absent, or already own that same expected page;
3. if absent, remove source and insert the identical page slot at destination;
4. if destination already owns the expected page, return idempotent success;
5. otherwise fail with both bindings unchanged.

No close, create, navigation, DOM mutation, or focus operation occurs.

### Session resolution after caller or bridge loss

For a known session ID:

1. If the deterministic thread owns a live page at the exact canonical URL, reuse it.
2. If it owns a different page, fail without mutation.
3. If the binding is absent, inspect only user-visible page token and exact URL in the proven Surf context.
4. If exactly one unowned restored page has the exact canonical URL, adopt it under the deterministic thread.
5. If more than one matches, fail as ambiguous without adopting any.
6. If none matches, allocate one dedicated, unfocused page and navigate that new owned page directly to the canonical URL.

Unrelated restored pages are not title-inspected, DOM-inspected, adopted, navigated, focused, or closed. Page-token continuity is not promised across restart.

### Metadata-only inspection programs

Automatic classifiers return only the fields needed by their caller:

```text
session identity or exact canonical-URL match
latest-attempt state
coarse human-gate type
owner/protection metadata
```

The bridge validates the returned object against the operation's allow-list before it crosses the extraction boundary. Unexpected keys, values, or result types fail as `inspection_failed` and preserve the page.

Explicit answer extraction is a separate result-only operation. Recent-session discovery is a separate title-returning operation. Neither implementation is reused by cleanup or capacity inspection.

### Serialized cleanup

`inspect_and_close` and each sweep item perform classification, page-token revalidation, ownership revalidation, allowed-scope revalidation, protection revalidation, terminal-state revalidation, and closure in one serialized bridge transaction.

A sweep is single-pass and non-interactive. Per retained page it performs at most one metadata-only DOM inspection. It never creates, recovers, adopts, navigates, reloads, scrolls, clicks, types, stops generation, emits UI events, waits, or polls.

## Retained-page policy

### Default cleanup

After a targeted command affirmatively captures `completed`, `stopped`, or `failed`:

1. serialize the command result;
2. write and flush it;
3. request best-effort guarded closure.

A close failure does not retract output. The page remains eligible for a later sweep.

A pre-allocation sweep may close terminal, unprotected, owned pages whose terminal state was not previously observed.

There is no grace period and no idle timeout.

### Protected pages

The following survive cleanup and capacity eviction:

- generating pages;
- pages waiting for human intervention;
- pages that are out of allowed scope, stale, changed, inaccessible, or unclassifiable;
- pages protected by `--retain`; and
- pages protected by successful `session handoff`.

A sweep checks handoff/retain protection from bridge metadata and does not inspect that page's DOM.

### Capacity failure

When ten protected retained pages remain, allocation fails without mutation. Diagnostics contain one bounded entry per blocking page:

```json
{"ok":false,"error":{"type":"capacity_exceeded","message":"The browser bridge already owns 10 protected surf-chatgpt pages.","hint":"Resolve or release one listed retained page before allocating another."},"capacity":{"limit":10,"retained":[{"session":{"id":"abc123"},"reason":"generating"},{"thread":"surf-chatgpt-login","reason":"human_intervention"}]}}
```

Allowed reasons are exactly:

- `generating`;
- `human_intervention`;
- `inspection_failed`; and
- `explicitly_retained`.

The applicable instruction is derived from the retained address and reason:

| Retained address | Reason | Derived instruction |
| --- | --- | --- |
| Session | `generating` or `explicitly_retained` | `surf-chatgpt abandon <id>` |
| Session | `human_intervention` or `inspection_failed` | `surf-chatgpt session handoff <id>` |
| Pre-session thread | `generating` or `explicitly_retained` | `surf-chatgpt abandon --thread <thread>` |
| Pre-session thread | `human_intervention` or `inspection_failed` | `surf-agent --thread <thread> focus` — user-run only |

The instruction is not repeated in JSON. Diagnostics contain no titles, URLs, excerpts, timestamps, activity history, detailed gate information, page/window IDs, or unrelated-page metadata.

## Privacy and non-interference

### Profile isolation

surf-chatgpt operates only in a Surf profile/context whose dedicated bridge ownership can be proved. It never attaches to or enumerates the user's normal browser profile. Cookie import conveys selected cookies; it does not grant access to the source profile or its open pages.

An unprovable backend, profile identity, or auto-connect target fails before inspection or mutation.

### Page authority

Mutation authority comes only from:

1. an existing live surf-chatgpt owner binding;
2. explicit recovery of one exact canonical session URL; or
3. an explicitly supplied preserved pre-session Surf thread.

Ownership survives only while the page remains in allowed ChatGPT session, pre-session, or human-gate scope. An owned page navigated outside that scope is not inspected, navigated back, stopped, or closed. It remains protected as `inspection_failed`; the user must inspect or close it manually.

### Focus and windows

surf-chatgpt:

- never invokes focus, activation, or bring-to-front;
- never attempts to restore focus after accidental activation;
- fails before mutation if non-activating creation or recovery cannot be affirmed;
- creates only a dedicated unfocused window when an owned page is required and absent; and
- never raises, repositions, resizes, or covers the active window.

A new window may appear in desktop or taskbar UI. That visibility is not focus permission.

### Error projection

All bridge and browser failures pass through an allow-listed projection. An error has:

```json
{"type":"stable_type","message":"Content-free message","hint":"Optional recovery instruction","cause":{"type":"stable_cause","phase":"operation_phase","message":"Content-free cause"}}
```

`cause` is optional. Raw bridge output, browser exceptions, DOM values, snapshots, command arguments, stack-local values, prompts, responses, titles, and non-public URLs never reach JSON, stderr, logs, traces, or automatic diagnostic artifacts.

Errors include `session` only when the ID is already known and `thread` only when that exact thread is necessary for recovery.

Public `error.type` values are:

| Type | Meaning |
| --- | --- |
| `invalid_args` | The command grammar or option values are invalid. |
| `empty_prompt` | `ask` received no non-whitespace prompt. |
| `unsupported_browser_capability` | The selected backend cannot prove the required owned-page or non-activation guarantees. |
| `browser_identity_unproven` | The bridge cannot prove that it owns the dedicated Surf profile/context. |
| `browser_unavailable` | The proven bridge or owned page cannot be reached before any more specific lifecycle result applies. |
| `capacity_exceeded` | Ten protected surf-chatgpt pages remain after the pre-allocation sweep. |
| `human_intervention_required` | The user must complete login, challenge, or browser inspection. |
| `submission_outcome_indeterminate` | Send may have occurred but no durable session ID is recoverable yet. |
| `session_rebind_failed` | The session ID is known, but guarded exact-page rebinding did not complete. |
| `session_not_found` | The supplied durable session cannot be resolved to the exact canonical ChatGPT page. |
| `thread_not_found` | The supplied preserved Surf thread has no live owned page. |
| `ownership_conflict` | A binding, page token, owner, or exact-URL guard conflicts with the requested operation. |
| `ambiguous_session_page` | More than one restored page exactly matches the canonical session URL. |
| `inspection_failed` | Current allowed metadata cannot affirm page scope, gate, or latest-attempt state. |
| `ui_changed` | An explicit ChatGPT UI contract, including Chats discovery, is missing or ambiguous. |
| `model_unavailable` | A requested model or thinking choice cannot be affirmed before send. |
| `abandonment_failed` | Stop-confirm-close or safe direct closure could not be affirmed. |
| `interrupted` | A caught signal stopped the caller at a phase with a determinate recovery contract. |
| `internal_error` | An unexpected content-free failure survived fail-fast internal handling. |

`submission_outcome_indeterminate` takes precedence over every cause after send while the ID remains unknown. `human_intervention_required` is used after send only when the ID is known; otherwise it is represented as the handoff attached to the indeterminate-submission error. `inspection_failed` takes precedence over `ui_changed` for opportunistic cleanup because the required outcome is page preservation rather than UI diagnosis.

## Module design

### surf-chatgpt

Use this source shape:

```text
surf_chatgpt/
  cli.py                 parser, signal-to-exit mapping, one-object emission
  commands.py            conversion from parsed commands to lifecycle requests
  contracts.py           enums, typed outcomes, and JSON projection
  session_address.py     strict ID/URL parsing and deterministic thread derivation
  session_lifecycle.py   deep orchestration module
  surf_pages.py          adapter to surf-agent's OwnedPageBridge interface
  errors.py              stable safe errors and cause projection
  dom/
    readiness.py         login/challenge/composer metadata classifier
    submission.py        prompt injection, send, and canonical-ID observation
    attempt.py           latest-attempt metadata classifier and explicit result extraction
    recent.py            rendered Chats-section discovery
```

`SessionLifecycle` is the external implementation seam used by command handlers and public-contract tests:

```python
class SessionLifecycle(Protocol):
    def ask(self, request: AskRequest) -> CommandOutcome: ...
    def observe(self, request: ObservationRequest) -> CommandOutcome: ...
    def current(self, request: CurrentSessionRequest) -> CommandOutcome: ...
    def handoff(self, request: HandoffRequest) -> CommandOutcome: ...
    def abandon(self, request: AbandonRequest) -> CommandOutcome: ...
    def recent(self, request: RecentSessionsRequest) -> CommandOutcome: ...
    def login(self, request: LoginRequest) -> CommandOutcome: ...
```

`ObservationRequest` carries mode `status`, `result_once`, or `result_wait`; this keeps status/result/wait on one state-classification implementation. `CommandOutcome` contains the public JSON value plus an optional private post-output cleanup action. `cli.py` emits and flushes first, then invokes that action best-effort.

DOM modules return typed classifications rather than raw dictionaries. Only `attempt.extract_result` may return response text, and only `recent.discover` may return visible titles. Lifecycle and cleanup code cannot call a generic content-bearing snapshot function.

### surf-agent

Add:

```text
surf_agent/
  owned_pages.py                    typed requests, outcomes, guards, allow-list decoding
  backends/
    bridge_common.py                owner/protection PageSlot metadata and shared guards
    patchright/bridge.py            serialized owned-page operations
    local_bridge.py                 typed bridge-client transport
```

The Patchright runtime remains the authority over page tokens, owner bindings, protection, exact-URL inventory, and atomic transactions. The surf-chatgpt package owns ChatGPT DOM semantics and supplies narrow classifier/action programs; surf-agent owns enforcement that only allow-listed metadata leaves those programs and that guarded mutations are serialized.

Tests use an in-memory `OwnedPageBridge` adapter at the same seam. Keep only one lifecycle implementation.

## Implementation sequence

Implement test-first in these dependency-ordered slices:

1. **Backend consolidation** — make Patchright the surf-agent default; remove Camoufox source, configuration, optional dependency, tests, and documentation; retain AXI as the explicit generic alternative.
2. **Contracts and addressing** — parser grammar, session ID normalization, deterministic thread derivation, JSON schemas, error projection, and exit statuses.
3. **Owned-page bridge primitives** — capability gate, owner metadata, non-activating allocation, compare-and-move rebind, exact-URL resolution, protection, guarded close, and sweep.
4. **Submission handshake** — pre-send readiness, single send, 30-second canonical-ID observation, atomic rebind, and phase-aware error outcomes.
5. **Observation** — latest-attempt classifier, one-shot result, waiting, explicit result extraction, and post-output cleanup token.
6. **Human intervention and abandonment** — pre/post-send gates, handoff protection, thread recovery, stop-confirm-close.
7. **Capacity and opportunistic cleanup** — bounded sweep and privacy-safe diagnostics.
8. **Recent discovery and restart recovery** — rendered Chats extraction, explicit selection flow, exact canonical-page adoption/new-page recovery.
9. **Skill documentation** — update Surf backend documentation for the Patchright default and update `skills/surf-chatgpt/SKILL.md` to teach submit-first use, optional waiting, recovery, handoff, and abandonment using the public JSON contract.
10. **Live compatibility gate** — run the serial opt-in flow once against the current ChatGPT UI.

## Acceptance tests

All deterministic rows are required in normal CI. Tests assert observable contracts and stable invariants, not selector spelling, helper call counts, polling counts, or coverage percentage.

### Public CLI contract

Invoke the real CLI entry point against a scripted bridge and assert stdout, exit status, and externally visible bridge/page effects.

Required cases:

- plain `ask` sends once, waits only through rebind, and returns ID-only identity;
- a response completing during handshake does not change plain `ask` output shape;
- `ask --wait` uses the same handshake and observation path;
- `ask --session` submits one follow-up; `ask --thread` accepts only an exact preserved pre-session page;
- session ID and canonical URL input normalize to ID-only output;
- requested picker dimensions report resolved labels only;
- generating/completed/stopped/failed status and result matrix matches this specification;
- status/result/wait are repeatable and non-consuming;
- timeout, browser loss, gates, and ambiguous UI never become attempt states;
- terminal JSON is flushed before guarded cleanup and survives close failure;
- `session current`, handoff, abandon, recent, and login match their exact no-focus contracts;
- allocation eleven sweeps once and then proceeds or fails without mutation;
- all parser, domain, operational, signal, and cleanup paths emit at most one compact JSON object;
- forbidden-content scans cover every normal result, error, cause, handoff, and capacity report.

### Browser-bridge lifecycle

Exercise the real bridge protocol with instrumented pages and focused Patchright tests against local pages.

Required cases:

- backend resolution defaults to Patchright, AXI remains explicitly selectable for generic Surf use, and no Camoufox option, package extra, source path, or documentation remains;
- surf-chatgpt with AXI selected returns `unsupported_browser_capability` before page inventory, creation, or inspection;
- atomic rebind preserves page token, target, URL, DOM, and in-flight activity while changing only registry keys;
- idempotent same-page rebind succeeds;
- stale source token, URL mismatch, occupied destination, and concurrent competing move leave both bindings unchanged;
- separate CLI callers reuse one live page token;
- restart resolution adopts exactly one exact canonical match, creates when none exists, and fails on multiple matches or conflicting binding;
- unrelated restored pages are not DOM-inspected, adopted, navigated, focused, or closed;
- replay occurs only for read-only work or affirmatively pre-send setup;
- inspect/close revalidates token, ownership, scope, protection, and terminal state in one transaction;
- live protection survives callers and ends at bridge restart;
- unsupported backends fail before mutation;
- all automatic creation and recovery remain non-activating and use dedicated windows.

### DOM fixtures

Run production classifiers/extractors in real headless Chromium against small local semantic HTML fixtures.

Required fixtures:

- latest attempt generating, completed, stopped with partial text, and failed beside stale text;
- completed refusal, empty/structured completion, and old turns beside a newer attempt;
- delayed canonical route assignment and no assignment by the handshake deadline;
- logged-in composer, login page, visible challenge, hidden challenge marker, and post-send gate;
- loading skeleton, missing controls, contradictory markers, stale stop controls, state-like text in content/sidebar, out-of-scope navigation, and multiple latest-turn candidates;
- Chats order, duplicates, more than ten rows, Pinned, Projects, unrelated links, empty Chats, missing Chats, and ambiguous multiple Chats sections;
- navigation/page replacement between classification and cleanup;
- canary prompt, response, title, URL, and DOM secrets proving metadata-only boundaries.

### Real interruption and cancellation

Run the CLI as a subprocess against a bridge exposing deterministic barriers at:

```text
before_send
send_may_have_occurred_id_unknown
id_known_rebind_pending
handshake_complete_observing
```

Deliver real `SIGINT` and `SIGTERM` at each barrier. Assert the phase table, one flushed JSON result, exit `130`/`143`, no duplicate send, and retained recoverability. Use bridge disconnects at the same barriers to verify the replay boundary. Use no timing sleeps for test synchronization.

For `SIGKILL`, assert only durable side effects: the bridge remains independent, an issued send is not repeated, and the known session or preserved thread remains recoverable when available.

Test abandonment separately for active generation, terminal attempts, non-generating human gates, failed stop/classification, and unrelated pages.

### Minimal live ChatGPT gate

Run serially behind an explicit `live_chatgpt` opt-in with a dedicated Surf profile/account and one disposable nonce conversation. Do not retain screenshots, DOM, prompts, responses, or raw browser errors as artifacts.

One flow proves:

1. retained plain `ask` returns a durable ID after one send and exact-page rebind;
2. a second short-lived command reaches the same live page; status, waiting result, and repeated result are non-consuming;
3. `session recent` includes the ID among the first ten Chats candidates without selecting it;
4. after bridge restart, status or result recovers the same session under the deterministic thread while leaving an unrelated Surf page untouched;
5. a clean logged-out profile detects login before send, preserves an unfocused handoff page, and never focuses automatically; and
6. teardown explicitly abandons the disposable session.

A live failure is UI-compatibility drift to diagnose. It is not permission to weaken fail-closed deterministic behavior.

## Definition of done

The implementation is accepted when:

- every deterministic acceptance row passes in normal CI;
- the opt-in live gate passes once against the current ChatGPT UI;
- command documentation matches the exact public schemas and recovery guidance;
- Patchright is Surf's default, AXI remains an explicit generic alternative, and Camoufox is absent;
- no alternate lifecycle implementation or persistent session/run registry remains; and
- proactive `login` prepares an unfocused retained pre-session page without requiring a prompt.
