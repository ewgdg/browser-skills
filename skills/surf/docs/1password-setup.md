# 1Password autofill setup

One-time. Run once per surf-agent profile.

Linux and macOS only. Windows uses registry-based native messaging host discovery, not profile-local file manifests.

## 1. Native messaging manifest

Chrome native messaging lookup follows `--user-data-dir`. The custom surf-agent profile needs its own manifest.

```bash
PROFILE_DIR=$(surf-agent profile show | jq -r '.profile_dir')
# Linux (also works with 'chromium'):
SOURCE_MANIFEST=~/.config/google-chrome/NativeMessagingHosts/com.1password.1password.json
# macOS:
# SOURCE_MANIFEST=~/Library/Application\ Support/Google/Chrome/NativeMessagingHosts/com.1password.1password.json

mkdir -p "$PROFILE_DIR/NativeMessagingHosts"
ln -sf "$SOURCE_MANIFEST" "$PROFILE_DIR/NativeMessagingHosts/com.1password.1password.json"
```

## 2. Verify the extension

```bash
surf-agent profile open
```

Opens the surf-agent Chrome profile with full browser UI. Click the 1Password extension icon — it should show your vault contents. If it says "Not connected," check that the manifest symlink is in place and the desktop app is running. Close when done.

## 3. Pre-unlock habit

Unlock the 1Password desktop app before agent browsing sessions. If locked, inline suggestions prompt for biometric unlock — a system dialog the agent can't interact with.
