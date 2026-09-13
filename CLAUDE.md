# CLAUDE.md

Guidance for Claude Code sessions working in this repo. Read PROJECT.md
first — it's the actual spec (data model, target-profile format, hard
rules, legal posture, build order). This file is about *how to work in
this codebase*, not what it does.

## Current status

Steps 1–6 are done, including the orchestration loop (docs/12) — and
steps 3, 5, and 6 have now been verified against real external services,
not just mocks — see the Verification note in each of docs/03, docs/06,
docs/07, and docs/12. Step 7 (bounce/reply monitoring) is untouched.
Step 8 (FastAPI + review UI) is done in substance: lead-review, run
history, scan-builder, and campaigns + message-approval UIs all exist.

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
- `src/leadgen/api/review.py` — a FastAPI **lead-review + run-history**
  UI (docs/07, docs/09): `/` filters a pipeline CSV by
  qualified/crawl_status/name with a Reject button writing to a target
  profile's `exclude_domains` — no DB dependency, still works for
  `--no-db` runs. `/runs` lists every persisted `target_runs` row;
  `/runs/{id}` is the same filterable table read from
  `target_run_businesses`/`businesses`/`contacts` instead of a CSV — the
  actual "history of previous scans" view. Both share the same
  rendering/filter helpers via `_business_row_dict()`. Run with
  `uv run uvicorn leadgen.api.review:app --reload`.
