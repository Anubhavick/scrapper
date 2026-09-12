# CLAUDE.md

Guidance for Claude Code sessions working in this repo. Read PROJECT.md
first — it's the actual spec (data model, target-profile format, hard
rules, legal posture, build order). This file is about *how to work in
this codebase*, not what it does.

## Current status

Steps 1–5 of PROJECT.md's build order are done — discover, enrich, and
CSV export form one runnable pipeline now. No compose / send logic
exists yet. What's real:

- `src/leadgen/{compose,send,api}` — still empty packages, right
  structure, no logic.
- `src/leadgen/db/models.py` — full SQLAlchemy schema, migrated.
- `src/leadgen/util/domains.py` — `normalise_domain()`, tested.
- `src/leadgen/config/{models,loader}.py` — Pydantic schemas + YAML
  loader for target profiles, business types, and offers.
- `src/leadgen/discover/{geocode,overpass,filters}.py` — Nominatim
  geocoding, Overpass query/run/parse (all 4 location modes), and
  discovery-time filtering (`must_have_website`, `exclude_domains`,
  etc.) applied before anything gets crawled.
- `src/leadgen/enrich/{robots,signals,crawler,qualify}.py` —
  robots.txt-aware rate-limited crawling, contact-email extraction
  (never guessed), the 8 enrichment signals, and qualification against
  a profile's rules. **`qualify.py` has a documented gap**: non-boolean
  signals (`last_content_year`, `page_weight_mb`) count as "matched"
  when truthy, not when their value indicates an actual problem —
  there's no threshold anywhere in PROJECT.md's spec, and inventing one
  wasn't a call to make unilaterally. See its module docstring and
  docs/05 before relying on qualification for real decisions.
- `src/leadgen/pipeline.py` — `run_target_profile()` / `export_csv()`:
  the actual discover→filter→crawl→qualify→CSV wiring. **Still no
  database** — this returns in-memory rows and writes a CSV file
  directly; nothing here (or anywhere yet) upserts into
  `businesses`/`contacts`/`enrichment_signals`/`crawl_cache`. That
  persistence layer is intentionally deferred past step 5.
- Postgres 16 + Redis via docker-compose, Alembic wired up (schema
  exists and is migrated, just not written to by any code yet).
- `docs/` has one file per completed build-order step — check there for
  the full reasoning behind any non-obvious decision before redoing it.
- **Known environment gap:** live network calls to Overpass/Nominatim
  hang indefinitely in the sandbox this was built in (see docs/03's
  Verification section) — the mocked test suite is solid, but nobody
  has confirmed this code reaches the real APIs from wherever it
  actually runs. Check that — and actually run `pipeline.py` against a
  real target profile and real website — before trusting this against
  production data, and before treating the `qualify.py` gap above as
  theoretical.

Next per PROJECT.md's build order: **read 200 rows by hand** — this is
a human task, not a build step. Once that's done and the data's judged
good enough, come back for step 6 (Gmail OAuth + send queue + caps +
suppression). Don't skip ahead to Gmail/send just because it's more
interesting to build than waiting on a hand-review.

## Commands

```bash
# one-time env setup
uv sync                          # installs into .venv, Python 3.12 pinned via .python-version
cp .env.example .env              # then fill in real secrets, never commit .env
docker compose up -d              # postgres:16 + redis:7, named volumes, healthchecks
uv run alembic upgrade head       # apply migrations

# day to day
uv run pytest                     # full test suite
uv run pytest -v tests/test_x.py  # one file
uv run alembic revision --autogenerate -m "..."   # after changing db/models.py
uv run alembic check              # verify no drift between models.py and the migrations
docker compose ps                 # confirm postgres/redis are healthy
```

If `uv sync` or `uv python install` times out downloading a Python
build, it's not blocked — this network has a slow start on large
transfers. Retry with `UV_HTTP_TIMEOUT=240 uv sync`.

## Architecture notes

