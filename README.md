# Ricette di Nonna Aua — special-barnacle

FastAPI serves the recipe pages, Google OAuth login, and an authenticated editing
API. Recipes remain JSON files in a separate private GitHub repository. The backend
uses GitHub's REST API; it does not clone a repository or run Git/SSH processes.

## Configuration

Copy `.env.example` to `.env` and fill in:

- `REPO`: data repository, e.g. `mmilanta/recipe-data`.
- `GITHUB_BRANCH`: existing writable branch, default `main`.
- `GITHUB_PAT`: fine-grained personal access token restricted to the data repository,
  with **Contents: read and write**. Keep it in a server-side secret (e.g. Cloud Run
  Secret Manager); it must never be sent to the browser.
- `SECRET_KEY`: a strong, stable secret for signed login sessions.
- `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`: existing Google OAuth credentials.

The token owner must be able to commit to the selected branch. Branch protection
requiring pull requests will reject direct writes. GitHub uses the token owner as committer. Saves and deletes set the commit author
to the signed-in writer’s Google name and email, derived on the server.

The data repository layout is unchanged: root files `recipe:<id>.json`,
`valid_categories.json`, `superusers.json`, and any existing `user:<id>.json` files.
No data migration is needed.

## Storage behavior

- The first data read downloads a branch ZIP snapshot through GitHub's archive API.
  Root JSON files are read into memory without extraction or local persistence.
  This avoids a GitHub API request per recipe when building the index.
- The cache expires after 60 seconds and refreshes on the next request. Direct
  repository edits can take up to 60 seconds to appear. Failed refreshes return an
  error rather than extending the cache's lifetime.
- Each create, edit, or delete waits for GitHub's Contents API to confirm its commit
  before returning success. There are no background writes or shutdown flushes.
- Mutations are serialized within the process. Updates/deletions include the file's
  blob SHA; stale versions return HTTP 409 rather than overwriting another edit.
  GitHub also checks the SHA if another instance or an external editor changes it.
  Conflicts are surfaced, not automatically overwritten or force-pushed.
- Failed saves leave form contents in place. A timeout may mean GitHub committed
  the change but its response was lost; open the recipe in another tab and compare
  before retrying. The create form retains its ID on retries to avoid duplicates.

API clients must retain the `version` returned by recipe GET/PUT responses. Send
it in the JSON body when updating and as `?version=<sha>` when deleting. Omit it
(or use `null`) only when creating a new recipe. Version metadata is not written
into the recipe JSON files.

GitHub API reference: https://docs.github.com/en/rest/repos/contents

## Recipe history

Every recipe page shows a small last-edited date in `dd/mm/yyyy` format, based on
its latest GitHub commit (UTC date). Only this date is public; editor names and
version links are restricted to signed-in accounts in `superusers.json`.

Writers can open **Cronologia**, browse all versions in pages of 20, and open a
read-only recipe snapshot at each commit. Deleted versions are identified, and
old snapshots cannot be edited or deleted from that view. Each revision with a
predecessor offers “prec”; older revisions also offer “curr”. “apri” opens the
snapshot. Diffs include every field, show unchanged content once, and highlight
removed/added lines by recipe field. Diffs escape historical HTML and require the
same writer permissions. Pagination is anchored
to the first version so concurrent edits do not shift the list between pages.
History follows the recipe file path; manual Git renames are not followed.

The server rechecks the writer whitelist for history and mutations, using the
existing data cache (up to 60 seconds). Private history pages use `Cache-Control:
private, no-store`. New saves/deletions record the signed-in writer as commit
author. Older commits retain their recorded author, which may be a shared service
account; missing past identities cannot be reconstructed.

History metadata has a bounded 60-second in-memory cache, invalidated on mutation.
It is fetched on recipe pages, not for every recipe on the homepage. A history
API outage leaves available recipe content visible and omits the unavailable
date. The existing Contents read/write token permissions are sufficient.

## Run locally

```sh
python -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python src/run.py
```

Or run `docker compose up --build` (port 8000). The production container runs one
Uvicorn process without the development reloader.

## Deploying the storage change

Before directing traffic to the new image, configure `REPO`, `GITHUB_BRANCH`, and
a valid `GITHUB_PAT` on the Cloud Run service. Preserve the existing OAuth and
session settings. Scale-to-zero and max-one-instance settings can stay as they are.
Ensure the previous version has pushed its pending edits before replacing it.

The following settings are obsolete and can be removed: `LOCAL_REPO_FOLDER`,
`PUSH_EVERY_N_SECONDS`, `REPO_URL`, `PRIVATE_KEY`, `USER_NAME`, `USER_EMAIL`,
`SKIP_REMOTE_CONNECTION`, `GIT_STORE_HOST`, `GIT_STORE_PORT`, and `GITHUB_PAT` URL
interpolation. No SSH key or local data volume is needed.

## Tests

```sh
.venv/bin/python -m unittest discover -s tests -v
node tests/test_search.cjs
```

Tests simulate GitHub at the HTTP boundary and cover caching/redirects, JSON
round trips, create/update/delete, stale and concurrent writes, GitHub failures,
authorization, version validation, rendered pages, and optional notes. The Node
tests cover live search, accent handling, empty results, and clearing the filter.
Tests do not touch live data.

## Rebuild CSS from Tailwind

```sh
cd src/tailwindcss
npx tailwindcss -i ./styles/app.css -o ../static/css/app.css --watch
```

## Typography

Libre Baskerville is served locally in regular and italic variable WOFF2 files,
including weights 400–700. See `src/static/fonts/libre-baskerville/README.md` for
sources and `OFL.txt` for its license. No Google Fonts requests are made at runtime.
