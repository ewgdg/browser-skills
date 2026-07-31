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

`surf-chatgpt` connects to Surf's Patchright bridge and composes its generic
thread-addressed browser operations. AXI remains available for generic Surf browser
work. Camoufox is not supported.

## Model and thinking selection

Use `--model` and `--thinking` with `ask` to select the two picker dimensions
independently before submission. `--model` searches the nested model rows;
`--thinking` searches the top-level thinking modes. Queries match visible labels
without depending on capitalization or punctuation.

```bash
surf-chatgpt ask --thinking pro 'Review this design.'
surf-chatgpt ask --model '5.6 sol' 'Review this design.'
surf-chatgpt ask --model '5.6 sol' --thinking pro 'Review this design.'
```

Selection is fail-closed. If a requested choice cannot be found and affirmed as
selected, `ask` returns `model_unavailable` without sending the prompt. A successful
submission reports the resolved visible labels:

```json
{"ok":true,"session":{"id":"abc123"},"selection":{"model":"GPT-5.6 Sol","thinking":"Pro"}}
```

## Resumable workflow

Use this sequence. Do not keep a caller blocked unless waiting is useful.

1. Submit once with plain `ask` and save `session.id`.
2. Optionally wait during submission with `ask --wait`.
3. Otherwise inspect later with `session status` or retrieve with `session result`.
4. Send follow-ups with `ask --session ID`; never reconstruct a conversation from a Surf thread.
5. If identity is lost, try `session current` on the preserved thread, then `session recent` and explicitly choose one candidate.
6. Use `session handoff` only when the user must inspect the browser.
7. Explicitly `abandon` open or active pages when they are no longer needed.

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
response uses `partial:true`; failed and rate-limited responses have a null result.
Only explicit `session result` commands extract response text. Status and terminal
cleanup remain metadata-only.

An explicit visible ChatGPT request limit before send is an operational failure:

```json
{"ok":false,"error":{"type":"rate_limited","message":"ChatGPT is rate limiting requests.","hint":"Wait for the account limit to reset before submitting a new prompt."}}
```

If the limit appears after send may have occurred but before a durable session ID is
known, the outcome remains `submission_outcome_indeterminate` with a `rate_limited`
cause and preserved thread. Never retry that prompt automatically. Once a session is
known, status and result report `{"attempt":{"state":"rate_limited"},"result":null}`.

After terminal JSON is written and flushed, the page closes through best-effort
cleanup. Use `--retain` when the terminal page must remain open.

## Follow up and recover

Address follow-ups by durable session identity:

```bash
surf-chatgpt ask --session abc123 'Check one more constraint.'
```

```json
{"ok":true,"session":{"id":"abc123"}}
```

Separate callers may reuse the same ID. The deterministic session thread is reused
while live; otherwise `surf-chatgpt` opens its canonical session URL in that thread.

`thread` is the live bridge address of one browser page. It is not durable ChatGPT
conversation identity. Use `--thread` only to continue on a page that
`surf-chatgpt` returned after login, challenge, or an indeterminate submission.

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

For manual inspection of a durable session, ensure its page and request a handoff:

```bash
surf-chatgpt session handoff abc123
```

```json
{"ok":true,"session":{"id":"abc123"},"handoff":{"action":"inspect_browser","thread":"surf-chatgpt-session-abc123"}}
```

Handoff does not focus or inspect conversation content. It returns the live thread so
the user can choose whether to focus or inspect that page.

Use proactive login when needed:

```bash
surf-chatgpt login
```

```json
{"ok":true,"handoff":{"action":"complete_login","thread":"surf-chatgpt-login"}}
```

`login` creates or reuses an unfocused page. Wait for the user to complete
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

Generating and human-blocked pages remain open. `--retain` leaves a terminal page
open after observation. Close it with explicit abandonment when it is no longer
needed:

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
It requests stop once, waits for generation to end, then closes the addressed thread.
Terminal and non-generating pages close directly. If classification, stop, or closure
fails, abandonment reports `abandonment_failed`. Age, inactivity, observation timeout,
caller exit, and process death never authorize abandonment.

There is no automatic page sweep or surf-chatgpt page capacity. The caller is
responsible for abandoning pages it deliberately keeps open.