- `src/leadgen/api/targets.py` — a FastAPI **scan-builder** UI (docs/10),
  mounted onto the same `app`: `/targets` lists every `targets/*.yaml`
  (parsed through the real loader, so a bad business_type/offer_id
  reference shows as "Invalid" inline instead of crashing the page);
  `/targets/new` (+ `POST /targets`) is a form that writes a new profile,
  validated by `TargetProfile.model_validate()` itself — the same model
  the loader uses — plus a business_type/offer_id existence check;
  `/targets/{name}` shows the raw YAML and the `run_pipeline.py` command
  to actually run it (doesn't trigger a scan itself — see below). Create-
  only: a name colliding with an existing file is rejected, never
  overwritten. `name` is sanitised as a filename
  (`^[a-z0-9][a-z0-9-]{0,62}$`) since it becomes one — this is what
  blocks path-traversal input, not just a "looks like a slug" nicety.
  `src/leadgen/api/nav.py` is the shared nav bar across all four pages.
- `src/leadgen/db/campaigns.py` + `src/leadgen/api/campaigns.py` — a
  FastAPI **campaigns + message-approval** UI (docs/11), mounted onto the
  same `app`. `create_campaign()` turns a completed `target_run` + an
  offer + a sender pool (real, authorized `mailboxes` rows only) into a
  `campaigns` row; `generate_campaign_messages()` renders one `queued`
  `messages` row per qualified business via `compose/render.py`
  (already built, untouched) — one message per **business**, not per
  contact, addressed to its best contact (`_select_best_contact()`
  prefers a named address over a generic `info@`-style one). `/campaigns`
  lists campaigns with a status breakdown; `/campaigns/new` creates one;
  `/campaigns/{id}` is the actual approval screen the hard rule "no send
  without a human clicking approve" needed — full rendered subject/body,
  editable while `queued` (`POST .../edit`, locked once approved — an
  edit request against an approved message is a silent no-op, verified),
  Approve/Reject per message, approving requires typing a name first
  (`approved_by` unaudited otherwise). **Sends nothing itself** — no
  route here calls `send/queue.py`/`send/gmail.py`; that's
  `scripts/send_approved_messages.py` (below, docs/12), a separate
  script, not a route in this module. Editing is manual text only, on
  purpose — see docs/11 for why an LLM-assisted rewrite isn't wired in
  yet (constrained rephrasing of the already-grounded line, never free
  drafting, is the intended shape if it's ever built).
- `src/leadgen/send/orchestrator.py` + `src/leadgen/db/orchestration.py`
  + `scripts/send_approved_messages.py` (docs/12) — the orchestration
  loop. `run_orchestration_loop()` (pure, tested) groups `approved`
  messages by mailbox and drives each through `send/queue.py`'s
  `send_next()` in turn; `db/orchestration.py` builds the jobs from
  Postgres and implements the callables against a real Mailbox/Message/
  Contact, including the `reserve_send_slot`-based reservation (mark the
  message `sent` inside the advisory-locked transaction, before the
  Gmail call — see docs/12 for why). The CLI script defaults to a
  genuinely read-only preview (`preview_approved_messages()` — zero
  writes); `--dry-run` runs the full loop for real (real reservation
  writes, faked Gmail call — disposable test data only, never a real
  campaign); `--live` plus typing back a confirmation phrase actually
  sends. **The "dry run" default was itself a caught bug** (docs/12): the
  first version defaulted to what's now `--dry-run`, which silently
  flips every eligible message to `sent` even with no Gmail call —
  "fakes the network call" isn't "makes no writes." Caught before any
  real use, fixed by making the true no-op the default. **Also fixed a
  real bug in `send/queue.py` while building this:** `send_next()` used to evaluate `sent_today_fn()` before
  confirming suppression (Python evaluates keyword-argument expressions
  eagerly), which would have let a side-effecting reservation run for a
  suppressed contact before `check_sendable` ever raised for it. Fixed
  by checking suppression first, inline, and only calling
  `sent_today_fn()` once it's clear; `SendBlocked` also gained a
  `reason` attribute (`"suppressed"` | `"cap"`) so a caller can tell a
  per-message block from a per-mailbox one without parsing text.
  Verified in dry-run mode against real Postgres (docs/12); never yet
  run `--live`.
- **Still not built, on purpose:** the scan-builder page doesn't trigger a
  scan (real Overpass + per-business HTTP calls can take minutes —
  running that synchronously in a request handler is a browser-timeout
  footgun); bounce/reply monitoring (step 7, needs restricted
  `gmail.readonly`/`gmail.modify` scopes + CASA) is untouched; the
  orchestration script has no preflight token-health check yet (docs/12).
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
  to the old behaviour. **Real bug worth knowing:** `businesses.normalized_domain`'s partial
  unique index breaks on a real multi-location chain sharing one domain
  (two actual "River Rock Dental" branches in the Austin data) —
  `upsert_business()` now leaves the second one's `normalized_domain`
  null rather than crash the run; see docs/08 for why and its accepted
  nondeterminism (which location "wins" the slot depends on discover()
  row order). Also writes one `target_run_businesses` row per business
  (docs/09) with that run's own qualified/crawl_status/tags — a run's
  verdict on a business, not the business's own attribute, since a
  re-scan can change it.
- Postgres 16 + Redis via docker-compose, Alembic wired up and actually
  written to now (docs/08, docs/09, docs/11) — `businesses`, `contacts`,
  `enrichment_signals`, `target_runs`, `target_run_businesses`,
  `campaigns`, `campaign_mailboxes`, `messages`. `mailboxes` is written to
  separately by `scripts/authorize_mailbox.py` (HOWTO.md). `suppressions`
  is migrated but still unwritten — needs stage 7's bounce/reply handling
  or a manual insert.
- `docs/` has one file per completed build-order step — check there for
  the full reasoning behind any non-obvious decision before redoing it.

Next concrete step: **run `scripts/send_approved_messages.py --live`
against a real approved campaign** — everything up to this is built and
verified. This is real, external, hard-to-reverse behavior (real email
to real business owners) and must not happen without the user explicitly
confirming it first, on top of the script's own `--live` + typed-
confirmation gate; do not run it yourself without that confirmation.

Full backlog after that, in priority order, with reasoning: **[ROADMAP.md](ROADMAP.md)**
— keep it updated as items ship or new ones are found, don't just leave
status in chat history.

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
