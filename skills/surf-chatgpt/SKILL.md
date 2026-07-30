---
name: surf-chatgpt
description: Consult logged-in web ChatGPT through resumable Surf sessions and return compact JSON to the local agent.
---

# surf-chatgpt

Use when the user explicitly wants external web ChatGPT input: a second opinion, critique, plan review, or comparison with local reasoning.

Do not use when local reasoning is enough. Browser work is slower and may require the user to complete login or a challenge.

## Safety

- Never send secrets, credentials, tokens, cookies, private user data, or irrelevant repository content.
- Send only the prompt argument or stdin content the user authorized.
- Treat the response as external advice. The local agent remains responsible for verification and judgment.
- Never focus or activate a browser page. Only the user may run a derived focus command.
- Never automatically retry a prompt after send may have occurred.
- Keep session IDs in agent state when later observation or follow-up is required.

## Commands

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

`SESSION` is either an ID containing ASCII letters, digits, `_`, or `-`, or an exact `https://chatgpt.com/c/<id>` URL. Output identifies a session only as `{"id":"<id>"}`.

Every non-help invocation emits one compact JSON object. Parse failures and empty prompts exit `2`; operational failures exit `1`; valid domain outcomes exit `0`.

Patchright is Surf's default backend and the only backend supported by
`surf-chatgpt`. AXI is available only for generic Surf browser work and is rejected
by `surf-chatgpt` before browser activity. Camoufox is not supported.

## Resumable workflow

Use this sequence. Do not keep a caller blocked unless waiting is useful.

1. Submit once with plain `ask` and save `session.id`.
2. Optionally wait during submission with `ask --wait`.
3. Otherwise inspect later with `session status` or retrieve with `session result`.
4. Send follow-ups with `ask --session ID`; never reconstruct a conversation from a Surf thread.
5. If identity is lost, try `session current` on the preserved thread, then `session recent` and explicitly choose one candidate.
6. Use `session handoff` only when the user must inspect the browser.
7. Explicitly `abandon` retained or active pages when they are no longer needed.

## Submit and observe

Plain `ask` submits once and returns after ChatGPT assigns durable session identity:

```bash
surf-chatgpt ask 'Review this design.'
```

```json
{"ok":true,"session":{"id":"abc123"}}
```

Use stdin for multiline prompts. The positional prompt takes precedence when both are present.

Bare `--wait` uses the default observation deadline. `--wait=SECONDS` requires a positive number and observes through the same result path used later:

```bash
surf-chatgpt ask --wait 'Review this design.'
surf-chatgpt session result abc123 --wait=300
```

A completed `ask --wait` returns the assigned session and result together:

```json
{"ok":true,"session":{"id":"abc123"},"attempt":{"state":"completed"},"result":{"text":"Answer","partial":false}}
```

A timeout is a successful observation outcome; it does not stop generation:

```json
{"ok":true,"session":{"id":"abc123"},"attempt":{"state":"generating"},"observation":{"outcome":"timed_out"},"result":null}
```

Use status for metadata-only classification and result for explicit response retrieval:

```bash
surf-chatgpt session status abc123
surf-chatgpt session result abc123
```

```json
{"ok":true,"session":{"id":"abc123"},"attempt":{"state":"generating"}}
```

```json
{"ok":true,"session":{"id":"abc123"},"attempt":{"state":"completed"},"result":{"text":"Answer","partial":false}}
```

Observation is read-only, repeatable, and non-consuming. A one-shot result while
generation is active returns `not_ready`; waiting returns `timed_out` only when its
observer deadline expires. Neither outcome stops or changes the response attempt.

Completed results use `{"text":"...","partial":false}`. An explicitly stopped
response uses `partial:true`; a failed response has a null result. Only explicit
`session result` commands extract response text. Status and terminal cleanup remain
metadata-only.

After terminal JSON is written and flushed, the unprotected page closes through a
guarded best-effort cleanup. Use `--retain` when the terminal page must remain open.

## Follow up and recover

Address follow-ups by durable session identity:

```bash
surf-chatgpt ask --session abc123 'Check one more constraint.'
```

```json
{"ok":true,"session":{"id":"abc123"}}
```

Separate callers may reuse the same ID. A live exact deterministic binding is reused;
after a browser-bridge restart, Surf adopts one restored exact-URL page or creates a
dedicated unfocused page at that canonical session URL. Ambiguous exact matches fail
without adopting or changing any page.

`--thread` is only for an exact preserved pre-session page returned after login or challenge intervention. It is not a conversation address.

