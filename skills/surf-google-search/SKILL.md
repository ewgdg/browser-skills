---
name: surf-google-search
description: Search the web through rendered Google Search and return compact structured organic results. Use for web search, explicit Google requests, and tasks where live Google ranking or rendered results matter.
---

# surf-google-search

Search through `scripts/run.py`, then use the results for the requested research. Follow this workflow; open linked `docs/` only when the stated task or problem applies.

Set `GOOGLE_SEARCH_SKILL` to the absolute directory containing this installed `SKILL.md`. The launcher installs the runtime pinned by the Surf skill installed beside it; for launcher or dependency failures, read [setup](docs/cli.md#setup). For browser startup or backend-selection problems, read [Surf backends](../surf/docs/backends.md).

## Search

```bash
python3 "$GOOGLE_SEARCH_SKILL/scripts/run.py" "latest Patchright documentation"
python3 "$GOOGLE_SEARCH_SKILL/scripts/run.py" --page 2 --page-count 2 "latest Patchright documentation"
printf 'latest Patchright documentation\n' | python3 "$GOOGLE_SEARCH_SKILL/scripts/run.py" -
```

Read the returned JSON before continuing. On success, use its ordered results and destination URLs for browsing or research. For pagination limits, field meanings, result eligibility, or exit codes, consult the [CLI contract](docs/cli.md#request-and-output).

## Human intervention

A `human_intervention_required` error preserves a browser thread. Tell the user what action is needed and wait for explicit confirmation. Preserve the page while waiting; queued searches share this handoff rather than bypassing it. Focus the window only on request, following [window focus](docs/cli.md#window-focus).

After confirmation, retry the original query and pagination options with `--thread` set to `handoff.thread`. For a default-page search:

```bash
python3 "$GOOGLE_SEARCH_SKILL/scripts/run.py" --thread '<thread>' "same query"
```

## Outcomes

Report affirmed zero results as a valid result. Treat `ui_changed` as a compatibility failure, not an empty result set or permission to substitute another provider. For other errors, report the returned type and hint; do not present failed retrieval as a completed search.

Each search cleans up its ordinary threads automatically. A challenge thread remains open for the human handoff; when abandoning it, use [Surf cleanup](../surf/SKILL.md#cleanup) for that thread only.
