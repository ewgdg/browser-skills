---
name: surf-chatgpt
description: Consult logged-in web ChatGPT/Pro through surf and return compact bounded advice to the local agent.
---

# surf-chatgpt

Use this skill when user explicitly wants external web ChatGPT/Pro input:

- second opinion on reasoning
- critique or red-team of a plan
- plan review before implementation
- compare local agent reasoning with web ChatGPT

Do **not** use when local reasoning is enough. Browser automation is slower and can fail on login/UI/CAPTCHA.

## Safety rules

- Never send secrets, private credentials, API keys, tokens, cookies, SSH keys, or private user data.
- Include only relevant snippets. Do not dump whole repos, huge logs, or browser output.
- Treat result as external advice, not authority. Local agent remains responsible.
- Label local results as **external ChatGPT via surf** when reporting to user.
- Do not expose caller/tooling identity in prompts sent upstream. Upstream prompts should read like normal user requests, not automation or agent handoff messages.
- Prefer small, narrow prompts. Explicit context beats hidden browser/session context.

## Install/layout

Runtime skill payload is self-contained under this directory. No global helper, Agentify, MCP, or scattered state.

```text
surf-chatgpt/
  SKILL.md
  pyproject.toml
  uv.lock
  src/
    surf_chatgpt/
```

Tests live in the repository-level `tests/surf-chatgpt/` directory and are not part of the shipped skill payload.

## Commands

Run from this skill directory:

```bash
printf 'Question...' | uv run surf-chatgpt ask
printf 'Plan...' | uv run surf-chatgpt ask --mode critique --max-chars 3000
printf 'Plan...' | uv run surf-chatgpt ask --mode redteam --format text
printf 'Question...' | uv run surf-chatgpt ask --thinking high
printf 'Follow up...' | uv run surf-chatgpt ask --session '<session-id>' --model gpt5.5:medium
uv run surf-chatgpt --help
uv run -m surf_chatgpt --help
```

Default output is compact JSON:

```json
{"ok":true,"source":"external-chatgpt-via-surf","mode":"critique","session":{"policy":"ephemeral"},"answer":"..."}
```

The local `source` label is for the caller/user only. The prompt sent to web ChatGPT does **not** include this label or mention surf, pi, local agents, browser automation, or bridge tooling.

Errors are structured and nonzero:

```json
{"ok":false,"source":"external-chatgpt-via-surf","error":{"type":"login_required","message":"ChatGPT login required","hint":"Open Chrome and log in to chatgpt.com, then retry."}}
```

## Modes

- `answer`: direct answer, minimal caveats.
- `critique`: flaws, missing constraints, simpler alternatives, highest-value fixes.
- `redteam`: failure modes, abuse cases, brittle assumptions.
- `plan-review`: sequencing, hidden decisions, testability, scope creep, stop rules.

## Model / thinking selection

GPT-5.5 thinking is a secondary web UI menu, not the same thing as surf's legacy `pro` model token. The controlled browser path supports it in ephemeral and persistent session modes:

```bash
printf 'Question...' | uv run surf-chatgpt ask --thinking high
printf 'Question...' | uv run surf-chatgpt ask --model gpt5.5:medium
printf 'Follow up...' | uv run surf-chatgpt ask --session '<session-id>' --thinking medium
printf 'Question...' | uv run surf-chatgpt ask --current --model gpt5.5:high
```

Mapping used by this skill:

- `low` -> click ChatGPT `Instant`
- `medium` -> click ChatGPT `Medium`
- `high` -> click ChatGPT `High`

Convenience forms are accepted for the GPT-5.5 submenu:

```bash
uv run surf-chatgpt ask --session '<session-id>' --model gpt5.5:low
uv run surf-chatgpt ask --session '<session-id>' --model gpt5.5:medium
uv run surf-chatgpt ask --session '<session-id>' --model gpt5.5:high
```

Top-level model tokens such as `--model instant`, `--model thinking`, or `--model pro` are rejected in this skill because the controlled browser path does not implement the top-level model picker. Use `--thinking low|medium|high` or `--model gpt5.5:<level>` instead. No silent fallback: if the requested level is missing from your subscription/UI, the command fails with `model_unavailable`.

