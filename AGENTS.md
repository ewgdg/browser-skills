# browser-skills — notes for agents

`skills/surf/runtime-revision` pins the runtime both skills install (`packages/surf-agent` and `packages/surf-google-search`): the pin names the commit holding the runtime change, so write it in the same change as that edit, which makes the pin update the next commit.

- Pin the local SHA right after committing the runtime change, before pushing; do not wait for the push. Use the newest commit touching `packages/`: `git log -1 --format=%H -- packages`.
- Push the runtime commit and its pin together: a pinned SHA that is not on the remote cannot be installed.
