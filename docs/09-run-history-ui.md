# 09 — Run history UI (DB-backed review)

The next piece after docs/08's persistence: docs/07's lead-review UI only
ever read a CSV, which has no row identity to page back through — reading
last week's scrape meant still having last week's CSV file lying around.
This is what turns `target_runs`/`businesses`/`contacts` (docs/08) into
the "history of previous scans" a human can actually browse: pick a run,
see who it found, reject the same way as before.

## A schema gap this surfaced

Persistence (docs/08) upserts `businesses`/`contacts`/`enrichment_signals`
per run, but nothing recorded *which run found which business*, or that
run's own qualified/crawl_status/tags verdict for it — those three are
properties of a run's crawl, not of the business (the same business
re-scanned later can flip qualified, or a temporary `unreachable` can
heal). `target_runs.businesses_found` was just a count with nothing to
list back out. Added `target_run_businesses` (migration
`108df6a403d2`): one row per (run, business), carrying that run's
`qualified`/`crawl_status`/`tags`. `pipeline.py` now writes to it
alongside the existing upserts, right after building each `LeadRow` --
`db/persist.py` gained `record_run_business()` for this.

## What was built

- `src/leadgen/db/models.py` — `TargetRunBusiness`, see its docstring for
  why these fields don't belong on `Business` itself.
- `src/leadgen/db/persist.py` — `record_run_business()`.
- `src/leadgen/pipeline.py` — calls it for every business, website or not,
  right after computing that business's `LeadRow`.
- `src/leadgen/api/review.py` — two new routes:
  - `GET /runs` — every `target_runs` row, newest first, status badge,
    counts, link into each.
  - `GET /runs/{run_id}` — the same filterable table docs/07 built
    (qualified/crawl_status/name-search, Reject button), now reading
    `target_run_businesses` join `businesses`/`contacts` instead of a
    CSV. Reject still writes to a target profile's `exclude_domains` the
    same way -- profile path defaults to `targets/<target_name>.yaml`,
    overridable via `?profile=`.
  - Both routes share `_row_html`/`_filter_bar`/`_render_page` with the
    original CSV-based `/` unchanged -- `_business_row_dict()` is the
    only new piece, mapping DB objects into the same row-dict shape a
    CSV `DictReader` row already had, so every existing rendering/filter
    helper works on either source without modification.
  - `/` (CSV) and `/runs/{id}` (DB) now cross-link via a "← Run history"
    / back-link, but `/` still works completely standalone for `--no-db`
    runs -- it has no DB dependency added.

## Two real bugs a browser session caught (unit tests didn't)

Both from actually clicking through in Playwright against a real
Postgres-backed run, not from `TestClient` against SQLite fixtures (which
can't exercise these routes at all -- see Testing below):

1. **`DetachedInstanceError` on `/runs/{id}`.** The initial version built
   `_render_page(...)` using `run.target_name`/`run.started_at` *after*
   the `with session_scope() as session:` block had already closed and
   committed. SQLAlchemy expires attributes on commit, so reading them
   post-close tried to reload from a session that no longer existed —
   500, not a silent stale read. Fixed by extracting the two needed
   strings while the session was still open.
2. **Empty-string `Form(...)` fields are "missing", not empty.** The
   reject button's hidden `csv` field is `""` on `/runs/{id}` (there's no
   CSV to point at). FastAPI's `Form(...)` (required) rejected that as a
   422 `"missing"` field — confirmed with a bare `curl -F csv=`, not a
   browser quirk. `csv` was never actually read inside `reject()`'s body
   in the first place, so the fix was `Form("")` instead of `Form(...)`.
   Added `test_reject_works_with_empty_csv_field` as a regression test —
   this is exactly the kind of bug a CSV-only test fixture (always a real
   non-empty path) can't surface.

## What this deliberately doesn't touch

- **No pagination.** `/runs` and `/runs/{id}` load every row in one
  query. Fine at 31-run/31-business scale; revisit once a target profile
  routinely returns hundreds.
- **No delete/archive for old runs.** History only grows for now.
- **Still no message-approval UI or campaigns/messages rows** — this
  step is entirely about *discovery* history, not outreach. Unchanged
  from docs/08's framing of what's next.
- **The scan-builder form (turning a filled-in form into a target
  profile YAML) is a separate, not-yet-started piece** — this doc is
  only the second of the three UI pages discussed, per the build order
  the user chose (history/review first, then scan-builder, then
  campaigns).

## Verification

`uv run pytest`: 172/172 passing (170 prior + 2 new: the empty-`csv`
regression test and a pure-function test for `_business_row_dict`'s
row-shape). `/runs` and `/runs/{run_id}` themselves aren't covered by
the automated suite, same reasoning as `db/persist.py`/`db/repository.py`
— `TargetRunBusiness`'s JSONB `tags` column isn't SQLite-compatible.

Verified for real: ran `scripts/run_pipeline.py` against the widened
`dentists-austin-tx` profile a second time (Postgres already had one run
from docs/08, predating this table). `/runs` correctly listed both —
the older run showing 0 businesses at `/runs/{id}` (it has no
`target_run_businesses` rows, expected), the new run showing all 31 with
correct qualified/crawl_status/tags per row, including both real "River
Rock Dental" locations rendering distinctly (confirms docs/08's
`normalized_domain` collision fix holds through to display). Filtering
by `qualified=yes` correctly showed 6 of 31, matching the pipeline's own
printed count. Reject-from-`/runs/{id}` tested against a scratch copy of
the profile file (not the real `targets/dentists-austin-tx.yaml`, to
avoid polluting it with a test-only exclusion) — confirmed the domain was
appended, the notice banner rendered, and the redirect landed back on
`/runs/{id}` rather than `/`.
