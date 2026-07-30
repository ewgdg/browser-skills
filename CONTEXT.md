# Domain glossary

**Abandonment** — An explicit user-authorized operation that stops an active response attempt when necessary, affirms the resulting state, and releases the owned browser page.

**Cookie import** — A one-way refresh that adds or updates selected cookies from a normal browser profile in the Surf profile. It does not remove cookies that exist only in Surf.

**Cookie source** — The explicitly configured normal browser profile from which Surf imports cookies.

**Cookie scope** — The set of website domains whose cookies a cookie import may expose to Surf. A scope is either an explicit domain allowlist or explicit all-domain consent.

**Retained page** — A user-visible page kept by the live browser bridge for a surf-chatgpt submission, durable session, or human handoff.

**Retained-page protection** — Live bridge metadata that prevents opportunistic cleanup. Explicit retention and human intervention establish protection until abandonment, manual closure, or bridge restart.

**User-visible page** — A normal browser tab or window that may contain user work, whether or not Surf remembers it as a managed thread. Background targets and extension workers are not user-visible pages.
