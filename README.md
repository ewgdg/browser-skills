# browser-skills

Pi package for browser automation skills used by agents.

Currently included:

- `surf-chatgpt`: consult logged-in web ChatGPT through browser automation.

Planned:

- `surf`: compact general browser-control skill with a stable helper interface. Default behavior should open a background `surf-agent` window, then navigate by window id.

## Install

```bash
pi install /path/to/browser-skills
```

## Develop

```bash
uv --project skills/surf-chatgpt run surf-chatgpt --help
uv --project skills/surf-chatgpt run python -m unittest discover -s tests/surf-chatgpt
```

Skill runtime payload lives under `skills/<skill>/`. Skill-specific tests live under `tests/<skill>/` and are not shipped.
