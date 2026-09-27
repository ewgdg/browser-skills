---
status: accepted
---

# Keep sessions behind the CLI, not a local MCP server

Sessions end on an idle timeout because nothing tells the worker that the task using it has ended (`plans/active/explicit-sessions.md`). A local MCP server was proposed as that signal and as a place to keep state. It is neither: MCP is a message protocol with no persistence or conversation lifecycle, so it would add a second front end and leave the timeout in place.

## Considered options

**A local stdio MCP server owning the interpreters.** Rejected. What it would offer, checked against what the skill needs, which is harness-agnostic sessions whose lifetime follows the task:

- **State:** the protocol stores nothing. State lives in the server process while it runs, which is what the session worker already does. Streamable HTTP's `Mcp-Session-Id` names a session but leaves storage and expiry to the server.
- **End signal:** a stdio server reads EOF when the client closes its stdin, which happens when the client app exits, not when a conversation ends. The spec leaves the timing of server starts, restarts and reuse to each client, so `/new` or `/clear` may reuse the open connection and send nothing. After a new conversation, the old conversation's interpreters stay alive with nothing to reap them, so the idle timeout stays.
- **Cost:** a second front end beside `run.py`, MCP configuration for each harness, tool schemas in the context of every conversation, and every binding lost when the client restarts or reconnects the server. Harnesses without MCP, such as pi, would still need the CLI.

**A long-running local HTTP MCP daemon.** Rejected: a daemon no client owns has the same unknown lifetime as the current worker and needs the same timeout.

**Harness hooks that kill sessions when a conversation ends** (for example a Claude Code `SessionStart` hook, which fires on `/clear`). Not adopted: each works for one harness only.

## Consequences

Sessions stay behind `run.py`. The idle timeout (`--ttl`) bounds how long an interpreter can outlive its task, and `--kill-session` ends one explicitly. State that must survive the interpreter belongs on disk, through the deferred checkpoint design in `plans/active/explicit-sessions.md`. Revisit if MCP gains a conversation-end notification that clients implement, since that would give a harness-agnostic end signal.
