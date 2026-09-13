# CLAUDE.md

Guidance for Claude Code sessions working in this repo. Read PROJECT.md
first — it's the actual spec (data model, target-profile format, hard
rules, legal posture, build order). This file is about *how to work in
this codebase*, not what it does.

## Current status

Steps 1–6 are done, including the orchestration loop (docs/12) and a way
to trigger it from the campaigns UI via a background RQ job instead of a
terminal (docs/13) — **and a real send has actually happened** (2026-09-13,
a real approved message to a real Austin dentist practice, real
`gmail_message_id` recorded — see ROADMAP.md's status section). Steps 3,
5, and 6 have been verified against real external services, not just
mocks — see the Verification note in each of docs/03, docs/06, docs/07,
docs/12, docs/13, docs/14, docs/15, docs/16, docs/17, docs/18, and docs/19. Step 7
(bounce/reply monitoring) is untouched and deliberately deferred
(2026-09-13, see ROADMAP.md), though a manual suppression-list UI exists
now (docs/14) as the stopgap until it does. Step 8 (FastAPI + review UI)
is done in substance: lead-review, run history, scan-builder, campaigns +
message-approval, suppressions, and mailbox-health UIs all exist, and the
whole thing now sits behind HTTP Basic Auth (docs/17) instead of being
open to anyone who can reach it.

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
  run `--live`. **docs/16**: `run_orchestration_loop()` gained an
  optional `token_health_fn(mailbox_id)`, called once per mailbox before
  that mailbox's queue starts; `db/orchestration.py` implements it with
  `send/oauth.py`'s `validate_token_health()`, cached per mailbox the
  same way its per-mailbox access-token refresh already is. An unhealthy
  mailbox has every one of its jobs blocked immediately
  (`reason="token"`) instead of failing per message after each one's own
  90-600s sleep. Verified against real Postgres and a real Google
  `invalid_grant` rejection using a disposable mailbox with a garbage
  refresh token.
- `src/leadgen/queue.py` + `src/leadgen/jobs.py` + `api/campaigns.py`'s
  Send section (docs/13) — sending from the campaigns UI instead of a
  terminal. `queue.py`'s `get_queue()` is an RQ `Queue` bound to
  `REDIS_URL` (`rq`/`redis` were already dependencies, Redis already ran
  via docker-compose — unused until now); `jobs.py`'s
  `send_campaign_messages_job(campaign_id, live)` is the RQ job body
  (plain picklable args only — opens its own session/client, same as the
  CLI script) and now holds the single `CONFIRMATION_PHRASE` both the
  CLI and the UI form import. `db/orchestration.py`'s three functions
  all gained an optional `campaign_id` filter so the UI only ever acts
  on one campaign. `/campaigns/{id}` shows a read-only preview, or (if
  nothing's running) a confirm-phrase-gated "Start sending" button that
  enqueues the job with a deterministic id (`send-campaign-<id>`, so a
  second click while one's running is refused) — **only ever `live=True`,
  the UI never exposes `--dry-run`'s test-only mode.** While a send runs,
  the page shows that and auto-refreshes every 15s; progress is just the
  messages table below updating live, no separate tracking. **Two more
  real bugs found and fixed building this:** (1) `_reserve_fn` now
  re-checks `message.status == "approved"` under the advisory lock
  before reserving — without it, two overlapping runs over the same
  mailbox (much easier to trigger from a browser than a terminal — two
  tabs, an impatient second click) could both reserve and actually send
  the same message twice. (2) `send/orchestrator.py`'s broad `except
  Exception` (added in docs/12) was silently swallowing `rq`'s own
  `JobTimeoutException` — it subclasses `Exception`, not `BaseException`
  — letting a timed-out job report "success" and keep running instead of
  actually stopping; fixed with a soft `rq` import that re-raises it
  instead, covered by a new pure test. Also hit (and documented in
  HOWTO.md) a macOS-only `rq worker` fork-safety crash, unrelated to this
  codebase — set `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES` if it happens.
  Verified against real Postgres + Redis with `live=False` (RQ `--burst`
  worker, including the real 90-600s sleep running uninterrupted); not
  yet clicked through in an actual browser.
- `src/leadgen/api/auth.py` (docs/17) — `require_auth`, an HTTP Basic
  Auth FastAPI dependency checking one shared `WEB_UI_USERNAME`/
  `WEB_UI_PASSWORD` credential (env, plaintext like the other secrets),
  `secrets.compare_digest` for both fields. Wired at `api/review.py`'s
  `FastAPI(dependencies=[Depends(require_auth)])`, so it covers every
  route on the shared app in one place, not per-router. One shared
  credential, not per-user accounts, on purpose (docs/17) — per-user
  identity already lives elsewhere in this system (typing a name to
  approve a message, docs/11) and has nothing to do with who's logged
  into the browser. **A real bug caught only by hitting the actual
  running server:** the first version never called `load_dotenv()`
  before reading the env vars, unlike every other module that reads a
  required env var (`db/session.py`, `queue.py`, `jobs.py`) — `uv run`
  does not auto-load `.env`, so a request to a page that touches no
  database (nothing else had loaded `.env` into that process yet) 500'd
  even with correct credentials and a fully populated `.env`. Fixed by
  adding the same lazy `load_dotenv()` call `db/session.py`'s
  `get_engine()` makes. `scripts/dev.sh` now also refuses to start if
  `WEB_UI_USERNAME`/`WEB_UI_PASSWORD` aren't set in `.env` — without
  that check, its own readiness probe (`curl`, which doesn't fail on a
  non-2xx response) would print "Ready" while every page 500s.
- `src/leadgen/api/suppressions.py` (docs/14) — `/suppressions`: list +
  create-only add form for `suppressions`, the manual lever for a reply
  asking to stop contact before step 7's automatic bounce/reply
  monitoring exists. `value` normalised exactly how `send/suppression.py`
  reads it at real send time (`_normalise_email()` for `scope=email`,
  the existing `util/domains.normalise_domain()` for `scope=domain`);
  duplicate `(scope, value)` rejected with a friendly message, checked
  before insert. No delete/edit route — a suppression is meant to be a
  permanent record, same reasoning as target profiles being create-only
  (docs/10). Caught a second instance of docs/09's `Form(...)`-treats-
  empty-string-as-missing bug while verifying against the live server
  (`reason` submitted empty raised a raw 422 instead of reaching this
  route's own validation); fixed the same way, `Form("")` + manual
  emptiness check. Verified against real Postgres via direct HTTP
  requests: add, normalise, duplicate-reject, malformed-input-reject,
  all confirmed live.
- `src/leadgen/api/mailboxes.py` (docs/18) — `/mailboxes`, read-only:
  per-mailbox live token-health (a real, uncached call to Google's token
  endpoint on every page load — `send/oauth.py`'s
  `validate_token_health()`, already built for docs/16's preflight
  check, just newly surfaced here), sent-today vs. `daily_cap`, and the
  last real send error (if any). `Mailbox` gained
  `last_send_error`/`last_send_error_at` (nullable, point-in-time —
  **not** a history log or counter needing a reset job, the exact thing
  this file's schema-decisions section already warns against for this
  table); `db/orchestration.py`'s `on_sent`/`on_error` write them,
  gated on `not dry_run` since a faked Gmail call can't say anything
  real about mailbox health. This is a deliberately scoped-down answer
  to "mailbox reputation visibility" — a real spam/deliverability signal
  needs the same restricted-scope/CASA path step 7 is already deferred
  on (or Postmaster Tools, which needs a verified domain, not personal
  Gmail accounts); what's surfaced here is only what's actually knowable
  today. Verified against the real running server, including a
  disposable mailbox with a genuinely dead refresh token rendering
  correctly; the `on_sent`/`on_error` column-writing itself was **not**
  exercised via a real `--live` send (would require an actual send —
  this session does not trigger that on its own initiative) — see
  docs/18 for exactly what was and wasn't proven.
- **docs/19** — five smaller ROADMAP.md items in one batch:
  - `api/targets.py` gained `GET`/`POST /targets/{name}/edit`, sharing
    `create_target`'s exact validation via a new `_validate_profile()`
    helper. Name is fixed (not renameable via this form). Saving
    rewrites the whole file via the same `yaml.safe_dump()` create
    already uses — **no comment-preserving round-trip**, a named
    trade-off (the edit form shows a standing warning), not an oversight.
  - `api/nav.py` gained a shared `pagination_bar()` (`PAGE_SIZE = 25`),
    used by `/runs`, `/targets`, `/campaigns`. **A real bug caught while
    testing it:** `page_size`'s default parameter was bound to
    `PAGE_SIZE` at nav.py's *import* time, so monkeypatching `PAGE_SIZE`
    in a test had no effect unless every call site passed
    `page_size=PAGE_SIZE` explicitly (read at call time from its own
    module) instead of relying on the default — an ordinary Python
    default-argument-evaluated-once gotcha, easy to miss.
  - `api/campaigns.py` gained bulk-approve — "Approve all queued (N)"
    once 2+ messages are queued, gated on typing back `"approve all
    N"` (N recomputed server-side at submit time, never trusted from
    the page's last render). Deliberately not a JS `confirm()` — this
    codebase has zero client-side JavaScript anywhere and stays that
    way. Does not relax the "no send without approving the exact
    rendered text" hard rule: every queued message's full text is
    already rendered directly on the same page before this control
    exists at all.
  - `db/orchestration.py` gained `_reassign_if_mailbox_inactive()` and
    `build_send_jobs(reassign=...)`: a message stuck on a since-
    deactivated mailbox is reassigned to the least-loaded active
    mailbox in the *same campaign's* sender pool and the change is
    persisted immediately. `reassign` defaults to `False` specifically
    so `preview_approved_messages()` (which never opts in) keeps its
    documented, tested zero-write guarantee (docs/12) — only
    `run_approved_messages()` (the real-run path) passes `reassign=True`.
  - `send/orchestrator.py`'s `run_orchestration_loop()` gained
    `mailbox_active_fn`, checked once per mailbox at the same point as
    (and before) docs/16's `token_health_fn` — an inactive mailbox
    blocks its whole queue the same way a dead token does. Implemented
    in `db/orchestration.py` as a plain `SELECT is_active FROM
    mailboxes WHERE id = ...`, not `session.get(Mailbox, ...)` — the
    latter would return the same identity-mapped object already loaded
    once at the top of the run, whose value could be stale by the time
    a later mailbox's turn comes up in a long loop. Confirmed directly
    (not just by isolation-level documentation) that a fresh
    column-select inside an already-open session sees a different
    session's concurrent commit immediately.
  All five verified against real Postgres/the real running server with
  disposable data, cleaned up after — see docs/19's Verification section.
- **docs/20** — PROJECT.md's GDPR/DPDP legal-posture table was spec-only
  until now: no `legal_region`/`requires_opt_in` field existed anywhere,
  and nothing in the send path checked either. `config/models.py`'s
  `TargetProfile` gained `legal_region: Literal["us", "eu_uk", "india"]`
  (required, no default — an author who forgets it should never get a
  silent posture, per PROJECT.md framing this as "the target country is
  a config flag") and `requires_opt_in: bool = False`, with a
  model-validator requiring `requires_opt_in: true` on any `eu_uk`
  profile at config-load time. Named `legal_region`, not `region` —
  `Business.region` already means state/province of a scraped address,
  an unrelated concept. The actual refusal lives in
  `db/orchestration.py`'s `build_send_jobs()`: it resolves each
  message's campaign → `target_runs.profile_yaml` and excludes any
  message whose profile is `legal_region: eu_uk` unconditionally —
  `requires_opt_in` or not, logged at ERROR — because nothing in this
  codebase collects or records consent anywhere, so the flag alone can
  never make an eu_uk send actually legal. `api/targets.py`'s
  scan-builder form gained the two fields (without this, making
  `legal_region` required would have broken every create/edit
  submission). Both real target profiles updated (`dentists-austin-tx`
  → `us`, `dentists-gurugram` → `india`) since the field is now
  required. DPDP's softer "prefer generic over named contacts"
  preference is still unenforced — a real gap, but a soft one (not a
  "refuses to run" hard rule), left for a follow-up. Verified against
  real Postgres: a disposable `eu_uk`+`requires_opt_in: true` campaign
  produced zero send jobs; a disposable `us` campaign was unaffected.
- **docs/21** — `.github/workflows/ci.yml`: no CI existed at all before
  this — `uv run pytest` only ran when a human remembered to. Now a
  `postgres:16` service container + `uv sync` + `uv run pytest` + `uv
  run alembic upgrade head` + `uv run alembic check` run on every push
  to `main` and every pull request. The constituent commands were
  verified against a real local dockerized Postgres (`alembic check` →
  no drift, `pytest` → 263/263); the workflow file itself has not yet
  been exercised by an actual GitHub Actions run, since that needs a
  real push this session didn't make on its own initiative.
- **docs/22** — a real Postgres-backed test tier, closing ROADMAP.md's
  long-standing gap that `db/repository.py`, `db/persist.py`,
  `db/campaigns.py`, `db/orchestration.py`, and every DB-backed `api/`
  route had zero automated coverage (every real bug in them, docs/08
  through docs/20, was caught by hand). `testcontainers[postgres]` (dev
  dependency) spins up a completely separate, ephemeral Postgres 16 per
  test session — deliberately never the dev `DATABASE_URL`, which holds
  real send data. `tests/conftest.py` provides `db_session` (rolled
  back per test via the SAVEPOINT recipe, even through a real
  `session.commit()` in the code under test) and `db_env` (for real
  API-route tests, since `session_scope()` is called directly rather
  than via a FastAPI dependency, cleaned up with a real `TRUNCATE`
  after). ~50 new tests, most notably a real two-thread concurrency
  test proving `_reserve_fn`'s `pg_advisory_xact_lock` actually prevents
  two racing workers from both sending against a mailbox's last cap
  slot — deterministic (Postgres serialises the transactions, not
  Python timing), not a flaky race. Degrades gracefully with no Docker:
  confirmed `uv run pytest` still passes (263/263) with `DOCKER_HOST`
  pointed at a nonexistent socket, this tier's ~50 tests skipping
  rather than failing.
- **Still not built, on purpose:** the scan-builder page doesn't trigger a
  scan (real Overpass + per-business HTTP calls can take minutes —
  running that synchronously in a request handler is a browser-timeout
  footgun); bounce/reply monitoring (step 7, needs restricted
  `gmail.readonly`/`gmail.modify` scopes + CASA) is untouched and
  deliberately deferred (2026-09-13 — a real CASA review is a paid,
  multi-week third-party assessment for production apps serving
  external users; this internal tool can very likely avoid it entirely
  by staying in the OAuth consent screen's Testing mode with each
  mailbox added as a test user, at the cost of 7-day refresh-token
  expiry — re-evaluate that path before assuming CASA is required when
  step 7 is picked up). The orchestration loop's preflight
  token-health check (ROADMAP.md item 4) is now built — see
  `send/orchestrator.py` below and docs/16.
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
  gets the old truthy-counts-as-matched behaviour unchanged.
  `last_content_year` (in `signals.py`, not `qualify.py`) used to be
  computed by regexing the *entire page text* for the largest 4-digit
  year found, which reliably picked up a "© 2026" footer as "last
  content" — fixed in docs/15: a year immediately after a copyright
  marker (`©`/`(c)`/"copyright") is now excluded before taking the max,
  so a copyright-only page reports `None` instead of a fabricated
  "fresh" signal. Verified against real re-crawled sites (docs/15).
  **Known remaining gap, narrower and separate:** a page whose only
  other year mentions come from embedded structured data (e.g. JSON-LD
  review `datePublished` values) still counts those — arguably correct
  (the page's reviews really are that recent), but worth knowing if it
  ever looks wrong on a specific site; see docs/15's smile360atx.com
  example.
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

**The first real send has happened** (2026-09-13) — a real approved
message to a real Austin dentist practice, sent via the campaigns UI's
Start sending button + an `rq worker`, real `gmail_message_id` recorded.
The system works end to end with real data, not just fixtures. Every
future real send still needs the same explicit human confirmation
(neither `--live` nor the UI's confirm-phrase gate should ever be
triggered on a Claude session's own initiative) — this note records that
it has now been proven to work, not that the confirmation requirement
is relaxed going forward.

Next concrete step and the full backlog, in priority order, with
reasoning: **[ROADMAP.md](ROADMAP.md)** — keep it updated as items ship
or new ones are found, don't just leave status in chat history.

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

`uv run pytest` needs no setup and no running services for most of the
suite. The `test_*_db.py` files (docs/22) are the exception: they spin
up their own ephemeral Postgres via `testcontainers` (a completely
separate container from `docker compose`'s dev Postgres — never point
tests at the real `DATABASE_URL`, which holds real send data) and just
skip themselves if Docker isn't reachable, so the rest of the suite
still runs and passes either way.

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
