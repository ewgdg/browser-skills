# 1Password autofill

Use this when filling a login form with the configured 1Password extension. For initial configuration, read [1Password setup](1password-setup.md).

Pre-condition: 1Password desktop app unlocked.

## Automated fill workflow

1Password inline suggestions may not appear as clickable DOM elements in snapshots (shadow DOM, ARIA live regions). Use keyboard navigation instead:

```python
from surf_agent import Thread

thread = Thread("login")
thread.open("https://example.com/login")
thread.emit(thread.snapshot())
```

Run Python through the [Surf launcher](../SKILL.md). Inspect the snapshot for the actual field target, then reattach in a fresh script:

```python
from surf_agent import Thread

thread = Thread("login")
thread.click("@email-field")  # replace with the observed target
thread.emit(thread.snapshot())
```

Look for status `1Password menu is available. Press down arrow to select.` Once confirmed, batch the deterministic keyboard actions in the next fresh script:

```python
from surf_agent import Thread

thread = Thread("login")
thread.press("ArrowDown")
thread.press("Enter")
thread.emit(thread.snapshot())
```

Inspect the resulting page before submitting anything else: 1Password may already have submitted the form. Click the observed login button only if still required. Confirm login from page state, not merely the absence of a form. Avoid printing passwords, cookies, or other secrets. Close `Thread("login")` when finished.

## Fallback (locked or no match)

When the snapshot doesn't show the "1Password menu is available" status, fall back to the human-in-the-loop workflow in the main Surf skill.
