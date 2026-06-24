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
