# Launcher setup and validation

Read this for dependency or release-installation failures, or deliberate local development validation. Normal invocation and browser workflow belong in [SKILL.md](../SKILL.md#prepare).

## Dependencies and execution

`scripts/run.py` requires Python 3 and `uv` on PATH. It asks uv for Python 3.11 and `surf-agent[patchright]`, independently of the current project's environment and uv configuration. Install missing uv using its [installation guide](https://docs.astral.sh/uv/getting-started/installation/).

The launcher executes Python directly: file-relative imports, working directory, script arguments, stdin, exceptions and exit codes follow ordinary Python behavior. It does not process inline dependency metadata. Google Chrome is a separate prerequisite; see [Patchright setup](patchright-backend.md#setup-and-selection) for detection and executable overrides.

## Published runtime

`runtime-revision`, beside `SKILL.md`, selects a full 40-character Git commit for the runtime dependency. Maintainers test and publish that runtime commit before updating the skill's pin, then validate the published skill without a dependency override. Users update the skill; the launcher installs its matching runtime.

A missing or invalid pin is an installation defect. Stop and report it, then update or repair the installed skill. If the pinned commit cannot be fetched, report that failure.

## Persistent sessions

`run.py --session NAME -` runs one cell in a per-session interpreter instead of a fresh one; [SKILL.md](../SKILL.md) covers when to use it. The interpreter keeps the environment, working directory and Python bindings of the call that created it, runs cells sequentially, and exits with the agent session.

Each session has a socket and a log in `$XDG_RUNTIME_DIR/surf-agent/` (the state directory when `XDG_RUNTIME_DIR` is unset). Cell output travels over the socket; raw file-descriptor writes and subprocess output from a cell appear only in the session log.

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
