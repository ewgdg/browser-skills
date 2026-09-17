# Google Search CLI reference

Read this to install/update the command, interpret options or output, or focus a preserved challenge window. The operating workflow and human-confirmation rules live in [SKILL.md](../SKILL.md).

## Installation

Requires `uv`, Google Chrome, and a working Surf backend. Install Google Search and its Surf dependency from the same published revision. Replace the placeholder with a reachable full commit ID; an unpushed local commit is not installable this way:

```bash
SURF_REV='<published-40-character-commit>'
uv tool install \
  --with "surf-agent[patchright] @ git+https://github.com/ewgdg/browser-skills.git@$SURF_REV#subdirectory=packages/surf-agent" \
  "surf-google-search @ git+https://github.com/ewgdg/browser-skills.git@$SURF_REV#subdirectory=packages/surf-google-search"
```

Google Search retains its own CLI; Surf's removed action CLI is not a prerequisite. It honors the [selected Surf backend](../../surf/docs/backends.md). Its installed Python dependency is separate from the [Surf skill launcher's runtime pin](../../surf/docs/launcher.md).

## Request and output

| Input | Contract |
| --- | --- |
| `QUERY` | Required nonempty query; exact `-` reads stdin. Leading/trailing whitespace is trimmed. |
| `--page N` | One-based start page; defaults to 1. |
| `--page-count N` | 1–3 consecutive pages; defaults to 1. |
| `--thread NAME` | Surf thread to use when resuming a preserved handoff. |

Searches sharing a Surf profile serialize with natural pacing. Normal invocations emit one compact JSON object; `--help` prints usage instead.

Success contains `ok: true`, `query`, `pages` (`start`, `requested`, `visited`), ordered `results`, and `exhausted`. Each result contains `page`, `position`, `title`, `url`, nullable `snippet`, and nullable `displayed_date`.

Result pages and page-local positions are one-based. Cleaned destination URLs are deduplicated within an invocation without compacting positions, so gaps are intentional. `exhausted` indicates affirmed no results or no next page; merely reaching the requested page count does not establish exhaustion.

Eligible results include standard organic records and visible, independently positioned top-level rich results. Ads, hidden/nested answer sources, and multi-link Google modules are excluded. Returned URLs do not preserve Google referrer behavior; that requires clicking a rendered result in a retained Search page through a separate browsing workflow.

Failure contains `ok: false` and `error` with `type`, `message`, and `hint`. Types are `invalid_request`, `browser_unavailable`, `human_intervention_required`, `ui_changed`, and `internal_error`. A challenge also provides `handoff.thread`; follow the [handoff workflow](../SKILL.md#human-intervention) before retrying.

| Exit | Meaning |
| --- | --- |
| `0` | Valid search, including affirmed zero results or exhaustion. |
| `1` | Browser, Google-interface, challenge, or internal operational failure. |
| `2` | Invalid command input. |

## Window focus

When the user requests focus, use the [Surf Python launcher](../../surf/SKILL.md) with the returned `handoff.thread`:

```python
from surf_agent import Thread

Thread("<returned-thread>").focus()
```

The Surf launcher release gate applies independently of the installed Google Search CLI. If it is unavailable, ask the user to select the preserved window manually rather than starting a replacement search.
