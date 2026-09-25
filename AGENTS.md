# browser-skills — notes for agents

`skills/surf/runtime-revision` pins the runtime both skills install (`packages/surf-agent` and `packages/surf-google-search`): the pin names the commit holding the runtime change, so write it in the same change as that edit, which makes the pin update the next commit.

- Pin the local SHA right after committing the runtime change, before pushing; do not wait for the push. Use the newest commit touching `packages/`: `git log -1 --format=%H -- packages`.
- Push the runtime commit and its pin together: a pinned SHA that is not on the remote cannot be installed.

## Live Google checks

Google challenges the Surf profile when searches arrive in bursts (about 60 page loads within minutes triggered a captcha), which blocks every search until a human solves it.

- Verify live with as few searches as possible, run one at a time: the live smoke test (`SURF_GOOGLE_SEARCH_LIVE=1`) or a single CLI search; the CLI's own pacing spaces its page loads.
- Do not loop over queries or reload pages in bulk to probe or benchmark; measure against DOM fixtures instead, or capture a live page once and run all comparisons on that same load.
