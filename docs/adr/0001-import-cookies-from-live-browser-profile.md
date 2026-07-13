---
status: accepted
---

# Import cookies from a live browser profile

Surf Agent will refresh authentication by importing cookies from an explicitly configured live browser profile into its inactive Chrome-family profile. V1 supports Linux, requires the same browser family, OS user, and matching encryption metadata, and uses SQLite online backup so the locked source browser remains running. Imports are limited to an explicit domain allowlist or explicit all-domain consent and transactionally upsert matching cookies without deleting destination-only cookies.

## Considered options

Full profile copying was rejected because browser storage mixes authentication with large caches, offline application data, history, extensions, and unrelated private state. Filesystem snapshots and process suspension were rejected because cookie-only import can use SQLite's supported online backup mechanism without filesystem dependencies or interrupting the source browser. A browser-extension control path was rejected because cookie refresh must remain part of the existing AXI/Patchright profile workflow rather than introduce another backend.

## Consequences

The configured source is fingerprinted using its Cookies database and WAL/journal metadata; automatic import runs only after observable change, while an explicit import always runs. Cookie import is opt-in, preserves destination-only sessions for convenience, and aborts backend startup on validation or compatibility failure. When no user-visible pages remain, AXI stops its bridge after a two-second recheck; Patchright stops immediately after returning the close response because Chrome may already have closed its persistent context. Closed Patchright contexts restart through lifecycle preflight and retry the interrupted command once. Automated coverage uses deterministic SQLite and lifecycle tests; real-browser smoke testing remains optional.
