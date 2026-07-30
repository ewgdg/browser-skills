# Domain glossary

**Abandonment** — An explicit decision to stop any active response attempt and release its retained page. Age, inactivity, timeout, and caller exit do not imply abandonment.

**Allowed page scope** — The recognized ChatGPT session, pre-session, or human-gate surface on which an owned page may be automatically inspected or closed. Ownership does not survive navigation outside this scope as mutation authority.

**Browser bridge** — Surf's persistent process that owns browser pages independently of short-lived CLI callers.

**ChatGPT session** — A durable ChatGPT conversation identified by its `/c/<id>` URL.

**Cookie import** — A one-way refresh that adds or updates selected cookies from a normal browser profile in the Surf profile. It does not remove cookies that exist only in Surf.

**Cookie source** — The explicitly configured normal browser profile from which Surf imports cookies.

**Cookie scope** — The set of website domains whose cookies a cookie import may expose to Surf. A scope is either an explicit domain allowlist or explicit all-domain consent.

**Observer** — A caller that inspects or waits on an already-recoverable ChatGPT session without owning its generation.

**Page ownership proof** — Authority for surf-chatgpt to mutate a page, established by a live surf-chatgpt bridge binding, explicit recovery of one exact canonical session URL, or an explicitly supplied preserved pre-session Surf thread.

**Indeterminate submission outcome** — A submission error reported when the browser may have sent a prompt but the submission handshake did not produce a recoverable ChatGPT session. It never authorizes automatic resubmission.

**Metadata-only inspection** — Automatic inspection whose extraction boundary returns only session identity, exact URL match, attempt state, human-gate type, and ownership or protection metadata. Conversation content and rendered-page artifacts do not cross that boundary.

**Retained page** — A user-visible page that the live browser bridge keeps available for a surf-chatgpt submission, ChatGPT session, or human handoff.

**Retained-page protection** — An explicit choice to keep a terminal retained page open until abandonment. Generating, human-blocked, and unclassifiable retained pages are protected by their state rather than by this choice.

**Surf thread** — A browser-page routing handle owned by Surf's browser bridge. It is not a ChatGPT conversation identity.

**Submission handshake** — The at-most-once sequence from issuing the browser-side send through observing the assigned ChatGPT session ID and rebinding the exact live page to its deterministic session-derived Surf thread. It has a 30-second deadline independent of response observation.

**User-visible page** — A normal browser tab or window that may contain user work, whether or not Surf remembers it as a managed thread. Background targets and extension workers are not user-visible pages.
