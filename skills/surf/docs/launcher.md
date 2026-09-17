# Launcher setup and validation

Read this for dependency or release-installation failures, or deliberate local development validation. Normal invocation and browser workflow belong in [SKILL.md](../SKILL.md#prepare).

## Dependencies and execution

`scripts/run.py` requires Python 3 and `uv` on PATH. It asks uv for Python 3.11 and `surf-agent[patchright]`, independently of the current project's environment and uv configuration. Install missing uv using its [installation guide](https://docs.astral.sh/uv/getting-started/installation/).

The launcher executes Python directly: file-relative imports, working directory, script arguments, stdin, exceptions and exit codes follow ordinary Python behavior. It does not process inline dependency metadata. Google Chrome is a separate prerequisite; see [Patchright setup](patchright-backend.md#setup-and-selection) for detection and executable overrides.

## Local development validation

Only when deliberately testing local runtime changes, supply an existing built wheel by absolute path:

```bash
export SURF_AGENT_DEPENDENCY=/absolute/path/surf_agent-0.1.0-py3-none-any.whl
python3 "$SURF_SKILL/scripts/run.py" - <<'PY'
from surf_agent import Browser
Browser().setup()
PY
```

This override bypasses the revision pin and installs the wheel with its Patchright extra. It accepts a wheel, not a source checkout or arbitrary dependency string. Successful local validation does not verify remote installation. Unset `SURF_AGENT_DEPENDENCY` when testing a published pin.
