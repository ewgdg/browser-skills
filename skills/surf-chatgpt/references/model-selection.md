# Model picker inspection

Use `model select` to exercise ChatGPT's web picker without injecting or sending a prompt:

```bash
surf-chatgpt model select --thinking pro
surf-chatgpt model select --model gpt-5.6-sol --format text
surf-chatgpt model select --model gpt-5.6-sol --thinking pro
```

`--model` searches only the nested actual-model rows. `--thinking` searches the top-level thinking modes; `Pro` belongs to `--thinking`. The command independently reads the checked picker state and fails if it disagrees with the requested selection. It leaves the dedicated browser window open in the background without raising or focusing it. JSON and text output include the reusable Surf thread id.

Inspect the window manually, or address the thread explicitly:

```bash
surf-agent --thread '<returned-thread>' focus
surf-chatgpt model select --thread '<returned-thread>' --thinking pro
surf-agent --thread '<returned-thread>' close
```

Only run `focus` when the user explicitly wants the window raised. Close the thread explicitly after inspection.

For a live no-prompt smoke test:

```bash
surf-chatgpt model select --thinking pro
```