- **`src/` layout**, package name `leadgen`, installed editable via
  hatchling (`pyproject.toml` → `[tool.hatch.build.targets.wheel]`).
  Import as `leadgen.db.models`, `leadgen.util.domains`, etc.
- **SQLAlchemy 2.0** declarative style (`Mapped[...]`, `mapped_column`),
  sync engine (psycopg3), not async. RQ workers and FastAPI both get a
  simpler mental model this way — don't introduce `asyncpg` or async
  SQLAlchemy sessions without a real reason to.
- **UUID primary keys** everywhere (Python-side `uuid.uuid4` default, no
  Postgres extension dependency). Chosen because this handles PII and
  will eventually sit behind an API — don't switch to bigint identity
  columns partway through.
- **Alembic env.py** reads `DATABASE_URL` from the environment (via
  `python-dotenv`) and overrides `sqlalchemy.url` — the placeholder in
  `alembic.ini` is never actually used. Point `DATABASE_URL` at whatever
  Postgres you're migrating, including a prod one, when the time comes.

## Schema decisions worth knowing before touching `db/models.py`

These were flagged explicitly when the schema was built — see the
original design discussion if you need the full reasoning. Short
version, so nobody re-litigates or accidentally reverts these:

- **`businesses.normalized_domain`** has a *partial* unique index
  (`WHERE normalized_domain IS NOT NULL`), because `must_have_website:
  false` profiles produce businesses with no domain. `(source,
  source_id)` is the other unique constraint, per-source. There is
  **no automatic cross-source merge** — if Overpass and Places surface
  the same business under different `source_id`s, that's an application-
  level dedupe problem for the discover stage to solve, not something
  the schema resolves for you.
- **`mailboxes` has no daily-counter column, on purpose.** The 50/day
  cap is this system's entire value proposition — a mutable counter that
  needs a correctly-timed reset job is a way to silently blow that cap.
  Compute today's count from `messages.sent_at` at query time. Do not
  add a counter column back in without solving the reset-timing problem
  first.
- **`businesses.source_raw_expires_at`** exists because Google Places
  content can't be persisted beyond 30 days (Maps Platform ToS) — only
  `place_id` is storable long-term. It's nullable and only meaningful
  when `source = 'places'`. There is no purge job yet; before Places
  becomes a live source, that job needs to exist.
- **`messages`** carries rendered `subject`/`body`, not just a template
  reference — so an approved message can't silently change if a
  template file is edited after approval but before send. It also
  carries `gmail_message_id`/`gmail_thread_id` for stage-7 bounce/reply
  correlation; don't send without populating them.
- **`suppressions`** is `(scope, value)` — `scope` is `'email'` or
  `'domain'`, not two separate nullable columns. Every send path checks
  both scopes before sending, forever. This check is non-negotiable per
  PROJECT.md's hard rules — never gate it behind a feature flag or a
  config option.

## Hard rules (from PROJECT.md, repeated here because it's easy to forget mid-implementation)

- Max 50 sends/mailbox/day, randomised 90–600s gaps, no bursts.
- Suppression check before **every** send, no exceptions.
- Every `contacts` row records `source`, `fetched_at`, `legal_basis`.
- `robots.txt` respected, 1 req/sec/host, real User-Agent with a contact
  URL, in the crawler.
- No email guessing — only addresses found on the business's own site or
  in OSM tags. No permutation, no SMTP probing.
- No message sends without a human clicking approve on the final
  rendered text.
- Secrets never in git, never in logs. OAuth refresh tokens encrypted at
  rest — `oauth_refresh_token_encrypted` is not optional-looking, it's
  the only form that column should ever hold.

## Conventions

- No comments explaining *what* code does — only *why*, when it's
  genuinely non-obvious (a legal constraint, a footgun avoided, a subtle
  invariant). The schema-decisions docstrings in `models.py` are the
  template for this.
- Config (target profiles, business types, offers) lives in YAML files,
  never in the database — don't add FK columns pointing at config
  concepts like `offer_id`; validate those at the application layer
  (Pydantic, per PROJECT.md step 2) instead.