Use `session current --thread THREAD` to discover whether a preserved pre-session page has acquired a durable session ID:

```bash
surf-chatgpt session current --thread surf-chatgpt-submit-safe123
```

```json
{"ok":true,"session":{"id":"abc123"}}
```

If no ID is assigned yet, the exact output is:

```json
{"ok":true,"session":null,"observation":{"outcome":"not_ready"}}
```

Use `session recent` only when session metadata is lost:

```bash
surf-chatgpt session recent
```

```json
{"ok":true,"sessions":[{"id":"abc123","title":"Visible title"}]}
```

Discovery reads only the rendered Chat history → Chats section. It returns at most
ten unique canonical conversations in displayed order. Pinned, Projects, archived,
duplicate, and out-of-section links are excluded. An affirmed empty Chats section
returns `{"ok":true,"sessions":[]}`. Missing or ambiguous Chats UI fails without
candidates:

```json
{"ok":false,"error":{"type":"ui_changed","message":"The required ChatGPT interface could not be identified.","hint":"Update surf-chatgpt for the current ChatGPT interface before retrying."}}
```

Discovery never selects, claims, binds, opens, or recovers a candidate. Explicitly
choose an ID, then run `session status`, `session result`, or `session handoff`.

## Human intervention

Login, challenge, and manual inspection outcomes return a coarse handoff action and preserved thread:

```json
{"ok":false,"error":{"type":"human_intervention_required","message":"The browser requires user intervention.","hint":"Complete the requested browser action manually before retrying."},"handoff":{"action":"complete_login","thread":"surf-chatgpt-login"}}
```

Tell the user what action is required and wait for confirmation. Do not focus, resend, or continue automatically. If useful, show this command for the user to run themselves:

```bash
surf-agent --thread '<thread>' focus
```

For manual inspection of a durable session, establish live retained protection first:

```bash
surf-chatgpt session handoff abc123
```

```json
{"ok":true,"session":{"id":"abc123"},"handoff":{"action":"inspect_browser","thread":"surf-chatgpt-session-abc123"}}
```

Handoff does not focus or inspect conversation content. Its protection lasts only for
the current bridge lifetime and ends on explicit abandonment, manual page closure, or
bridge restart.

Use proactive login when needed:

```bash
surf-chatgpt login
```

```json
{"ok":true,"handoff":{"action":"complete_login","thread":"surf-chatgpt-login"}}
```

`login` creates or reuses an unfocused protected page. Wait for the user to complete
the action. For a discovery login or challenge gate, retry only the exact returned
discovery thread after the user confirms completion:

```json
{"ok":false,"error":{"type":"human_intervention_required","message":"The browser requires user intervention.","hint":"Complete the requested browser action manually before retrying."},"handoff":{"action":"complete_login","thread":"surf-chatgpt-discovery-safe123"}}
```

```bash
surf-chatgpt session recent --thread surf-chatgpt-discovery-safe123
```

Do not retry that thread automatically. A successful retry closes the discovery page
only after its JSON has been flushed.

## Retention and abandonment

Generating and human-blocked pages remain protected. `--retain` explicitly protects
a page during terminal observation. Release it only through explicit abandonment:

```bash
surf-chatgpt abandon abc123
surf-chatgpt abandon --thread surf-chatgpt-login
```

```json
{"ok":true,"session":{"id":"abc123"},"attempt":{"state":"stopped"}}
```

```json
{"ok":true,"thread":"surf-chatgpt-login"}
```

Abandonment is the only automatic path allowed to stop an active response attempt.
It requests stop exactly once, affirms `stopped`, then closes. Terminal attempts and
affirmed non-generating login or challenge pages close directly. If ownership, page
scope, classification, stop, or closure cannot be affirmed, abandonment fails and
preserves the page. Age, inactivity, observation timeout, caller exit, and process
death never authorize abandonment.

Before each owned-page allocation, the live bridge performs one metadata-only sweep
of surf-chatgpt pages. Explicitly retained and human-protected pages are not DOM
inspected. Other pages are inspected at most once and close only when terminal state,
ownership, scope, protection, and identity are all affirmed. Sweeps never navigate,
recover, poll, stop generation, or emit browser UI events.

The bridge retains at most ten surf-chatgpt-owned pages. If ten protected pages remain,
allocation fails with `capacity_exceeded` and a bounded `capacity.retained` list. Each
entry contains only a session ID or necessary pre-session thread plus one reason:
`generating`, `human_intervention`, `inspection_failed`, or `explicitly_retained`.
Resolve the blocker or explicitly abandon one listed page before allocating another.
