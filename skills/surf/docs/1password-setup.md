# 1Password autofill setup

Read once when enabling the 1Password extension in a Surf profile, or when its native messaging connection fails.

Linux and macOS only. Windows uses registry-based native messaging host discovery, not profile-local file manifests.

## 1. Native messaging manifest

Chrome native messaging lookup follows `--user-data-dir`. The dedicated Surf profile needs its own manifest.

Use the [skill launcher](../SKILL.md) to resolve the actual profile:

```bash
PROFILE_DIR=$(python3 "$SURF_SKILL/scripts/run.py" - <<'PY'
from surf_agent import Browser
print(Browser().profile().profile_dir)
PY
)
# Linux (also works with 'chromium'):
SOURCE_MANIFEST=~/.config/google-chrome/NativeMessagingHosts/com.1password.1password.json
# macOS:
# SOURCE_MANIFEST=~/Library/Application\ Support/Google/Chrome/NativeMessagingHosts/com.1password.1password.json

mkdir -p "$PROFILE_DIR/NativeMessagingHosts"
ln -sf "$SOURCE_MANIFEST" "$PROFILE_DIR/NativeMessagingHosts/com.1password.1password.json"
```

## 2. Verify the extension

Close automation-owned Surf windows before opening the profile manually:

```python
from surf_agent import Browser

Browser().open_profile()
```

Run this Python through the launcher. It opens the Surf Chrome profile with full browser UI, without automation/debugging. Click the 1Password extension icon — it should show your vault contents. If it says "Not connected," check that the manifest symlink is in place and the desktop app is running. Close manual Chrome when done before restarting automation.

## 3. Pre-unlock habit

Unlock the 1Password desktop app before agent browsing sessions. If locked, inline suggestions prompt for biometric unlock — a system dialog the agent can't interact with.
