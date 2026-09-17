# Surf backends

Read this when selecting a backend or diagnosing an unexpected selection. Surf supports one active backend at a time: Patchright by default, AXI as an explicit alternative.

Selection priority:
1. `SURF_AGENT_BACKEND`
2. persisted platform user config
3. `patchright`

Run these Python snippets through the [skill launcher](../SKILL.md):

```python
from surf_agent import Browser

browser = Browser()
print(browser.backend())  # backend, selection source, config_file
print(browser.profile())  # actual dedicated profile and runtime settings
```

To persist a selection, call `browser.set_backend("axi")` or `browser.set_backend("patchright")`. Changing selection stops the previous persisted backend first; cleanup failure leaves configuration unchanged. Temporary environment overrides are ignored for that cleanup, but still take priority for browser use.

To clear selection, stop the current runtime with `browser.stop_bridge()`, then call `browser.reset_backend()`. Reset itself does not stop runtime. Stop any differently selected environment-override runtime too before moving between backends that share a profile.

For one script without changing config:
```bash
SURF_AGENT_BACKEND=patchright python3 "$SURF_SKILL/scripts/run.py" /tmp/browse.py
```

- [Patchright](patchright-backend.md): Chrome-channel persistent-profile backend.
- [AXI](axi-backend.md): Chrome DevTools alternative.
- [Cookie import](cookie-import.md): explicitly scoped reuse of existing login state. Both backends share the default Surf Chrome profile; cookie import requires an inactive, verifiably owned destination.
