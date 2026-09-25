---
status: accepted
---

# Recognize organic results by weighted fingerprints

Google changes its result markup without notice, and an exact-selector extractor turns each change into a total `ui_changed` outage. The page observation therefore scores every visible outbound link that carries a heading, adding weight for each organic fingerprint present in its card: an `h3` title, a displayed-URL `cite`, the destination host shown in the card, Google's result metadata and snippet blocks, a result slot, the main results column, and snippet prose. Cards holding several titles (news boxes, carousels) lose weight, and ads, AI Overview, People also ask and snippet citations are excluded outright. Links at or above the threshold are results, keeping the most likely title per card.

On live pages (September 2026) organic results score about 15, and the other cards score 4 or less. With any one fingerprint group removed, every result was still recognized; with two removed, 76–100% were, with no false positives.

We accept that a rare non-organic card, including an unlabelled ad, may occasionally pass. When too few fingerprints remain, the page is still reported as `ui_changed` rather than guessed.

The next page is recognized by its address (same query, `start` advanced by one page), independent of pager markup and interface language. A page with results but no pager counts as final only once the document has finished loading, since Google streams result cards in after `DOMContentLoaded`.
