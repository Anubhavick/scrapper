# MANUAL — Engineering reference for the Lead Generation & Cold Outreach System

This is the single document meant to get an engineer who has never seen
this repo to a working mental model of the whole thing — architecture,
data model, module boundaries, current state, and what's genuinely left.
It doesn't replace the other docs (below); it's the map that tells you
which of them to open next.

## 1. What this system does

A self-hosted tool, run by a small team, that:

1. **Discovers** businesses matching a target profile (business type +
   location + filters) from OpenStreetMap (primary) or Google Places
   (fallback, not yet built).
2. **Enriches** each one by crawling its own website — never a third
   party — for a contact email and a handful of factual signals ("no
   online booking," "no HTTPS," site platform, page weight, …).
3. **Qualifies** each business against the target profile's rules, so
   only leads worth contacting survive.
4. **Composes** a personalised cold email grounded in a real signal
   found on that business's own site.
5. **Sends** it through a team member's own Gmail account, under a hard
   50-emails/mailbox/day cap with randomised delays — and never without
   a human clicking approve on the exact rendered text first.
6. **Tracks** replies, bounces, and unsubscribes, suppressing that
   contact permanently and globally (not yet built).

Full spec — data model, target-profile YAML format, hard rules, legal
posture, the original build order — lives in [PROJECT.md](PROJECT.md).
This manual assumes you'll read that too; it doesn't repeat all of it.

**Design principle worth internalizing early:** this system is not
optimised for volume. ~50 sends/mailbox/day is a deliverability ceiling
no amount of better scraping code changes. Every design choice here
optimises for *precision* — fewer, better-qualified leads — not
throughput.

## 2. How the documentation is organized

There are six places information about this repo lives. Reading the
wrong one for what you're trying to do wastes time, so:

| Document | Read it when you want to know... |
|---|---|
| **MANUAL.md** (this file) | The big picture: architecture, module map, current status, what's next |
| [PROJECT.md](PROJECT.md) | The actual spec: data model, YAML formats, hard rules, legal posture — the source of truth for *what the system is supposed to do* |
| [HOWTO.md](HOWTO.md) | Exact commands, in order, from a fresh clone to a real sent email |
| [README.md](README.md) | A shorter, GitHub-front-page version of this manual plus setup |
| [CLAUDE.md](CLAUDE.md) | Conventions and schema decisions, written for whoever (human or AI) is about to edit this code |
| [docs/](docs/README.md) | A dated build log — one file per completed step, written *after* it shipped, recording what actually happened and why, including deviations from plan |

If something in one of these contradicts another, `git log` and the code
itself outrank all of them — these are maintained by hand and drift.

## 3. Architecture

### 3.1 The central abstraction: target profiles

Nothing about *who gets contacted* is hardcoded. It's a YAML file:

```yaml
# targets/dentists-austin-tx.yaml
name: dentists-austin-tx
business_type: dentist              # resolved via config/business_types.yaml
location:
  mode: radius                      # radius | bbox | city | admin_area
  center: "Austin, Texas, USA"
  radius_km: 15
source:
  primary: overpass
  max_results: 500
filters:
  must_have_website: true
  exclude_domains: [touchto.io]     # a business OSM mistags, or a chain to skip
enrichment:
  crawl_pages: [/, /contact, /about, /services]
  max_pages: 6
  signals: [no_https, no_online_booking, site_platform, last_content_year, ...]
qualification:
  require_email: true
  require_any_signal: [no_online_booking, no_contact_form]
  min_signal_count: 1
outreach:
  offer_id: appointment-automation
  sender_pool: [sales1]
  daily_cap_per_mailbox: 40
```

A new city or vertical is a new YAML file, not a code change. A new
business type is one block added to `config/business_types.yaml`
(OSM tags + Places type + keywords). A new pitch to the same leads is a
new `config/offers/*.yaml`. See PROJECT.md for the full format.

### 3.2 The pipeline

```
target profile (YAML)
      │
      ▼
[1] DISCOVER  ──► businesses          (OpenStreetMap Overpass; Places/CSV not built)
      ▼
[2] CRAWL     ──► contacts + signals  (the business's own site only)
      ▼
[3] QUALIFY   ──► sendable leads      (rules from the profile)
      ▼
[4] COMPOSE   ──► drafts              (template + one generated line)      ← built, not wired up
      ▼
[5] REVIEW    ──► human approval      (mandatory)                          ← lead-review UI exists; message-approval UI doesn't
      ▼
[6] SEND      ──► Gmail API, capped, randomised delays                     ← built, not wired up
      ▼
[7] MONITOR   ──► replies/bounces/unsubscribes → permanent suppression     ← not built
```

Stages 1–3 are wired together today by `leadgen.pipeline.
run_target_profile()`, callable via `scripts/run_pipeline.py`. Stages 4
and 6 exist as tested building blocks (`compose/`, `send/`) that nothing
yet calls in sequence — that's the actual next piece of work, not a
missing capability (see §7).

### 3.3 Where persistence fits (added after step 5, see docs/08)

`run_target_profile()` can run two ways:

- **Without a `session`**: pure, in-memory, CSV-only. No database
  dependency at all — this is how the whole test suite exercises it.
- **With a `session`** (what `scripts/run_pipeline.py` does by default):
  it also creates a `target_runs` row up front, upserts every
  business/contact/signal into Postgres as it crawls, records a
  `target_run_businesses` row per business (that run's own
  qualified/crawl_status/tags verdict — see §4), and marks the run
  `completed` or `failed` at the end.

This was deferred from step 5 on purpose (PROJECT.md's build order says
so explicitly) until there was a concrete reason to need it — there now
is: a run-history view (built, see below) and an editable/re-runnable
campaign both need real rows with real IDs to reference, not a CSV
that's gone the moment you overwrite it. The run-history view itself —
`/runs` and `/runs/{id}` in `api/review.py`, reading exactly these
tables instead of a CSV — is docs/09.

### 3.4 Where the database ends and campaigns begin (not built yet)

`campaigns` and `messages` exist in the schema and are migrated, but
nothing writes to them. The intended flow, once built: pick a
`target_run` + an offer + a sender pool → create a `campaigns` row →
render one `messages` row per qualified contact via
`compose/render.py` (already built) → a human approves each rendered
message → an orchestration loop calls `send/queue.py` +
`send/gmail.py` against approved ones, respecting caps/suppression/delay.

## 4. Data model

Eleven tables, defined in `src/leadgen/db/models.py`, two Alembic
migrations so far. Full column list is in the code (it's the more
authoritative source — this is a summary):

| Table | Purpose | Notable constraint / decision |
|---|---|---|
| `businesses` | one row per discovered company | unique on `(source, source_id)`; **partial** unique on `normalized_domain` (null allowed) — see the multi-location-chain caveat in docs/08 |
| `contacts` | emails found for a business | unique on `(business_id, email)`; always carries `source`/`fetched_at`/`legal_basis` (hard rule) |
| `enrichment_signals` | key/value facts from the site | one row per `(business_id, key)`; re-crawl updates in place, no history |
| `crawl_cache` | raw HTML + fetched_at | designed for re-crawl avoidance during dev; **not written to by any code yet** |
| `target_runs` | one row per pipeline execution | stores the **full resolved profile YAML**, not a reference, so a run stays reproducible even if the file changes later |
| `target_run_businesses` | join: which businesses a run found, with that run's qualified/crawl_status/tags | (docs/09) these three live here, not on `businesses`, because a re-scan of the same business can change them; unique on `(target_run_id, business_id)` |
| `campaigns` | a target run + an offer + a sender pool | offer is referenced by id (config file), not a foreign key — offers live in YAML, never the DB |
| `campaign_mailboxes` | join table, campaign ↔ sender pool | |
| `mailboxes` | a sending Gmail identity | **no daily-counter column, on purpose** — it's derived from `messages.sent_at` at query time, so there's nothing to drift out of sync with a badly-timed reset job |
| `messages` | one per `(campaign, contact)` | carries **rendered** subject/body (not a template reference) so an approved message can't silently change later; carries `gmail_message_id`/`gmail_thread_id` for future bounce/reply correlation |
| `suppressions` | permanent email/domain blocklist | `(scope, value)` shape, not two nullable columns; checked before **every** send, no exceptions, ever |

**`normalise_domain()`** (`src/leadgen/util/domains.py`) is the one
function almost everything else depends on for dedup — lowercase, strip
`www.`, strip path/query/port, punycode-normalise, reject anything with
no extractable host. Read its module docstring before touching it.

Full reasoning behind every non-obvious schema choice: CLAUDE.md's
"Schema decisions" section, and `models.py`'s own docstrings.

## 5. Module reference (`src/leadgen/`)

| Module | What it does | Status |
|---|---|---|
| `config/` | Pydantic schemas + YAML loader for target profiles, business types, offers | Done, tested |
| `util/domains.py` | `normalise_domain()` | Done, tested |
| `discover/` | Nominatim geocoding (cached) + Overpass query/run/parse (all 4 location modes) + discovery-time filters | Done; confirmed reachable against real Overpass/Nominatim (docs/03). Places/CSV sources not implemented |
| `enrich/` | robots.txt-aware rate-limited crawler, email extraction (never guessed), 8 signals, qualification | Done. Known gap: `last_content_year` regexes full page text and gets fooled by copyright footers (docs/07) |
| `pipeline.py` | Wires discover → filter → crawl → qualify → CSV, optionally persisting to Postgres | Done, verified against real Postgres (docs/08) |
| `db/persist.py` | Upserts for businesses/contacts/signals/target_runs/target_run_businesses | Done, verified against real Postgres |
| `db/repository.py` | The two Postgres queries behind send caps/suppression (`count_sent_today`, `fetch_suppressions`, `reserve_send_slot`) | Done; **not covered by the automated test suite** (JSONB/UUID aren't SQLite-compatible) — verify manually against real Postgres before relying on it |
| `db/session.py` | Engine/sessionmaker, reads `DATABASE_URL` | Done |
| `db/models.py` | Full SQLAlchemy schema | Done, migrated |
| `compose/render.py` | Renders subject + one generated line from an offer template, grounded in a real signal | Done, tested. Raises rather than fabricating a generic line if no relevant signal is actually true |
| `send/{crypto,oauth,gmail,caps,suppression,queue}.py` | Refresh-token encryption, Gmail OAuth flow (`gmail.send` only), MIME + actual send call, cap/suppression/delay decision logic | Done, tested; **verified against a real Gmail account** (a real message was sent and received) |
| `api/review.py` | FastAPI UI: `/` filters a pipeline CSV and rejects bad matches (docs/07, no DB needed); `/runs` + `/runs/{id}` browse past scans from Postgres instead (docs/09), `/runs?target_name=` filters to one target | Done. Message-approval UI is a separate, unbuilt piece |
| `api/targets.py` | Scan-builder UI (docs/10): `/targets` lists profiles, `/targets/new` + `POST /targets` create one (validated via `TargetProfile.model_validate()`, create-only -- never overwrites), `/targets/{name}` shows the raw YAML + run command | Done, tested (no DB dependency) |
| `api/nav.py` | Shared top-nav strip across all three pages | Done |
| `api/` (rest) | Campaign creation, message approval, orchestration trigger | **Not built** |

## 6. Setup & running

Full step-by-step (including a real Gmail send): [HOWTO.md](HOWTO.md).
Condensed version:

```bash
uv sync                          # installs into .venv, Python 3.12 pinned
cp .env.example .env             # fill in real secrets later; never commit .env
docker compose up -d             # postgres:16 + redis:7
uv run alembic upgrade head      # apply migrations
uv run pytest                    # should be all green (188 tests as of this writing)
```

Build a new target profile without hand-editing YAML, or browse the ones
that exist, then run one (discovers, crawls, qualifies, persists to
Postgres, writes a CSV):

```bash
uv run uvicorn leadgen.api.review:app --reload
# http://127.0.0.1:8000/targets           -- browse/duplicate/create profiles
uv run python scripts/run_pipeline.py targets/dentists-austin-tx.yaml leads.csv
```

Review the result in a browser — either the CSV directly, or (now that
it's persisted) the run's history entry:

```bash
# CSV-based, no DB needed:
# http://127.0.0.1:8000/?csv=leads.csv&profile=targets/dentists-austin-tx.yaml
# DB-based, every past run:
# http://127.0.0.1:8000/runs
```

If a dependency download times out, this network has a slow ramp-up on
large transfers, not a real block — retry with `UV_HTTP_TIMEOUT=240`.

## 7. Testing

`uv run pytest` — 188 tests, all pure-function or mocked-`httpx`, zero
real network calls, zero real database. This is intentional and has a
consequence worth knowing: two real modules
(`db/repository.py`, `db/persist.py`) — and the `/runs`/`/runs/{id}`
routes in `api/review.py` — are **not exercised by this suite at all**,
because `db/models.py` uses Postgres-specific `JSONB`/`UUID` column types
that SQLite can't stand in for. Those are verified by hand against a
real, migrated Postgres instead — see the "Verification" section at the
bottom of docs/06, docs/08, and docs/09 for what was actually run and
what came back. Treat any change to those as unverified until you've done
the same. (`_business_row_dict()`, the pure function that maps DB rows
into the CSV-page's row-dict shape, *is* covered — see
`test_business_row_dict_matches_csv_row_shape` in `test_review_api.py`.)

Similarly, live network reachability (Overpass, Nominatim, real business
websites, real Gmail) is confirmed by the manual verification runs
recorded in docs/03, docs/06, docs/07, docs/08, and docs/09 — not by the
automated suite, which mocks all of it deliberately.

`api/targets.py` (docs/10) is the exception among the API modules: it has
no Postgres dependency at all (target profiles live in files, not the
DB), so its 16 tests in `test_targets_api.py` run against an isolated
`tmp_path` config set and are fully part of the automated suite —
including the create-only-never-overwrite guarantee and the filename
sanitisation that blocks path traversal.

## 8. Hard rules (non-negotiable, enforced in code)

From PROJECT.md, repeated here because they're easy to lose track of
mid-feature:

- Max 50 sends/mailbox/day, randomised 90–600s gaps, no bursts.
- Suppression check before **every** send, no exceptions, ever.
- Every `contacts` row records `source`, `fetched_at`, `legal_basis`.
- `robots.txt` respected, 1 req/sec/host, real User-Agent with a contact URL.
- No email guessing — only addresses actually found on the business's
  own site or in OSM tags. No permutation, no SMTP probing.
- No message sends without a human clicking approve on the final
  rendered text.
- Secrets never in git, never in logs. OAuth refresh tokens encrypted at
  rest, always.

**Legal posture** is a config flag, not a fixed assumption — CAN-SPAM
(US), GDPR (EU/UK, opt-in required, send stage refuses to run without
it), DPDP (India, prefer generic role addresses). Default is the
strictest of the three. **Not legal advice** — verify before a first
real send in a new region.

## 9. Current status (as of this writing)

Build order steps 1–6 done; step 7 (bounce/reply) untouched; step 8
(FastAPI + review UI) partially done — lead review, run history, and the
scan-builder form exist, message approval doesn't.

Real, verified — not just passing mocked tests:

- Live Overpass/Nominatim reachability and a real site crawl (docs/03)
- A real Gmail account authorized and a real email sent through it (HOWTO.md, docs/06)
- The "read 200 rows by hand" checkpoint, at smaller volume (31 rows) than PROJECT.md's 200 (docs/07)
- Pipeline → Postgres persistence, including a real bug (a multi-location chain colliding on `normalized_domain`) found and fixed on the first real run (docs/08)
- The `/runs`/`/runs/{id}` run-history UI, including two real bugs a browser session (not the unit suite) caught: a `DetachedInstanceError` from reading an ORM attribute after its session closed, and FastAPI treating an empty-string `Form(...)` field as missing rather than empty (docs/09)
- The `/targets` scan-builder UI: a real profile created through the form round-tripped through the actual `load_target_profile()` loader correctly typed, and create-only (never-overwrite) + filename-sanitisation behavior confirmed both by browser testing and by dedicated tests (docs/10)

The three-UI-page plan the user asked for is: history/review (docs/09,
done) → scan-builder form (docs/10, done) → campaigns page (next). Not
built, in priority order:

1. **Orchestration**: `target_run` → `campaigns`/`messages` rows, then a
   loop calling `send/queue.py` + `send/gmail.py` against approved ones.
2. **Message-approval UI** — needs #1 to exist first (nothing to approve
   without it).
3. **Step 7**: bounce/reply monitoring — needs a Google CASA review for
   the restricted `gmail.readonly`/`gmail.modify` scopes.
4. Google Places as a second discover source (designed for in
   PROJECT.md, not implemented) — only worth it if Overpass coverage
   proves thin for a real target vertical/city.

For the reasoning behind any specific decision mentioned above, the
matching file in [docs/](docs/README.md) has it in full — this manual
gives you the map, not the argument.
