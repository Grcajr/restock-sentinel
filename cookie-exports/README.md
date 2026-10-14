# cookie-exports/

Drop every Cookie-Editor JSON export in here, straight after you export it
from the browser extension. This whole folder is gitignored (see
`.gitignore`) -- nothing in it will ever be committed or pushed, which is
the point: these files are live session cookies, basically as good as being
logged into the account.

## Naming convention

`<retailer>_<account>.json`

Examples:
- `walmart_mine.json`
- `target_mine.json`
- `walmart_wife.json`

## Using one

```bash
restock-sentinel import-cookies --account mine --file cookie-exports/target_mine.json
```

`--account` is just the label you want this session saved under (matches
whatever you pass to `--account` later with `setup-session`-based commands
too) -- it doesn't have to match the filename, but keeping them matching
makes it easy to tell at a glance which file is which.

## Why this file never gets touched remotely

Claude never reads the contents of anything in this folder, even when
helping edit the project over the remote bridge -- only you move files in
here and only you point `import-cookies` at them locally. That's
deliberate: a session cookie is a live credential, same tier as a password,
and it should never pass through chat, a screenshot, or Claude's own
sandbox.
