---
name: surf-google-search
description: Search the web through rendered Google Search and return compact structured organic results. Use for web search, explicit Google requests, and tasks where live Google ranking or rendered results matter.
---

# surf-google-search

Use the installed `surf-google-search` CLI. It serializes searches sharing one Surf profile, uses natural pacing, and returns one compact JSON object.

## Prerequisite

```bash
uv tool install \
  --with "surf-agent[patchright] @ git+https://github.com/ewgdg/browser-skills.git#subdirectory=packages/surf-agent" \
  "surf-google-search @ git+https://github.com/ewgdg/browser-skills.git#subdirectory=packages/surf-google-search"
```

## Search

```bash
surf-google-search "latest Patchright documentation"
surf-google-search --page 2 "latest Patchright documentation"
surf-google-search --page 2 --page-count 2 "latest Patchright documentation"
```

`--page` is one-based. `--page-count` accepts 1–3 and defaults to 1. One invocation processes one query and returns every unique eligible result from the requested consecutive Google pages. Eligible results include standard organic records and visible, independently positioned rich result cards; hidden or nested answer sources and multi-link Google modules are excluded.

Successful output contains `query`, requested/visited pages, ordered `results`, and `exhausted`. Each result contains one-based Search `page`, one-based page-local `position`, `title`, cleaned destination `url`, nullable `snippet`, and nullable `displayed_date`. Position gaps are intentional when a repeated destination is removed.

Use returned destination URLs with the appropriate browsing or research workflow. Search output does not preserve Google referrer behavior; that requires clicking a rendered result in a retained Search page.

## Human intervention

A Google challenge returns `human_intervention_required` and preserves one browser thread. Tell the user what action is required and wait for explicit confirmation. Never focus the page automatically. If useful, show the user:

```bash
surf-agent --thread '<thread>' focus
```

After confirmation, retry the exact search with the returned thread:

```bash
surf-google-search --thread '<thread>' "same query"
```

Queued searches sharing that Surf profile return the same handoff until the challenge is resolved or its page is closed.

## Outcomes

- Exit `0`: valid search, including affirmed zero results or exhaustion.
- Exit `1`: browser, Google-interface, challenge, or internal operational failure.
- Exit `2`: invalid command input.

Treat `ui_changed` as a compatibility failure. Do not reinterpret it as an empty result set or fall back to another search provider.
