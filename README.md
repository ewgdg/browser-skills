# browser-skills

Pi package for browser automation skills used by agents.

Currently included:

- `surf`: generic browser-control skill using an agent-owned one-tab window through `surf-agent`.
- `surf-chatgpt`: consult logged-in web ChatGPT through browser automation.

## Install

```bash
pi install /path/to/browser-skills
```

## Develop

```bash
uv --project skills/surf run surf-agent --help
cd skills/surf && uv run python -m unittest discover
uv --project skills/surf-chatgpt run python -m unittest discover -s skills/surf-chatgpt/tests
uv --project skills/surf-chatgpt run python -m unittest discover -s tests/surf-chatgpt
```

Skill runtime payload lives under `skills/<skill>/`. Tests are inside each skill dir under `tests/`.
