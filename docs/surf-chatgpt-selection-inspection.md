# Surf ChatGPT selection inspection

Use the selection diagnostic to verify ChatGPT's live model and thinking picker
without injecting or sending a prompt:

```bash
surf-chatgpt selection inspect --model '5.6 sol'
surf-chatgpt selection inspect --thinking pro
surf-chatgpt selection inspect --model '5.6 sol' --thinking pro
```

At least one picker dimension is required. `--model` searches nested model rows;
`--thinking` searches top-level thinking modes. Selection uses the same readiness and
picker logic as `ask`, and succeeds only when every requested choice is visibly
affirmed as selected.

Success returns the visible labels selected by ChatGPT:

```json
{"ok":true,"selection":{"model":"GPT-5.6 Sol","thinking":"Pro"}}
```

The command never fills the composer, clicks send, or creates a conversation. Its
temporary browser page closes only after terminal JSON is flushed.

## Manual inspection

Use `--retain` to keep the diagnostic page open. The result includes the exact browser
thread:

```bash
surf-chatgpt selection inspect --thinking pro --retain
```

```json
{"ok":true,"selection":{"thinking":"Pro"},"thread":"surf-chatgpt-selection-..."}
```

Inspect or focus that thread manually with Surf, then release it explicitly:

```bash
surf-agent --thread 'surf-chatgpt-selection-...' focus
surf-chatgpt abandon --thread 'surf-chatgpt-selection-...'
```

`surf-chatgpt` itself never focuses the page.

## Human gates and failures

Login and challenge gates preserve the exact diagnostic page and return a handoff.
After the user completes the requested action, retry the same selection on that exact
thread:

```bash
surf-chatgpt selection inspect \
  --thread 'surf-chatgpt-selection-...' \
  --model '5.6 sol' \
  --thinking pro
```

Do not retry automatically. `--thread` accepts only a live thread created by selection
inspection and never navigates it.

Other diagnostic failures return the normal safe error types:

- `model_unavailable` when a requested visible choice cannot be selected and affirmed.
- `ui_changed` when the required picker structure cannot be identified.
- `rate_limited` when ChatGPT displays a request limit before inspection.
- `inspection_failed` when returned browser metadata is incomplete or contradictory.

Without `--retain`, non-human-gate failures close the diagnostic page after JSON is
flushed. With `--retain`, the failure includes the open thread for manual inspection.

## Live compatibility gate

The opt-in live compatibility gate selects the first available model and thinking
rows through this command, verifies that the active desktop window does not change,
and confirms an unrelated Surf page remains alive:

```bash
uv run pytest -q packages/surf-chatgpt/tests/test_live_chatgpt.py \
  --live-chatgpt \
  --live-chatgpt-profile /path/to/dedicated-profile
```

The live gate uses a dedicated authenticated Surf profile and also submits one
disposable prompt for the broader resumable-session compatibility checks.
