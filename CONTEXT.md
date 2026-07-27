# Domain glossary

**Browser bridge** — Surf's persistent process that owns browser pages independently of short-lived CLI callers.

**ChatGPT session** — A durable ChatGPT conversation identified by its `/c/<id>` URL.

**Cookie import** — A one-way refresh that adds or updates selected cookies from a normal browser profile in the Surf profile. It does not remove cookies that exist only in Surf.

**Cookie source** — The explicitly configured normal browser profile from which Surf imports cookies.

**Cookie scope** — The set of website domains whose cookies a cookie import may expose to Surf. A scope is either an explicit domain allowlist or explicit all-domain consent.

**Observer** — A caller that inspects or waits on an already-recoverable ChatGPT session without owning its generation.

**Surf thread** — A browser-page routing handle owned by Surf's browser bridge. It is not a ChatGPT conversation identity.

**User-visible page** — A normal browser tab or window that may contain user work, whether or not Surf remembers it as a managed thread. Background targets and extension workers are not user-visible pages.
