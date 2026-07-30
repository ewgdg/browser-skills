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

A timeout is a successful observation outcome; it does not stop generation:

```json
{"ok":true,"session":{"id":"abc123"},"attempt":{"state":"generating"},"observation":{"outcome":"timed_out"},"result":null}
```

Use status for metadata-only classification and result for explicit response retrieval:

```bash
surf-chatgpt session status abc123
surf-chatgpt session result abc123
```

## Follow up and recover

Address follow-ups by durable session identity:

```bash
surf-chatgpt ask --session abc123 'Check one more constraint.'
```

Separate callers may reuse the same ID. A live exact deterministic binding is reused;
after a browser-bridge restart, Surf adopts one restored exact-URL page or creates a
dedicated unfocused page at that canonical session URL. Ambiguous exact matches fail
without adopting or changing any page.

`--thread` is only for an exact preserved pre-session page returned after login or challenge intervention. It is not a conversation address.

Use `session current --thread THREAD` to discover whether a preserved pre-session page has acquired a durable session ID. Use `session recent` only when session metadata is lost; explicitly select a returned candidate before running another session command.

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

## Retention and abandonment

Generating and human-blocked pages remain protected. `--retain` explicitly protects a page after terminal observation. Release it only through explicit abandonment:

```bash
surf-chatgpt abandon abc123
surf-chatgpt abandon --thread surf-chatgpt-login
```

Abandonment is the only automatic path allowed to stop an active response attempt.
