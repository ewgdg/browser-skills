# Cookie import setup and debugging

Read this for initial cookie-source setup, when expected login state is missing, or when cookie import prevents Surf from starting. During normal browsing, configured cookie import runs automatically and needs no agent action.

## Normal behavior

Cookie import is opt-in and limited to explicitly allowed domains unless the user deliberately consents to `--all-domains`.

For AXI and Patchright, Surf checks for changed source cookies before starting an inactive dedicated profile and imports them automatically. It does not refresh on a timer. Camoufox does not support cookie import.

Imports add or update matching cookies but do not delete destination-only cookies. Logging out in the source browser therefore does not propagate that deletion to Surf.

## Inspect configuration

```bash
surf-agent profile cookie-source show
```

Do not set, broaden, or reset the cookie source without user intent: the configuration controls which browser authentication data Surf may access.

Initial scoped configuration example:

```bash
surf-agent profile cookie-source set \
  --source /path/to/chrome-user-data \
  --source-profile Default \
  --domain github.com
```

Use `--all-domains` only when the user explicitly wants that broader exposure.

## Force a refresh

First close Surf pages:

```bash
surf-agent close-all
```

Wait for the bridge's two-second idle shutdown, then run:

```bash
surf-agent profile import-cookies
```

An explicit import bypasses automatic source-fingerprint suppression. If the destination profile is still active, stop the process using it before retrying.

## Compatibility failures

The source and destination must:

- use the same Chrome browser family;
- belong to the same OS user;
- have matching `Local State.os_crypt` metadata.

The source Chrome may remain open because Surf reads its cookie database with SQLite online backup. Imported Linux v11 cookies also require Chrome's real OS password store or keychain; Patchright disables its incompatible automation defaults for this reason.

Validation and identity failures stop startup rather than silently using stale cookies. Correct the reported source, profile, browser-family, or encryption mismatch, then retry the explicit import while the destination is inactive.

## Reset configuration

Only reset when the user intends to disable future imports:

```bash
surf-agent profile cookie-source reset
```

Resetting configuration does not remove cookies already present in the Surf profile.
