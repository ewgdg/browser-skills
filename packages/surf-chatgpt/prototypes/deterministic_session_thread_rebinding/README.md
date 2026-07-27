# PROTOTYPE — deterministic session-thread rebinding

Run from the repository root:

```sh
uv run python packages/surf-chatgpt/prototypes/deterministic_session_thread_rebinding/tui.py
```

## Question

Can Surf atomically move the exact page used to submit a prompt from a temporary Surf thread to a deterministic ChatGPT-session thread, let later short-lived callers reuse that page, and recover the durable ChatGPT session under the same thread name after a browser-bridge restart?

This is throwaway logic, not production code. It models the bridge registry and a caller-held ChatGPT session handle in memory. The important distinction is visible in the page token: rebinding and later observation preserve it while the bridge remains alive; recovery after restart creates a new page for the same `/c/<id>` conversation.
