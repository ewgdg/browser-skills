# Google Search CLI reference

Read this to fix launcher setup, interpret options or output, or focus a preserved challenge window. The operating workflow and human-confirmation rules live in [SKILL.md](../SKILL.md).

## Setup

`scripts/run.py` requires Python 3, `uv`, Google Chrome, and the Surf skill installed beside this skill. It installs Google Search and `surf-agent` from the commit in Surf's `runtime-revision`, so both skills always drive the shared browser with the same runtime. Update the skills to receive runtime updates; there is no separate installation. Arguments, stdin and exit codes pass through unchanged. It honors the [selected Surf backend](../../surf/docs/backends.md), and [Surf launcher setup](../../surf/docs/launcher.md) covers `uv` and pin failures.

For local development, set both `SURF_AGENT_DEPENDENCY` and `SURF_GOOGLE_SEARCH_DEPENDENCY` to absolute built-wheel paths, as in [Surf's local-wheel validation](../../surf/docs/launcher.md#local-development-validation).

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

Eligible results include standard organic records and visible, independently positioned top-level rich results. Ads are dampened rather than excluded: one with a full result's title, displayed URL and snippet is included. Hidden/nested answer sources and multi-link Google modules are excluded. Returned URLs do not preserve Google referrer behavior; that requires clicking a rendered result in a retained Search page through a separate browsing workflow.

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