## Session policy

### Default: ephemeral one-shot

`ask` defaults to ephemeral mode using this skill's controlled lower-level surf tab/JS browser path. It opens a temporary unfocused ChatGPT window, optionally selects `--thinking`, sends the prompt, extracts the assistant response, returns compact output, then closes the temporary window. It reuses Chrome login cookies but does not intentionally reuse a ChatGPT conversation. If ChatGPT rewrites the page to `https://chatgpt.com/c/<id>` before cleanup, the returned `session` includes that id/url for optional follow-up.

### Explicit browser sessions

Core `ask` flow does not need local session state. Use returned ChatGPT session id/url directly.

```bash
printf 'first prompt' | uv run surf-chatgpt ask --new
printf 'follow up' | uv run surf-chatgpt ask --session '<session-id>'
printf 'follow up with high thinking' | uv run surf-chatgpt ask --session '<session-id>' --thinking high
printf 'follow up by URL' | uv run surf-chatgpt ask --session 'https://chatgpt.com/c/<session-id>'
printf 'follow up in active ChatGPT tab' | uv run surf-chatgpt ask --current
```

`--new` opens `https://chatgpt.com/`, sends the prompt, leaves the browser session open, and returns `session.id` plus `session.url`. `--session ID_OR_URL` opens `https://chatgpt.com/c/<ID>` or the provided ChatGPT URL and continues there. URL reuse can carry prior conversation context, so continuity is explicit. Prefer default ephemeral mode for clean one-shot consults.

## Web session discovery

`session` commands inspect real ChatGPT browser state. They do not maintain local aliases or local session files.

```bash
uv run surf-chatgpt session current
uv run surf-chatgpt session search "rust async" --limit 10
uv run surf-chatgpt session search "plan review" --format text
```

`session current` reads `surf tab.list --json` and returns the active ChatGPT conversation id/url/title when the active tab is `https://chatgpt.com/c/<id>`. If the active ChatGPT tab is home/settings/etc., or no active ChatGPT tab exists, it returns `ok: true` with `session: null` and a warning.

`session search QUERY` opens a temporary unfocused ChatGPT window, uses ChatGPT web site's own Search chats UI, extracts only result links matching `https://chatgpt.com/c/<id>`, then closes the temporary window. It does not use browser history or local state. This is experimental because ChatGPT search DOM can change.

Search output shape:

```json
{"ok":true,"source":"external-chatgpt-via-surf","query":"rust async","sessions":[{"id":"abc","url":"https://chatgpt.com/c/abc","title":"Rust async notes"}]}
```

Planned but not implemented: `session recent` using ChatGPT web UI/sidebar. Do not claim it is available.

## Output budget

- `--max-chars N` clamps returned answer. Default: 6000.
- `--max-words N` optionally clamps by words before chars.
- UI noise like `Copy`, `Good response`, and `Regenerate` is stripped.
- Code fences are preserved during whitespace cleanup when possible.

## Prerequisites

- `surf` installed and on `PATH`.
- Chrome running with surf browser bridge/extension available.
- Logged in to `chatgpt.com` in Chrome.

Failure classes include `login_required`, `captcha_or_cloudflare`, `ui_changed`, `timeout`, `surf_unavailable`, `browser_unavailable`, `model_unavailable`, `parse_error`, and `invalid_args`.

## Validation checklist

Non-browser checks:

```bash
uv run surf-chatgpt --help
uv run -m surf_chatgpt --help
uv run python -m unittest discover -s ../../tests/surf-chatgpt
uv run surf-chatgpt ask --format json < /dev/null; test $? -ne 0
uv run surf-chatgpt ask --help | grep -q -- '--session' && ! uv run surf-chatgpt ask --help | grep -q -- '--thread'
uv run surf-chatgpt session search --help | grep -q -- '--limit'
uv run surf-chatgpt session --help | grep -q 'current' && uv run surf-chatgpt session --help | grep -q 'search' && ! uv run surf-chatgpt session --help | grep -E 'bind|forget|list'
```

Optional live smoke only when user permits browser ChatGPT use:

```bash
printf 'Reply with one word: ok' | uv run surf-chatgpt ask --ephemeral --max-chars 1000
```
