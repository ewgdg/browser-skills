# Model picker inspection

Use `model select` to exercise ChatGPT's web picker without injecting or sending a prompt:

```bash
surf-chatgpt model select --thinking pro
surf-chatgpt model select --model gpt-5.6-sol --format text
surf-chatgpt model select --model gpt-5.6-sol --thinking pro
```

`--model` searches only the nested actual-model rows. `--thinking` searches the top-level thinking modes; `Pro` belongs to `--thinking`. The command independently reads the checked picker state and fails if it disagrees with the requested selection. It leaves the dedicated browser window open and returns the reusable Surf thread id in JSON and text output.

Continue with the returned thread, then close it after inspection:

```bash
surf-chatgpt model select --thread '<returned-thread>' --thinking pro
surf-agent --thread '<returned-thread>' close
```

For a live no-prompt smoke test:

```bash
surf-chatgpt model select --thinking pro
```
