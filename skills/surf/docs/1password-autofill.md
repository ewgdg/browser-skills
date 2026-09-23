# 1Password autofill

Use this when filling a login form with the configured 1Password extension. For initial configuration, read [1Password setup](1password-setup.md).

Pre-condition: 1Password desktop app unlocked.

## Automated fill workflow

1Password inline suggestions may not appear as clickable elements in snapshots (shadow DOM, ARIA live regions), so drive them with the keyboard. Work on the task's existing thread and interpreter mode from the [Surf workflow](../SKILL.md); these steps assume `thread` already shows the login page with an emitted snapshot (in a fresh script, rebuild it as `Thread(<task thread name>)`).

1. Click the observed username field and emit a snapshot:

   ```python
   thread.click("@e7")  # username field ref from the observed snapshot
   thread.emit(thread.snapshot())
   ```

2. Look for the status `1Password menu is available. Press down arrow to select.` Once it appears, batch the keyboard selection:

   ```python
   thread.press("ArrowDown")
   thread.press("Enter")
   thread.emit(thread.snapshot())
   ```

3. Inspect the resulting page before submitting anything else: 1Password may already have submitted the form. Click the observed login button only if still required. Confirm login from page state, not merely the absence of a form. Keep passwords, cookies, and other secrets out of printed output.

## Fallback (locked or no match)

When the snapshot doesn't show the "1Password menu is available" status, fall back to [Login and human unblock](../SKILL.md#login-and-human-unblock).
