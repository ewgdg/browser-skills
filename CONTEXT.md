# Domain glossary

**Abandonment** — An explicit user-authorized operation that stops an active response attempt when necessary, affirms the resulting state, and closes its browser thread.

**Browser thread** — The live bridge address of one managed browser page. A thread is process-local page identity, not durable website identity.

**Cookie import** — A one-way refresh that adds or updates selected cookies from a normal browser profile in the Surf profile. It does not remove cookies that exist only in Surf.

**Cookie source** — The explicitly configured normal browser profile from which Surf imports cookies.

**Cookie scope** — The set of website domains whose cookies a cookie import may expose to Surf. A scope is either an explicit domain allowlist or explicit all-domain consent.

**ChatGPT session** — A durable ChatGPT conversation identity represented by its canonical `https://chatgpt.com/c/<id>` URL. `surf-chatgpt` maps it to a deterministic browser thread when browser work is needed.

**User-visible page** — A normal browser tab or window that may contain user work, whether or not Surf remembers it as a managed thread. Background targets and extension workers are not user-visible pages.
