# 08 — Wiring the pipeline to Postgres

Not a numbered PROJECT.md build-order step on its own — it's the
persistence layer steps 1 and 5 both left for later ("intentionally
deferred past step 5," CLAUDE.md). It became necessary now because two
things asked for next (a run-history view, and a campaign a human can
edit and re-run) both need real `businesses`/`contacts`/
`enrichment_signals`/`target_runs` rows to point at — a CSV has no
identity to reference.

## What was built

- `src/leadgen/db/persist.py` — `upsert_business()`, `upsert_contacts()`,
  `upsert_signals()`, `create_target_run()`, `finish_target_run()`. Dedup
  key for businesses is `(source, source_id)`, matching the schema's own
  documented dedup key — not `normalized_domain`, which is a uniqueness
  constraint, not an identity key (see the real bug below).
- `src/leadgen/pipeline.py`'s `run_target_profile()` gained optional
  `session` / `target_name` / `profile_yaml_text` parameters. Without a
  session it behaves exactly as before (in-memory rows, CSV only, zero
  DB dependency) — existing tests pass unchanged. With one, it creates a
  `target_runs` row up front, upserts each business/contact/signal as it
  crawls, and marks the run `completed` or `failed` (with the error
  message) at the end.
- `scripts/run_pipeline.py` now persists by default; `--no-db` opts back
  out to the old CSV-only behaviour.

## A real bug a real Postgres caught immediately

First live run against the widened (15km) Austin profile crashed:

```
psycopg.errors.UniqueViolation: duplicate key value violates unique
constraint "uq_businesses_normalized_domain"
DETAIL: Key (normalized_domain)=(riverrockdentalfamily.com) already exists.
```

Real data, not a hypothetical: this Austin scrape contains two actual
River Rock Dental locations (`/locations/mueller/` and
`/locations/river-rock-east-riverside/`), two distinct OSM nodes with two
distinct `source_id`s, sharing one corporate domain. CLAUDE.md's own
schema-decisions note on `normalized_domain`'s partial unique index calls
out *why* it's partial (nullable for `must_have_website: false` profiles)
but doesn't anticipate this case: a legitimate multi-location business
whose two locations are two different `businesses` rows by design (they
have different addresses, phones, OSM ids) but one shared domain.

Fix, in `upsert_business()`: before assigning `normalized_domain`, check
whether another business (different `source_id`) already holds it. If
so, this row gets `normalized_domain = None` instead — `website_url` is
untouched, only the domain-uniqueness slot is skipped. Whichever
location's `source_id` gets upserted first in a given run keeps the slot;
which one that is depends on `discover()`'s row order, not guaranteed
stable. Documented as an accepted limitation, not fixed further — the
schema's own note already frames cross-business dedup as "an
application-level problem for the discover stage to solve," not
something to force a bigger schema change over on the first real
collision found. If Places (or a merge job) is added later, this is the
function that logic extends, not a new one.

## What this deliberately doesn't touch

- **`CrawlCache` is unused.** Nothing writes fetched HTML there yet — it
  exists in the schema for re-crawl caching during development, not
  needed by this step.
- **No purge job for `source_raw_expires_at`.** Irrelevant until Places
  is a live source (`source='overpass'` rows never set it).
- **No cross-source merge logic** beyond the collision handling above —
  still explicitly out of scope, per CLAUDE.md.
- **`campaigns`/`messages` are still untouched.** This step only feeds
  `target_runs`/`businesses`/`contacts`/`enrichment_signals`; turning a
  `target_run` into a `campaign` a human can edit and approve is the next
  step, not this one.

## Verification

`uv run pytest`: 170/170 passing, unchanged — no test exercises the new
`session=` path (same reasoning as `db/repository.py`: JSONB/UUID columns
aren't SQLite-compatible, so this needs real Postgres to test
meaningfully, not a mock).

Run for real against `docker compose up -d` + `alembic upgrade head`
(migrations already current, no drift) on the widened
`dentists-austin-tx` profile: 31 businesses, 18 contacts, 200
enrichment-signal rows persisted, one `target_runs` row transitioned
`running` → `completed` with `businesses_found = 31`. Confirmed via
`psql` directly, not just application-side logging. The first run (before
the collision fix) failed cleanly — `session_scope()`'s single
transaction rolled back the entire run on the exception, leaving zero
partial rows behind, confirmed via `psql` before retrying.
