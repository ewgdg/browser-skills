# browser-skills

Pi package for agent browser automation:

- `surf`: Python browser control through an agent-owned page/window.
- `surf-google-search`: compact structured results from rendered Google Search.

## Install skills

```bash
pi install git:github.com/ewgdg/browser-skills
```

## Surf Python workflow

The Surf skill launches ordinary Python files or stdin through `skills/surf/scripts/run.py`. Each call starts a fresh interpreter; the dedicated browser and named threads survive between calls. The Surf action CLI is removed. See [Surf skill](skills/surf/SKILL.md) for the execution workflow and [Python API](docs/surf-python-api.md) for the interface.

The launcher requires Python 3 and `uv`, selects Python 3.11, and supplies `surf-agent[patchright]`. Google Chrome must be installed separately. Patchright is the default; AXI remains an explicitly selected alternative. Camoufox is not supported.

**Release status:** `skills/surf/runtime-revision` is intentionally `UNRELEASED`. Default launch fails until an authorized push publishes a tested commit and its full commit ID is pinned. Local wheel validation is available now; it does not prove remote installation works:

```bash
uv build packages/surf-agent --wheel --out-dir /tmp/surf-wheels
# Set this to the actual absolute wheel path produced above.
export SURF_AGENT_DEPENDENCY=/tmp/surf-wheels/surf_agent-0.1.0-py3-none-any.whl
python3 skills/surf/scripts/run.py - <<'PY'
from surf_agent import Browser

browser = Browser()
browser.setup()
print(browser.backend())
print(browser.profile())
PY
```

## Google Search CLI

Google Search retains its CLI and depends on Surf's Python API. Install both from the same intended release/source revision; installing from the repository default branch below uses remote code, not unpublished local changes:

```bash
uv tool install \
  --with "surf-agent[patchright] @ git+https://github.com/ewgdg/browser-skills.git#subdirectory=packages/surf-agent" \
  "surf-google-search @ git+https://github.com/ewgdg/browser-skills.git#subdirectory=packages/surf-google-search"
```

```bash
surf-google-search "latest Patchright documentation"
surf-google-search --page 2 --page-count 2 "latest Patchright documentation"
printf 'latest Patchright documentation\n' | surf-google-search -
```

Pass exact `-` as the required query to read stdin. One invocation returns compact JSON for one to three consecutive rendered Google Search pages.

Searches sharing one Surf profile serialize with randomized natural pacing and honor Patchright or AXI selection. Results include visible primary organic and top-level rich records; ads, hidden/nested sources, and multi-link Google modules are excluded. Each result includes its Search page and one-based page-local position. Duplicate destinations are removed within the invocation without compacting positions.

A Google challenge preserves one browser thread and blocks queued searches from repeatedly navigating until resolved or closed. Follow the [Google Search skill](skills/surf-google-search/SKILL.md) for human handoff.

## Profiles and login

Surf uses a dedicated Chrome profile, separate from the user's main tabs. For manual login, extension setup, or existing Chrome cookie access, use the [Surf skill](skills/surf/SKILL.md) and [cookie setup](skills/surf/docs/cookie-import.md).

Cookie import requires explicit scope consent and an inactive, verifiably owned destination. It refreshes changed sources before startup, not on a timer. Source Chrome may remain open. Same Chrome family, OS user, and encryption metadata are required. Imports upsert cookies without propagating source deletions; disabling imports does not remove already imported cookies.

## Develop

```bash
uv run pytest
uv run ruff check packages tests benchmarks
```

Skill payloads live under `skills/<skill>/`; Python packages under `packages/<dist-name>/`. Use the built-wheel override above to validate the launcher against local package changes before releasing.

Projects import the same `surf_agent` package directly, without the launcher. Install the built wheel with the Patchright extra for local development; after publication, use `uv add` with the same commit-pinned Git requirement recorded by the launcher.

## Installed acceptance and release

Build a wheel and skill archive (`npm pack --pack-destination /tmp/surf-release`), then extract the archive outside this checkout. With Chrome available, run:

```bash
SURF_INSTALLED_SKILL=/tmp/surf-release/package/skills/surf \
SURF_AGENT_DEPENDENCY=/tmp/surf-wheels/surf_agent-0.1.0-py3-none-any.whl \
SURF_TEST_LIVE_PATCHRIGHT=1 \
uv run pytest tests/test_installed_workflow.py packages/surf-agent/tests/test_patchright_navigation.py
```

The acceptance test uses isolated temporary profiles and a local website. It checks separate file/stdin invocations, browser reattachment, exact input, observations, navigation and cleanup. Ordinary project imports and launcher failure/argument contracts are covered by `tests/test_skill_launcher.py`.

Publication requires authorization: push the tested runtime commit, verify that its full SHA is reachable, write that SHA into `skills/surf/runtime-revision`, then publish the skill pin. Repeat installed acceptance with `SURF_AGENT_DEPENDENCY` unset. Keep issue #21 open until that no-override remote installation passes; an unpushed SHA is not a release.
