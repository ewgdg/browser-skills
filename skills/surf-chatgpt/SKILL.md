---
name: surf-chatgpt
description: Consult logged-in web ChatGPT/Pro through surf-agent and return compact bounded advice to the local agent.
---

# surf-chatgpt

Use when user explicitly wants external web ChatGPT/Pro input: second opinion, critique, plan review, or comparison with local reasoning.

Do **not** use when local reasoning is enough. Browser automation is slower and can fail on login/UI/CAPTCHA.

## Safety rules

- Never send secrets, credentials, API keys, tokens, cookies, SSH keys, or private user data.
- Include only relevant snippets. Do not dump whole repos, huge logs, or browser output.
- Treat result as external advice, not authority. Local agent remains responsible.
- Label local results as **external ChatGPT via surf-agent** when reporting to user.
- Prompt sent upstream is exactly the positional prompt argument, or stdin when no prompt argument is given; no hidden tooling/agent handoff text is prepended.

## Prerequisites

```bash
uv tool install \
  --with "surf-agent @ git+https://github.com/ewgdg/browser-skills.git#subdirectory=packages/surf-agent" \
  "surf-chatgpt @ git+https://github.com/ewgdg/browser-skills.git#subdirectory=packages/surf-chatgpt"
```

`surf-chatgpt` depends on `surf-agent` and imports its CLI directly. No separate `surf-agent` executable lookup needed for normal use.

Also required:

- surf-agent browser backend configured and able to open pages.
- Logged in to `chatgpt.com` in the surf-agent browser profile. Use the surf skill/profile command, for example: `surf-agent profile open https://chatgpt.com/`.

## Commands

```bash
surf-chatgpt ask 'Question...'
printf 'Question...' | surf-chatgpt ask
printf 'Critique this plan: ...' | surf-chatgpt ask --format text
surf-chatgpt ask --thinking high 'Question...'
surf-chatgpt ask --session '<session-id>' --model gpt-5.5 --thinking medium 'Follow up...'
surf-chatgpt ask --thread '<thread-id>' 'Follow up in kept browser thread...'
surf-chatgpt --help
```

Use stdin/heredoc for long prompts, multiline context, or sensitive text. Positional prompts are shell-visible/history-prone.

Default output is compact JSON:

```json
{"ok":true,"source":"external-chatgpt-via-surf-agent","session":{"policy":"ephemeral"},"answer":"..."}
```

Errors are structured and nonzero:

```json
{"ok":false,"source":"external-chatgpt-via-surf-agent","error":{"type":"login_required","message":"ChatGPT login required","hint":"Open the surf-agent profile and log in to chatgpt.com, then retry."}}
```

## Model / thinking selection

`--model` is a fuzzy query against models visible in ChatGPT's web model picker. No silent fallback: if no usable match is found, command fails with `model_unavailable`.

```bash
surf-chatgpt ask --thinking high 'Question...'
surf-chatgpt ask --model pro 'Question...'
surf-chatgpt ask --model gpt-5.5 'Question...'
surf-chatgpt ask --model gpt-5.5:high 'Question...'
```

Thinking mapping: `low` -> `Instant`, `medium` -> `Medium`, `high` -> `High`.

## Session policy

### Default: ephemeral one-shot

`ask` defaults to an ephemeral surf-agent thread. It creates a temporary ChatGPT thread, optionally selects model/thinking, sends the prompt argument or stdin, extracts response, returns compact output, then closes the thread. If ChatGPT rewrites to `https://chatgpt.com/c/<id>` before cleanup, returned `session` includes id/url for follow-up.

### Explicit continuity

Use returned ChatGPT session id/url for conversation continuity, or returned surf-agent `thread` for browser-thread continuity.

```bash
surf-chatgpt ask --new 'first prompt'
surf-chatgpt ask --keep-open 'first prompt'
surf-chatgpt ask --new --keep-open 'first prompt'
surf-chatgpt ask --session '<session-id>' 'follow up'
surf-chatgpt ask --session 'https://chatgpt.com/c/<session-id>' 'follow up by URL'
surf-chatgpt ask --thread '<thread-id>' 'follow up in kept thread'
surf-chatgpt ask --current 'follow up in default thread'
```
`--new` and `--session` create a surf-agent thread and close it by default. Add `--keep-open` to leave it open; `--keep-open` alone implies `--new`. JSON includes `session.thread` / `session.thread_id`, reusable with `--thread`. `--current` targets surf-agent thread `main`.

## Web session discovery

`session` commands inspect ChatGPT through surf-agent threads. They do not maintain local aliases or local session files.

```bash
surf-chatgpt session current --thread '<thread-id>'
surf-chatgpt session search "rust async" --limit 10
surf-chatgpt session search "plan review" --format text
```

`session current` evaluates `location.href` in the selected surf-agent thread and returns the conversation id/url/title when URL is `https://chatgpt.com/c/<id>`. Otherwise it returns `ok: true` with `session: null` and warning.

`session search QUERY` creates a temporary surf-agent thread, opens ChatGPT, uses ChatGPT web Search chats UI, extracts only links matching `https://chatgpt.com/c/<id>`, then closes the thread. Experimental: ChatGPT search DOM can change.

Search output shape:

```json
{"ok":true,"source":"external-chatgpt-via-surf-agent","query":"rust async","sessions":[{"id":"abc","url":"https://chatgpt.com/c/abc","title":"Rust async notes"}]}
```

Failure classes include `login_required`, `captcha_or_cloudflare`, `ui_changed`, `timeout`, `surf_unavailable`, `browser_unavailable`, `model_unavailable`, `parse_error`, and `invalid_args`.

## Validation checklist

```bash
surf-chatgpt --help
surf-chatgpt ask --format json < /dev/null; test $? -ne 0
surf-chatgpt ask --help | grep -q -- 'prompt'
surf-chatgpt ask --help | grep -q -- '--session' && surf-chatgpt ask --help | grep -q -- '--thread' && ! surf-chatgpt ask --help | grep -q -- '--window-id'
surf-chatgpt session search --help | grep -q -- '--limit'
```

Optional live smoke only when user permits browser ChatGPT use:

```bash
surf-chatgpt ask --ephemeral 'Reply with one word: ok'
```
