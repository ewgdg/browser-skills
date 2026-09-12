# Domain glossary

**Google Search** — A browser-mediated query of `google.com` whose product is the ordered, structured organic results. It excludes destination-page research and answer synthesis.
_Avoid_: Websearch, web research

**Organic result** — An ordered, visible, independently positioned Google Search result that points to an external destination. It includes standard and rich result cards while excluding advertisements, hidden or nested answer sources, multi-link Google modules, and navigation controls.
_Avoid_: Search item, link

**Destination URL** — The cleaned result hyperlink returned to the caller. Meaningful destination parameters and named or media fragments remain, while Google redirect wrappers, known Google-added tracking, and text-highlight directives are removed.
_Avoid_: Canonical URL, displayed URL

**Page position** — The one-based order of an organic result among eligible organic records extracted from one rendered Search page before invocation-scoped deduplication. Removing a duplicate leaves its page-local position empty.
_Avoid_: Global rank, item offset

**Search page** — One rendered page in Google Search's one-based navigation sequence. Its organic result count is not fixed.
_Avoid_: Result window, item range

**Snippet** — The normalized visible description Google associates with an organic result. Its displayed date and interface labels are excluded.
_Avoid_: Raw result text, summary

**Page span** — One or more consecutive Search pages selected by a start page and page count. Repeated destinations are removed within the span without carrying deduplication state into another search.
_Avoid_: Page offset, page limit

**Displayed date** — The optional date label Google visibly associates with an organic result. It is preserved as shown and is not an independently verified publication date.
_Avoid_: Publication date, inferred date

**Browser thread** — A named browser interaction context managed by Surf, presented as a window or tab. The thread is the caller's unit of browser interaction and ownership, not the underlying browser page identifier or bridge address.

**Cookie import** — A one-way refresh that adds or updates selected cookies from a normal browser profile in the Surf profile. It does not remove cookies that exist only in Surf.

**Cookie source** — The explicitly configured normal browser profile from which Surf imports cookies.

**Cookie scope** — The set of website domains whose cookies a cookie import may expose to Surf. A scope is either an explicit domain allowlist or explicit all-domain consent.

**User-visible page** — A normal browser tab or window that may contain user work, whether or not Surf remembers it as a managed thread. Background targets and extension workers are not user-visible pages.
