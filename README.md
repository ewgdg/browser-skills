# browser-skills

Pi package for agent browser automation:

- `surf`: Python browser control through an agent-owned page/window.
- `surf-google-search`: compact structured results from rendered Google Search.

## Install skills

```bash
pi install git:github.com/ewgdg/browser-skills
```

## Surf Python workflow

The Surf skill launches ordinary Python files or stdin through `skills/surf/scripts/run.py`. Each ordinary call starts a fresh interpreter; `--new-session` creates a session interpreter whose id later calls pass back with `--session ID`, with an idle timeout and `--kill-session`/`--list-sessions` for control. The dedicated browser and named threads survive either way. The Surf action CLI is removed. See [Surf skill](skills/surf/SKILL.md) for the execution workflow and [Python API](docs/surf-python-api.md) for the interface.

Supported platforms: Linux and macOS. Windows is unsupported.

The launcher requires Python 3 and `uv`, selects Python 3.11, and supplies `surf-agent[patchright]`. Google Chrome must be installed separately. Patchright is the default; AXI remains an explicitly selected alternative. Camoufox is not supported.

The installed skill's `runtime-revision` selects its matching published runtime. Update the skill to receive runtime updates; no separate Surf runtime installation or dependency override is needed. Set `SURF_SKILL` to the absolute directory containing the installed Surf `SKILL.md`, then validate setup:

```bash
python3 "$SURF_SKILL/scripts/run.py" - <<'PY'
from surf_agent import Browser

browser = Browser()
browser.setup()
print(browser.backend())
print(browser.profile())
PY
```

## Google Search CLI

Google Search retains its CLI and depends on Surf's Python API. Follow the shipped [installation instructions](skills/surf-google-search/docs/cli.md#installation) to install matching published revisions.

```bash
surf-google-search "latest Patchright documentation"
```

Follow the [Google Search skill](skills/surf-google-search/SKILL.md) for retrieval and human handoff. Consult its [CLI reference](skills/surf-google-search/docs/cli.md#request-and-output) for pagination, result eligibility, output fields and errors.

## Profiles and login

Surf uses a dedicated Chrome profile, separate from the user's main tabs. For manual login, extension setup, or existing Chrome cookie access, use the [Surf skill](skills/surf/SKILL.md) and [cookie setup](skills/surf/docs/cookie-import.md).

Cookie import requires explicit scope consent and an inactive, verifiably owned destination. It refreshes changed sources before startup, not on a timer. Source Chrome may remain open. Same Chrome family, OS user, and encryption metadata are required. Imports upsert cookies without propagating source deletions; disabling imports does not remove already imported cookies.

To set up cookie import yourself, add `cookie_source` to Surf's `config.json` (`~/.config/surf-agent/` on Linux, `~/Library/Application Support/surf-agent/` on macOS):

```json
{
  "cookie_source": {
    "root": "/home/you/.config/google-chrome",
    "profile": "Default",
    "family": "chrome",
    "scope": {"domains": ["github.com"]}
  }
}
```

- `root` is the absolute path of Chrome's user-data directory (`/Users/you/Library/Application Support/Google/Chrome` on macOS); `profile` names a profile inside it.
- `family` is `chrome`, `chromium`, `brave` or `edge`, and must match Surf's browser.
- `scope` holds either a domain list or `{"all_domains": true}`, not both.

Keep any other keys already in the file. Surf imports on its next start with no browser running; an invalid entry stops startup with an error saying what is wrong.

## Develop

```bash
uv run pytest
uv run ruff check packages tests benchmarks
```

Skill payloads live under `skills/<skill>/`; Python packages under `packages/<dist-name>/`. For deliberate local development, build a wheel with `uv build packages/surf-agent --wheel --out-dir /tmp/surf-wheels`, then follow the [local-wheel validation procedure](skills/surf/docs/launcher.md#local-development-validation). This override is not part of normal browsing setup.

Projects import the same `surf_agent` package directly, without the launcher. Install the built wheel with the Patchright extra for local development; after publication, use `uv add` with the same commit-pinned Git requirement recorded by the launcher.

### Documentation boundaries

- `skills/<skill>/SKILL.md`: the agent's operating workflow—when to use it, minimal execution examples, decisions, safety rules and completion/cleanup. Link to details at the point they become relevant.
- `skills/<skill>/docs/`: shipped, on-demand references and specialized procedures. Each document states when to read it; it owns its detailed contracts or procedure rather than repeating the main workflow. These files are not separate skills.
- Root `docs/`: project architecture, research and validation. Link to shipped skill references instead of duplicating them; installed skill docs must not depend on files outside the skill payload.

## Installed acceptance

Build a wheel and skill archive (`npm pack --pack-destination /tmp/surf-release`), then extract the archive outside this checkout. With Chrome available, run:

```bash
SURF_INSTALLED_SKILL=/tmp/surf-release/package/skills/surf \
SURF_AGENT_DEPENDENCY=/tmp/surf-wheels/surf_agent-0.1.0-py3-none-any.whl \
SURF_TEST_LIVE_PATCHRIGHT=1 \
uv run pytest tests/test_installed_workflow.py packages/surf-agent/tests/test_patchright_navigation.py
```

The acceptance test uses isolated temporary profiles and a local website. It checks separate file/stdin invocations, browser reattachment, exact input, observations, navigation, persistent session retention, interpreter replacement with the browser preserved, and cleanup. Ordinary project imports and launcher failure/argument contracts are covered by `tests/test_skill_launcher.py`.
