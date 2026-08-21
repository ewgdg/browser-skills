# browser-skills

Pi package for browser automation skills used by agents.

Currently included:

- `surf`: generic browser-control skill using an agent-owned one-tab window through `surf-agent`.
- `surf-google-search`: retrieve compact structured organic results from rendered Google Search pages.
- `surf-chatgpt`: consult logged-in web ChatGPT through browser automation.

## Install

```bash
pi install git:github.com/ewgdg/browser-skills
```

## Python CLIs

Install the browser helper CLIs separately:

```bash
uv tool install "surf-agent[patchright] @ git+https://github.com/ewgdg/browser-skills.git#subdirectory=packages/surf-agent"
uv tool install \
  --with "surf-agent[patchright] @ git+https://github.com/ewgdg/browser-skills.git#subdirectory=packages/surf-agent" \
  "surf-google-search @ git+https://github.com/ewgdg/browser-skills.git#subdirectory=packages/surf-google-search"
uv tool install \
  --with "surf-agent[patchright] @ git+https://github.com/ewgdg/browser-skills.git#subdirectory=packages/surf-agent" \
  "surf-chatgpt @ git+https://github.com/ewgdg/browser-skills.git#subdirectory=packages/surf-chatgpt"
```

The site-specific CLIs depend on the latest available `surf-agent`.

## Browser backends

Patchright is Surf's default backend. `surf-google-search` honors the selected
Patchright or AXI backend. `surf-chatgpt` supports only Patchright and rejects AXI
before starting or inspecting a browser. Camoufox is not supported.

## Develop

```bash
uv --directory packages/surf-agent run surf-agent --help
uv --directory packages/surf-agent run python -m unittest discover -s tests
uv run pytest packages/surf-google-search/tests
uv --directory packages/surf-chatgpt run python -m unittest discover -s tests
```

Skill payload lives under `skills/<skill>/`. Python packages live under `packages/<dist-name>/`.

## Google Search

`surf-google-search` returns compact JSON containing primary organic results from one to three consecutive rendered Google Search pages:

```bash
surf-google-search "latest Patchright documentation"
surf-google-search --page 2 --page-count 2 "latest Patchright documentation"
printf 'latest Patchright documentation\n' | surf-google-search -
```

Pass exact `-` as the required query to read it from stdin.

Searches sharing one Surf profile run one at a time with randomized natural pacing. Standard organic and visible top-level rich results are included; ads, hidden or nested answer sources, and multi-link Google modules are excluded. Every result carries its Search page and one-based page-local position; duplicate destinations are removed only within one invocation without compacting those positions. A Google challenge preserves one browser thread and blocks queued searches from repeatedly navigating until the challenge is resolved or its page is closed.

## Live cookie import

`surf-agent` can optionally refresh selected encrypted cookies from a running normal Chrome profile into its inactive Surf Chrome profile. Configure an explicit source and exposure scope first:

```bash
surf-agent profile cookie-source set \
  --source ~/.config/google-chrome \
  --source-profile Default \
  --domain github.com \
  --domain openai.com
surf-agent profile import-cookies
```

Use `--all-domains` only when that broader exposure is intentional. Imports use SQLite online backup, so the source Chrome may stay open. Source and Surf must be the same Chrome family, owned by the same OS user, and have matching `Local State.os_crypt` metadata. Patchright disables its `--password-store=basic` and `--use-mock-keychain` automation defaults so imported Linux v11 cookies use Chrome’s real OS password store/keychain. Rows are upserted only: source cookies update/add matching destination identities, while destination-only cookies (including a source logout) remain.

Before AXI or Patchright starts an inactive configured profile, Surf automatically imports only when its source fingerprint changed. There is no timer-based refresh. Cookie import fails closed when the destination is active or identity cannot be proven; stop Surf, fix the source/configuration, then run `surf-agent profile import-cookies` to retry. After the last user-visible Surf page closes, AXI stops after a two-second recheck; Patchright stops immediately after returning the close response. If Chrome independently closes Patchright's persistent context, Surf transparently starts a fresh bridge and retries the interrupted command once.
