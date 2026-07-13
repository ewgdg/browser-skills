# browser-skills

Pi package for browser automation skills used by agents.

Currently included:

- `surf`: generic browser-control skill using an agent-owned one-tab window through `surf-agent`.
- `surf-chatgpt`: consult logged-in web ChatGPT through browser automation.

## Install

```bash
pi install git:github.com/ewgdg/browser-skills
```

## Python CLIs

Install the browser helper CLIs separately:

```bash
uv tool install "surf-agent @ git+https://github.com/ewgdg/browser-skills.git#subdirectory=packages/surf-agent"
uv tool install \
  --with "surf-agent @ git+https://github.com/ewgdg/browser-skills.git#subdirectory=packages/surf-agent" \
  "surf-chatgpt @ git+https://github.com/ewgdg/browser-skills.git#subdirectory=packages/surf-chatgpt"
```

`surf-chatgpt` depends on the latest available `surf-agent`.

## Develop

```bash
uv --directory packages/surf-agent run surf-agent --help
uv --directory packages/surf-agent run python -m unittest discover -s tests
uv --directory packages/surf-chatgpt run python -m unittest discover -s tests
```

Skill payload lives under `skills/<skill>/`. Python packages live under `packages/<dist-name>/`.

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
