# PROTOTYPE — recent-session discovery and fallback recovery

Run from the repository root:

```sh
uv run python packages/surf-chatgpt/prototypes/recent_session_discovery/tui.py
```

## Question

Should surf-chatgpt treat ChatGPT's visible, ordered **Chats** list as a candidate-discovery seam after submission metadata is lost, returning at most ten session IDs and titles while requiring the caller to choose an ID explicitly before the existing session commands recover it?

This is throwaway logic, not production code. It models a read-only scan of the ChatGPT sidebar, exclusion of pinned conversations, fixed ten-item truncation, fail-closed UI detection, explicit recovery selection, and a preserved human-gate retry that never focuses the browser or retries automatically.
