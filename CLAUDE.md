# CLAUDE.md

Guidance for Claude Code sessions working in this repo. Read PROJECT.md
first — it's the actual spec (data model, target-profile format, hard
rules, legal posture, build order). This file is about *how to work in
this codebase*, not what it does.

## Current status

Steps 1–6 are done and steps 3, 5, and 6 have now been verified against
real external services, not just mocks — see the Verification note in
each of docs/03, docs/06, and docs/07. Step 7 (bounce/reply monitoring)
is untouched. Step 8 (FastAPI + review UI) is **partially** done: a
lead-review UI exists (docs/07), a message-approval UI does not.

- **Real, verified end-to-end:** `scripts/run_pipeline.py` against
  `targets/dentists-austin-tx.yaml` reaches live Overpass/Nominatim and
  real business websites (docs/03's reachability gap is closed).
  `scripts/authorize_mailbox.py` + `scripts/send_test_email.py` have
  authorized a real Gmail account and sent a real message through the
  live Gmail API (docs/06, HOWTO.md). The **200-rows-by-hand checkpoint**
  PROJECT.md calls for after step 5 has now happened, at smaller volume
  (31 real rows, not 200) — see docs/07 for what that review found and
  fixed. Worth doing again at real volume before trusting qualification
  broadly across other business types/cities.
- `src/leadgen/compose/render.py` — `render_message()`: offer template +
  one generated line grounded in a real boolean signal. Raises if none of
  the offer's `relevant_signals` were truthy — treated as an upstream
  qualification bug, not something to paper over.
- `src/leadgen/send/{crypto,oauth,gmail,caps,suppression,queue}.py` —
  refresh-token encryption (Fernet), the Gmail OAuth authorization-code
  flow (`gmail.send` scope only), MIME message building +
  `users.messages.send`, and the pure decision logic for daily caps +
  suppression + the 90–600s randomised gap (`check_sendable()` →
  `SendBlocked`). Decision logic is covered by mocked-`httpx`/pure-function
  tests; the Gmail send path itself has also been run for real (above).
- `src/leadgen/db/{session,repository}.py` — engine/sessionmaker setup and
  the two actual Postgres queries (`count_sent_today`,
  `fetch_suppressions`) behind the caps/suppression decisions above.
  **Not covered by the test suite** — `db/models.py`'s Postgres-specific
  `JSONB`/`UUID` types don't work against SQLite, so these need a real
  migrated Postgres to verify (`docker compose up -d` + `alembic upgrade
  head`) — still unverified as of this writing.
- `src/leadgen/api/review.py` — a FastAPI **lead-review** UI (docs/07):
  filters a pipeline CSV by qualified/crawl_status/name, and a Reject
  button that writes to a target profile's `exclude_domains`. Run with
  `uv run uvicorn leadgen.api.review:app --reload`. This is *not* the
  message-approval UI the hard rule "no send without a human clicking
  approve" needs — that needs `campaigns`/`messages` rows to review,
  which nothing creates yet (see the orchestration-loop gap below).
- **Still not built, on purpose:** nothing turns a CSV of qualified leads
  into `campaigns`/`messages` rows; nothing orchestrates reading approved
  messages and actually calling `send/queue.py` + `send/gmail.py` against
  them; no message-approval UI (needs the above to exist first);
  bounce/reply monitoring (step 7, needs restricted
  `gmail.readonly`/`gmail.modify` scopes + CASA) is untouched.
- `src/leadgen/db/models.py` — full SQLAlchemy schema, migrated.
- `src/leadgen/util/domains.py` — `normalise_domain()`, tested.
- `src/leadgen/config/{models,loader}.py` — Pydantic schemas + YAML
  loader for target profiles, business types, and offers.
- `src/leadgen/discover/{geocode,overpass,filters}.py` — Nominatim
  geocoding, Overpass query/run/parse (all 4 location modes), and
  discovery-time filtering (`must_have_website`, `exclude_domains`,
  etc.) applied before anything gets crawled. `exclude_domains` is also
  the mechanism for excluding a business OSM mistags (see docs/07).
- `src/leadgen/enrich/{robots,signals,crawler,qualify}.py` —
  robots.txt-aware rate-limited crawling, contact-email extraction
  (never guessed), the 8 enrichment signals, and qualification against
  a profile's rules. Non-boolean signals (`last_content_year`,
  `page_weight_mb`) only count as a real problem when a profile opts in
  via `qualification.stale_content_before_year` /
  `qualification.max_page_weight_mb` — a profile that doesn't set these
  gets the old truthy-counts-as-matched behaviour unchanged. **Known
  remaining issue:** `last_content_year` itself (in `signals.py`, not
  `qualify.py`) is computed by regexing the *entire page text* for the
  largest 4-digit year found — it reliably picks up a "© 2026" footer as
  "last content," which is noise, not staleness. Not yet fixed; see
  docs/07.
- `src/leadgen/pipeline.py` — `run_target_profile()` / `export_csv()`:
  the actual discover→filter→crawl→qualify→CSV wiring, now also emitting
  a `crawl_status` column (`ok`/`partial`/`unreachable`/`no_website`,
  docs/07) so an unreachable site is distinguishable from one with
  genuinely empty signals. **Now optionally persists** (docs/08): pass
  `session=`/`target_name=`/`profile_yaml_text=` and it upserts
  `businesses`/`contacts`/`enrichment_signals` and creates/finishes a
  `target_runs` row as it goes, via `src/leadgen/db/persist.py`. Without
  a session it's unchanged — CSV-only, no DB dependency.
  `scripts/run_pipeline.py` persists by default now; `--no-db` reverts
  to the old behaviour. `campaigns`/`messages` are still untouched —
  nothing yet turns a `target_run` into something a human approves.
  **Real bug worth knowing:** `businesses.normalized_domain`'s partial
  unique index breaks on a real multi-location chain sharing one domain
  (two actual "River Rock Dental" branches in the Austin data) —
  `upsert_business()` now leaves the second one's `normalized_domain`
  null rather than crash the run; see docs/08 for why and its accepted
  nondeterminism (which location "wins" the slot depends on discover()
  row order).
- Postgres 16 + Redis via docker-compose, Alembic wired up (schema
  exists and is migrated, just not written to by any code yet).
- `docs/` has one file per completed build-order step — check there for
  the full reasoning behind any non-obvious decision before redoing it.

Next concrete step: campaigns. Turn a `target_run` into a `campaigns` row
(offer + sender pool), generate `queued` `messages` rows for its
qualified contacts via `compose/render.py` (already built), extend the
review UI (or a new page) to let a human drop leads from the campaign and
approve each rendered message, then the orchestration loop calling
`send/queue.py` + `send/gmail.py` against approved ones. The hard rule
about human approval has nothing to click until the first two pieces of
that exist.

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
