# 1Password autofill setup

Read once when enabling the 1Password extension in a Surf profile, or when its native messaging connection fails.

Linux and macOS only. Windows uses registry-based native messaging host discovery, not profile-local file manifests.

Prerequisites: the 1Password desktop app is installed, signed in and running. The app writes the native messaging manifest that step 1 links; if `SOURCE_MANIFEST` below does not exist, set up the app's browser integration first.

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

## 2. Install the extension (user)

The Surf profile is separate from the user's everyday Chrome, so it has none of their extensions. Close automation-owned Surf windows, then open the profile manually:

```python
from surf_agent import Browser

Browser().open_profile()
```

Run this Python through the launcher. It opens the Surf Chrome profile with full browser UI, without automation/debugging. Hand this window to the user: they install 1Password from the Chrome Web Store and confirm the extension connects to the desktop app. Wait for their confirmation. If the extension is already installed, continue to step 3 in the same window.

## 3. Verify

- **Manual:** in that window, the 1Password extension icon shows the vault. If it says "Not connected," check that the manifest symlink is in place and the desktop app is running. Close manual Chrome before restarting automation.
- **Automation:** the manual check does not prove automated fill. On a real login page, follow [autofill](1password-autofill.md) until a snapshot shows `1Password menu is available`; that status is the completion check for setup.

## 4. Pre-unlock habit

Unlock the 1Password desktop app before agent browsing sessions. If locked, inline suggestions prompt for biometric unlock — a system dialog the agent can't interact with.
