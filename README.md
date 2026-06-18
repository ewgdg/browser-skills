# surf-chatgpt-skill

Pi skill for consulting logged-in web ChatGPT through `surf`.

## Install

```bash
pi install /absolute/path/to/surf-chatgpt
```

## Develop

```bash
cd skills/surf-chatgpt
uv run surf-chatgpt --help
uv run python -m unittest discover -s ../../tests
```

Runtime skill payload lives in `skills/surf-chatgpt/`. Tests stay repo-only.
