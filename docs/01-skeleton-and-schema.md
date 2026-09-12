# 01 — Skeleton, docker-compose, Alembic, schema, domain normalisation

Build order step 1. First commit (`291017b`).

## What was built

- `pyproject.toml` via `uv`, Python 3.12 (pinned in `.python-version`),
  the full stack from PROJECT.md: FastAPI, SQLAlchemy 2.0, psycopg3,
  Alembic, Redis, RQ, httpx, selectolax, pytest.
- `docker-compose.yml`: Postgres 16 + Redis 7, named volumes,
  healthchecks on both.
- Alembic initialised and wired to `leadgen.db.models.Base.metadata`;
  `env.py` reads `DATABASE_URL` from the environment via `python-dotenv`.
- `src/leadgen/{config,discover,enrich,compose,send,db,api,util}` — empty
  packages, `src/` layout, installed editable via hatchling.
- `src/leadgen/db/models.py` — SQLAlchemy models for all tables in
  PROJECT.md's data model section (plus one join table — see below).
- `src/leadgen/util/domains.py` — `normalise_domain()`.
- `tests/test_domains.py` — 31 cases: www-stripping, case, trailing
  dots, paths, query strings, non-www subdomains, IDN, invalid input.
  All passing.
- `.env.example`, `.gitignore`.

## Schema disagreements with PROJECT.md, and what was done instead

PROJECT.md's data-model table was treated as a starting point, not a
literal spec, because a few things in it don't hold together:

1. **`businesses` unique constraints had no precedence.** "Unique on
   normalised domain" and "unique on `(source, source_id)`" side by side
   doesn't say what happens when two sources surface the same domain
   under different `source_id`s, or when `must_have_website: false`
   leaves `normalized_domain` NULL. Resolution: `normalized_domain` got
   a **partial** unique index (`WHERE normalized_domain IS NOT NULL`);
   `(source, source_id)` stays unique per-source. Cross-source merge-by-
   domain is explicitly *not* solved at the schema level — it's an
   open problem for the discover stage (step 3).
2. **`mailboxes.daily_counter` (as literally described) is a footgun.**
   A mutable counter column needs a correctly-timed, exactly-once daily
   reset, or the 50/day send cap — the entire point of this system —
   silently breaks. Dropped the counter column entirely; the send stage
   will derive today's count from `messages.sent_at` at query time,
   which can't drift out of sync with what was actually sent.
3. **`messages` was missing columns the Hard Rules section itself
   requires**, even though the data-model table only lists `status`.
   Added: rendered `subject`/`body` (so an approved message can't
   silently change if a template edits later), `approved_by`/
   `approved_at` (the mandatory human-approval gate), `gmail_message_id`/
   `gmail_thread_id` (needed for stage 7 bounce/reply correlation — no
   other way to tie a bounce back to the message that caused it).
4. **`contacts` was likewise missing required provenance columns.**
   Hard Rules says every contact row records source, fetched_at, and
   legal basis; the data-model table didn't list them. Added `source`,
   `fetched_at`, `legal_basis`.
5. **`suppressions` modeled as one polymorphic table, not two nullable
   columns.** `scope` (`'email'` | `'domain'`) + `value`, unique on
   `(scope, value)`. Matches the actual lookup pattern (check email OR
   check domain) as a single indexed query shape.
6. **Google Places' 30-day retention rule has a schema consequence the
   doc didn't spell out.** Added `businesses.source_raw_expires_at`
   (nullable, only meaningful when `source='places'`) so a future purge
   job has something to query against. No purge job exists yet — this
   is a placeholder for when Places becomes a live source, not a
   solved problem.
7. **`sender_pool` is a list in the target-profile YAML but there was no
   table for it.** Added `campaign_mailboxes`, a join table — a
   structural necessity for the many-to-many, not a scope addition.
8. Minor: `enrichment_signals.value` is `JSONB` (signal values are
   mixed bool/number/string); `target_runs` stores the full resolved
   profile YAML text, not just its hash (a hash alone can't reproduce a
   run if `business_types.yaml` or an offer file drifts independently
   of the profile file); all primary keys are UUID (Python-side
   `uuid.uuid4`, no Postgres extension needed) since this handles PII
   and will eventually sit behind an API.

These are recorded in more detail as docstrings in `db/models.py` and in
CLAUDE.md, so they don't get silently reverted later.

## Verification

- `uv run alembic revision --autogenerate -m "initial schema"` against
  the live compose Postgres produced the migration with no manual
  patching needed — CHECK constraints, the partial unique index, and
  cascade/restrict FKs all came through correctly on the first try.
- `alembic upgrade head` → `alembic downgrade base` → `alembic upgrade
  head` round-tripped cleanly.
- `alembic check` confirmed zero drift between `db/models.py` and the
  applied migration.
- `docker compose exec postgres psql ... \dt` confirmed all 10 tables
  exist.
- `uv run pytest`: 31/31 passing.

## Environment note

This network has a slow ramp-up on large downloads (a ~45s stall before
throughput picks up), which made `uv python install 3.12` and the first
`uv sync` fail against `uv`'s default HTTP timeout. Fixed by retrying
with `UV_HTTP_TIMEOUT=240`. Worth remembering if a fresh clone or CI run
hits the same thing.

Docker Desktop also wasn't running at the start of this step and had to
be launched (`open -a Docker`) before `docker compose up -d` would work.
