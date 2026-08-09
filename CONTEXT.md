# Domain glossary

**Google Search** — A browser-mediated query of `google.com` whose product is the ordered, structured organic results. It excludes destination-page research and answer synthesis.
_Avoid_: Websearch, web research

**Organic result** — A ranked Google Search result that points to an external destination and is not an advertisement, Google-owned answer module, or navigation control.
_Avoid_: Search item, link

**Destination URL** — The cleaned result hyperlink returned to the caller. Meaningful destination parameters and named or media fragments remain, while Google redirect wrappers, known Google-added tracking, and text-highlight directives are removed.
_Avoid_: Canonical URL, displayed URL

**Result rank** — The nominal one-based position assigned to an organic result within its Google Search page before invocation-scoped deduplication. Removing a duplicate leaves its rank slot empty.
_Avoid_: Google rank, item offset

**Search page** — One rendered page in Google Search's one-based navigation sequence. It has ten nominal result slots but may yield fewer organic results after filtering.
_Avoid_: Result window, item range

**Snippet** — The normalized visible description Google associates with an organic result. Its displayed date and interface labels are excluded.
_Avoid_: Raw result text, summary

**Page span** — One or more consecutive Search pages selected by a start page and page count. Repeated destinations are removed within the span without carrying deduplication state into another search.
_Avoid_: Page offset, page limit

**Displayed date** — The optional date label Google visibly associates with an organic result. It is preserved as shown and is not an independently verified publication date.
_Avoid_: Publication date, inferred date

**Abandonment** — An explicit user-authorized operation that stops an active response attempt when necessary, affirms the resulting state, and closes its browser thread.

**Browser thread** — The live bridge address of one managed browser page. A thread is process-local page identity, not durable website identity.

**Cookie import** — A one-way refresh that adds or updates selected cookies from a normal browser profile in the Surf profile. It does not remove cookies that exist only in Surf.

**Cookie source** — The explicitly configured normal browser profile from which Surf imports cookies.

**Cookie scope** — The set of website domains whose cookies a cookie import may expose to Surf. A scope is either an explicit domain allowlist or explicit all-domain consent.

**ChatGPT session** — A durable ChatGPT conversation identity represented by its canonical `https://chatgpt.com/c/<id>` URL. `surf-chatgpt` maps it to a deterministic browser thread when browser work is needed.

**User-visible page** — A normal browser tab or window that may contain user work, whether or not Surf remembers it as a managed thread. Background targets and extension workers are not user-visible pages.
