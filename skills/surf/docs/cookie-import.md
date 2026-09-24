# Cookie import setup and debugging

Read this for initial setup, missing login state, or cookie-import startup failures. Run Python through the [skill launcher](../SKILL.md).

## Consent and behavior

Live cookie import is Linux-only, opt-in, and limited to explicitly allowed domains unless the user deliberately consents to all-domain exposure. Do not set, broaden, or reset access without user intent.

Before starting an inactive dedicated profile, AXI and Patchright import configured cookies only when the source fingerprint changed. There is no timed refresh. Imports upsert matching identities; destination-only cookies survive. Logging out in source Chrome therefore does not delete the corresponding Surf cookie.

Inspect configuration without exposing cookie values:

```python
from surf_agent import Browser

print(Browser().cookie_source())
```

After consent, configure a scoped source:

```python
from surf_agent import Browser

browser = Browser()
browser.set_cookie_source(
    "/path/to/chrome-user-data",
    "Default",
    domains=["github.com"],
)
```

Use `all_domains=True` instead of `domains` only for explicit broader consent. The source path names Chrome's user-data directory; the second argument names its profile.

## Import for one site

Use when a login wall blocks the task and the user's normal Chrome is already signed in. Ask first, naming the domain: "Import your Chrome cookies for github.com into Surf?" Proceed only on a yes.

If `Browser().cookie_source()` is `None`, ask which Chrome profile to use (Linux default: `~/.config/google-chrome`, profile `Default`) and configure it with `set_cookie_source(..., domains=[domain])`.

Close the task's thread, then import:

```python
from surf_agent import Browser, Thread

Thread("research-42").close()
print(Browser().import_cookies_for("github.com"))
```

`import_cookies_for` adds the domain to the allowlist (an all-domain scope stays as is), stops the Surf browser, and imports. It refuses and names the open threads when any remain, because stopping the browser would close them; do not close other tasks' threads to get past it—report it to the user. Afterwards reopen the page with the same thread name.

## Force refresh

Close Surf pages first. Global cleanup below is appropriate only when the user owns all remembered threads; otherwise coordinate with their owners:

```python
from surf_agent import Browser

browser = Browser()
browser.close_matching("*")
browser.stop_bridge()
print(browser.import_cookies())
```

An explicit import bypasses source-fingerprint suppression. It still refuses an active or unproven destination. Close any manual Chrome process using that profile before retrying. Stopping the bridge can affect other tasks; do not interrupt their work silently.

## Compatibility failures

Source and destination must use the same Chrome family, belong to the same OS user, and have matching `Local State.os_crypt` metadata. Source Chrome can stay open: Surf reads its cookie database with SQLite online backup. Imported Linux v11 cookies require Chrome's real password store/keychain; Patchright disables its incompatible automation defaults.

Validation and identity failures stop startup instead of silently accepting stale cookies. Correct the reported mismatch and retry explicit import with the destination inactive. For AXI identity overrides, see [AXI backend](axi-backend.md).

## Disable future imports

Only with user intent:

```python
from surf_agent import Browser

Browser().reset_cookie_source()
```

Resetting configuration does not remove cookies already present in Surf.
