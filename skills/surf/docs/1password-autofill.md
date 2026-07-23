# 1Password autofill

Agent workflow. One-time setup: [1Password setup](1password-setup.md).

Pre-condition: 1Password desktop app unlocked.

## Automated fill workflow

1Password inline suggestions may not appear as clickable DOM elements in snapshots (shadow DOM, ARIA live regions). Use keyboard navigation instead:

```
# 1. Navigate to login page
surf-agent --thread main open https://example.com/login

# 2. Click a form field to trigger 1Password inline suggestion
surf-agent --thread main click @email-field

# 3. Snapshot to confirm 1Password is responding
#    Look for: status "1Password menu is available. Press down arrow to select."
surf-agent --thread main snapshot

# 4. Select and fill
surf-agent --thread main press ArrowDown
surf-agent --thread main press Enter

# 5. If 1Password didn't auto-submit, click the login button
surf-agent --thread main click @login-button
```

### Composed with `do`

```bash
surf-agent --thread main do <<'EOF'
open https://example.com/login
snapshot --baseline
click @email-field
snapshot --diff
press ArrowDown
press Enter
snapshot --diff
EOF
```

Check the final diff: if still on the login page, 1Password didn't auto-submit — run `click @login-button`. If redirected, login succeeded.

## Fallback (locked or no match)

When the snapshot doesn't show the "1Password menu is available" status, fall back to human-in-the-loop: focus the window, tell user to autofill, then snapshot after confirmation.
