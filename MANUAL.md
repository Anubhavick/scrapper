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
[4] COMPOSE   ──► drafts              (template + one generated line)      ← wired up via campaigns (docs/11)
      ▼
[5] REVIEW    ──► human approval      (mandatory)                          ← /campaigns/{id} (docs/11)
      ▼
[6] SEND      ──► Gmail API, capped, randomised delays                     ← built and wired up (docs/12); not yet run with --live
      ▼
[7] MONITOR   ──► replies/bounces/unsubscribes → permanent suppression     ← not built
```

Stages 1–6 are wired together today: 1–3 by `leadgen.pipeline.
run_target_profile()` (`scripts/run_pipeline.py`), 4–5 by
`db/campaigns.py` + `api/campaigns.py` (docs/11) — a human can go from a
target profile to an approved, exactly-as-will-be-sent message entirely
through the UI — and 6 by `scripts/send_approved_messages.py` (docs/12),
which reads `approved` messages and sends them. Defaults to a genuinely
read-only preview (no writes at all); `--dry-run` runs the full loop
against disposable test data (fakes only the Gmail call — the
reservation writes are real); `--live` is the real thing. Verified
end-to-end against real Postgres in preview and `--dry-run` modes; not
yet run with `--live` against a real campaign — see §9.

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

### 3.4 Campaigns and message approval (docs/11)

`api/campaigns.py` + `db/campaigns.py`: pick a completed `target_run` +
an offer + a sender pool (real, authorized `mailboxes` only) →
`create_campaign()` makes the `campaigns` row → `generate_campaign_messages()`
renders one `queued` `messages` row per **qualified business** (not per
contact — `_select_best_contact()` picks one address per business,
preferring a named one over a generic `info@`-style one) via
`compose/render.py` (already built, untouched) → a human can hand-edit
the subject/body of any still-`queued` message at `/campaigns/{id}`
before clicking Approve or Reject, required to type a name first so
`approved_by` means something. Editing is locked once approved — the
`edit` route silently refuses to change an approved message, keeping
`messages.subject`/`body` an accurate record of exactly what a human
signed off on. Editing is manual only; an LLM-assisted rewrite was
considered and deliberately not built yet — see docs/11 for why any
future version of that should stay constrained to rephrasing the
already-signal-grounded line, not free drafting.

**The orchestration loop that reads `status='approved'` messages and
calls `send/queue.py` + `send/gmail.py` against them is now built**
(§3.5, docs/12) — it's a separate script, not a route in
`api/campaigns.py`; nothing in that module sends anything itself.

### 3.5 The orchestration loop (docs/12; preflight checks docs/16, docs/19)

`scripts/send_approved_messages.py` (thin CLI) → `db/orchestration.py`
(`build_send_jobs()`/`preview_approved_messages()`/`run_approved_messages()`,
DB-touching glue) → `send/orchestrator.py`'s `run_orchestration_loop()`
(pure decision logic, tested) → `send/queue.py`'s `send_next()` (sleep
the 90-600s gap, re-check fresh state, send) for each `approved`
message, one mailbox's queue at a time. The CLI has three modes: no
flags calls `preview_approved_messages()`, a genuinely read-only summary
(queued/sent-today/would-send-now/would-be-cap-blocked per mailbox, zero
writes) — this is the safe default; `--dry-run` runs the *real* loop
(sleeps, fresh suppression/cap checks, the reservation commit) with only
the Gmail call faked, which still permanently flips eligible messages to
`sent` in Postgres, so it's for disposable test data only, never a real
campaign; `--live` plus typing back a confirmation phrase actually
sends. (An earlier version made `--dry-run`'s behavior the CLI's
default, which would have silently consumed a real campaign's messages
the first time someone ran the script with no flags expecting a safe
look — caught and fixed before any real use, see docs/12.) A cap-block
stops the rest of that mailbox's queue (every later message would fail
identically) without stopping other mailboxes; a suppression-block only
skips that one message. The reservation that holds a cap slot against a
concurrent worker is transitioning the message to `sent` *inside*
`db.repository.reserve_send_slot`'s advisory-locked transaction, before
the Gmail call — see docs/12 for why, and for the accepted failure mode
(a `sent` message with `gmail_message_id` still null if the Gmail call
itself then fails). Verified against real Postgres in preview mode
(zero writes confirmed directly) and `--dry-run` mode (happy path, a
suppression-block, and a cap-block); never yet run with `--live`.
**docs/16**: the loop now also runs `send/oauth.py`'s
`validate_token_health()` once per mailbox before that mailbox's queue
starts — a dead refresh token blocks every job for that mailbox
immediately (no sleep, no reservation) instead of failing once per
remaining message, each after its own 90-600s wait. Verified against
real Postgres and a real Google rejection (`invalid_grant`) using a
disposable mailbox with a garbage refresh token.

### 3.6 Send from the campaigns UI (docs/13)

`/campaigns/{id}` now has a **Send** section, so sending doesn't require
a terminal. It reuses the exact same pieces as §3.5 — `preview_approved_
messages()`/`run_approved_messages()`, now both accepting an optional
`campaign_id` filter — plus two new modules: `queue.py` (`get_queue()`,
an RQ `Queue` bound to `REDIS_URL` — `rq`/`redis` were already
dependencies and Redis already ran via `docker-compose.yml`, just
unused until now) and `jobs.py` (`send_campaign_messages_job()`, the RQ
job body, plus the `CONFIRMATION_PHRASE` constant the CLI script now
imports from here too instead of defining its own).

Why a background job at all: a real send can take hours, so the button
can't run it inline any more than the scan-builder page can trigger a
scan inline (docs/10) — it enqueues a job for a separate `uv run rq
worker` process instead. The page shows a genuinely read-only preview
when nothing's running, and a confirm-phrase-gated "Start sending" form
otherwise (only ever `live=True` — the UI never exposes `--dry-run`,
since that mode's real reservation-writes are only meant for disposable
test data, never a real approved campaign). While a send is active, the
page shows that and auto-refreshes every 15s — no separate progress
bar, since the messages table lower on the same page already shows each
message's live status. A deterministic RQ job id
(`send-campaign-<id>`) blocks a second click from starting a duplicate
run while one's in flight.

**A second real concurrency bug caught while building this**, on top of
docs/12's suppression-ordering one: `db/orchestration.py`'s `_reserve_fn`
now re-checks `message.status == "approved"` under the advisory lock
before reserving it — without this, two overlapping runs over the same
mailbox (much easier to trigger by accident from a browser than from a
terminal) could both have fetched the same message as `approved` before
either reserved it, and the second would have resent it for real.

**A third, unrelated to concurrency:** `send/orchestrator.py`'s broad
`except Exception` (added in docs/12 so one bad message doesn't kill the
whole run) was silently swallowing `rq`'s own `JobTimeoutException` —
which subclasses `Exception`, not `BaseException` — since that's exactly
the kind of thing a bare `except Exception` catches. Caught for real
during this step's own verification: a job hit its timeout mid-sleep,
the exception was logged as an ordinary per-message error, and the job
reported success instead of actually stopping — defeating `job_timeout`
as a safety net entirely. Fixed with a soft `rq` import (this module
still has no hard dependency on it) that re-raises `JobTimeoutException`
instead of swallowing it. Covered by a new pure test.

Verified against real Postgres + Redis with `live=False` throughout, RQ
worker run in `--burst` mode: happy path (the job actually ran the real
90-600s sleep, not patched out, and the message reserved correctly).
Hit and worked around a macOS-specific `rq worker` fork-safety crash
along the way (`OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES`, documented in
HOWTO.md) — not a bug in this codebase. **Not yet clicked through in an
actual browser** — the page/form code follows existing conventions
closely but hasn't had the same live-browser check that caught
docs/09's and docs/11's `DetachedInstanceError` bugs; worth doing before
fully trusting it.

## 4. Data model

Eleven tables, defined in `src/leadgen/db/models.py`, three Alembic
migrations so far (the third, docs/18, just adds two nullable columns to
`mailboxes`). Full column list is in the code (it's the more
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
| `mailboxes` | a sending Gmail identity | **no daily-counter column, on purpose** — it's derived from `messages.sent_at` at query time, so there's nothing to drift out of sync with a badly-timed reset job. `last_send_error`/`last_send_error_at` (docs/18) are point-in-time state, not a history log or counter — cleared on the next real success |
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
| `enrich/` | robots.txt-aware rate-limited crawler, email extraction (never guessed), 8 signals, qualification | Done. `last_content_year`'s copyright-footer bug fixed (docs/15) |
| `pipeline.py` | Wires discover → filter → crawl → qualify → CSV, optionally persisting to Postgres | Done, verified against real Postgres (docs/08) |
| `db/persist.py` | Upserts for businesses/contacts/signals/target_runs/target_run_businesses | Done, verified against real Postgres |
| `db/repository.py` | The two Postgres queries behind send caps/suppression (`count_sent_today`, `fetch_suppressions`, `reserve_send_slot`) | Done; **not covered by the automated test suite** (JSONB/UUID aren't SQLite-compatible) — verify manually against real Postgres before relying on it |
| `db/session.py` | Engine/sessionmaker, reads `DATABASE_URL` | Done |
| `db/models.py` | Full SQLAlchemy schema | Done, migrated |
| `compose/render.py` | Renders subject + one generated line from an offer template, grounded in a real signal | Done, tested. Raises rather than fabricating a generic line if no relevant signal is actually true |
| `send/{crypto,oauth,gmail,caps,suppression,queue}.py` | Refresh-token encryption, Gmail OAuth flow (`gmail.send` only), MIME + actual send call, cap/suppression/delay decision logic | Done, tested; **verified against a real Gmail account** (a real message was sent and received) |
| `send/orchestrator.py` | `run_orchestration_loop()` -- pure decision logic grouping approved sends by mailbox and driving each through `send_next()` (docs/12) | Done, tested (`tests/test_orchestrator.py`) |
| `api/review.py` | FastAPI UI: `/` filters a pipeline CSV and rejects bad matches (docs/07, no DB needed); `/runs` + `/runs/{id}` browse past scans from Postgres instead (docs/09), `/runs?target_name=` filters to one target | Done |
| `api/targets.py` | Scan-builder UI (docs/10): `/targets` lists profiles (paginated, docs/19), `/targets/new` + `POST /targets` create one (validated via `TargetProfile.model_validate()`, create-only -- never overwrites), `/targets/{name}` shows the raw YAML + run command, `/targets/{name}/edit` (docs/19) edits one in place (same validation, rewrites the whole file -- comments are lost, warned inline) | Done, tested (no DB dependency) |
| `db/campaigns.py` | `create_campaign()` + `generate_campaign_messages()` -- turns a target_run's qualified leads into a campaign + one rendered message per business | Done, verified against real Postgres |
| `api/campaigns.py` | Campaigns + message-approval UI (docs/11): `/campaigns` (paginated, docs/19), `/campaigns/new`, `/campaigns/{id}` -- edit a queued message's text, then Approve or Reject (edit locked once approved), or bulk-approve every queued message at once (docs/19, gated on typing back the exact count). Also `/campaigns/{id}`'s **Send** section (docs/13): a read-only preview, and a confirm-phrase-gated button that enqueues `jobs.send_campaign_messages_job` via RQ (never inline -- see docs/13) | Done |
| `api/suppressions.py` | Manual suppression-list UI (docs/14): `/suppressions` -- list + create-only add form, `value` normalised the same way `send/suppression.py` checks it, duplicate/malformed input rejected with a friendly error | Done; not covered by the automated suite (same reason as the other DB-touching `api/` modules) -- verified manually |
| `api/nav.py` | Shared top-nav strip across all six pages, plus a shared `pagination_bar()` (docs/19) used by `/runs`, `/targets`, `/campaigns` | Done |
| `api/auth.py` | HTTP Basic Auth (docs/17): `require_auth`, one shared `WEB_UI_USERNAME`/`WEB_UI_PASSWORD` credential, wired at the `FastAPI(dependencies=...)` level so it covers every route on the shared app | Done, tested (`tests/test_auth.py`); verified against the real running server |
| `api/mailboxes.py` | `/mailboxes` health page (docs/18): live token-health check, sent-today vs. daily cap, last real send error -- everything currently knowable about a mailbox's health without new OAuth scopes | Done, tested (`tests/test_mailboxes_api.py`); verified against the real running server |
| `db/orchestration.py` | `build_send_jobs()` + `preview_approved_messages()` (zero-write summary) + `run_approved_messages()` -- reads `approved` messages, drives `send/orchestrator.py` against real Postgres/Gmail (docs/12, docs/13), running per-mailbox token-health (docs/16) and `is_active` (docs/19) preflights, reassigning a message stuck on an inactive mailbox (docs/19, real-run path only) | Done; **not covered by the automated test suite** (same JSONB/UUID reason as `db/repository.py`/`db/campaigns.py`) -- verified manually |
| `scripts/send_approved_messages.py` | CLI entry point; no flags = read-only preview (default), `--dry-run` = full loop/fake Gmail/real reservation writes (test data only), `--live` + a typed confirmation phrase = actually send | Done; not yet run with `--live` |
| `queue.py` | `get_queue()` -- RQ `Queue` bound to `REDIS_URL` (docs/13) | Done |
| `jobs.py` | `send_campaign_messages_job()` + shared `CONFIRMATION_PHRASE` (docs/13) | Done; verified manually against real Postgres + Redis (`live=False`) |

## 6. Setup & running

Full step-by-step (including a real Gmail send): [HOWTO.md](HOWTO.md).
Condensed version:

```bash
uv sync                          # installs into .venv, Python 3.12 pinned
cp .env.example .env             # fill in real secrets later; never commit .env
docker compose up -d             # postgres:16 + redis:7
uv run alembic upgrade head      # apply migrations
uv run pytest                    # should be all green (191 tests as of this writing)
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
it's persisted) the run's history entry — then, once a run has qualified
leads, turn them into an approvable campaign:

```bash
# CSV-based, no DB needed:
# http://127.0.0.1:8000/?csv=leads.csv&profile=targets/dentists-austin-tx.yaml
# DB-based, every past run:
# http://127.0.0.1:8000/runs
# Campaigns: pick a run + offer + sender pool, approve rendered messages one by one:
# http://127.0.0.1:8000/campaigns/new
```

If a dependency download times out, this network has a slow ramp-up on
large transfers, not a real block — retry with `UV_HTTP_TIMEOUT=240`.

## 7. Testing

`uv run pytest` — 203 tests, all pure-function or mocked-`httpx`, zero
real network calls, zero real database. This is intentional and has a
consequence worth knowing: several real modules
(`db/repository.py`, `db/persist.py`, `db/orchestration.py`) — and the
`/runs`/`/runs/{id}` routes in `api/review.py` — are **not exercised by
this suite at all**,
because `db/models.py` uses Postgres-specific `JSONB`/`UUID` column types
that SQLite can't stand in for. Those are verified by hand against a
real, migrated Postgres instead — see the "Verification" section at the
bottom of docs/06, docs/08, docs/09, and docs/12 for what was actually
run and what came back. Treat any change to those as unverified until
you've done the same. (`_business_row_dict()`, the pure function that
maps DB rows into the CSV-page's row-dict shape, *is* covered — see
`test_business_row_dict_matches_csv_row_shape` in `test_review_api.py`;
`send/orchestrator.py`'s decision logic is likewise covered despite
`db/orchestration.py` not being, for the same reason — see
`tests/test_orchestrator.py`.)

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

Build order steps 1–6 done, including the orchestration loop (docs/12),
a UI to trigger it per-campaign via a background RQ job (docs/13), and
**a real send has now actually happened** — see below; step 7
(bounce/reply) untouched, though a manual suppression-list UI exists now
too (docs/14); step 8 (FastAPI UI) done in substance — lead review, run
history, scan-builder, campaigns, message approval, send-from-the-UI,
and suppressions all exist.

Real, verified — not just passing mocked tests:

- Live Overpass/Nominatim reachability and a real site crawl (docs/03)
- A real Gmail account authorized and a real email sent through it (HOWTO.md, docs/06)
- The "read 200 rows by hand" checkpoint, at smaller volume (31 rows) than PROJECT.md's 200 (docs/07)
- Pipeline → Postgres persistence, including a real bug (a multi-location chain colliding on `normalized_domain`) found and fixed on the first real run (docs/08)
- The `/runs`/`/runs/{id}` run-history UI, including two real bugs a browser session (not the unit suite) caught: a `DetachedInstanceError` from reading an ORM attribute after its session closed, and FastAPI treating an empty-string `Form(...)` field as missing rather than empty (docs/09)
- The `/targets` scan-builder UI: a real profile created through the form round-tripped through the actual `load_target_profile()` loader correctly typed, and create-only (never-overwrite) + filename-sanitisation behavior confirmed both by browser testing and by dedicated tests (docs/10)
- The `/campaigns` + message-approval UI: a real campaign built from a real 6-qualified-lead run, all 6 messages rendered with correctly grounded text and addressed to each business's best contact, approve/reject verified end to end including the "must type a name to approve" guard — plus the same `DetachedInstanceError` class of bug as docs/09, caught the same way and fixed the same way (docs/11)
- The orchestration loop: in `--dry-run` mode, a happy-path run (3 throwaway approved messages all correctly reserved/marked `sent`), a suppression-block (one message blocked and left `approved`, the rest of that mailbox's queue unaffected), and a cap-block (a message blocked with the mailbox's real `daily_cap` temporarily set to 1, left `approved` for a later run); separately, `preview_approved_messages()` confirmed to make zero writes (message statuses read identical before/after) with a correctly computed would-send/would-be-cap-blocked split — all against real Postgres, all cleaned up after (docs/12). Two real bugs found and fixed along the way: an ordering bug in `send/queue.py`'s `send_next()` (it evaluated `sent_today_fn()` before confirming suppression, which would have let a side-effecting reservation run for a suppressed contact), and the CLI originally defaulting to what it called a "dry run" that actually made real reservation writes — caught before any real use and replaced with a genuinely read-only default.
- The campaigns UI's Send section: an RQ job enqueued and run to completion (`--burst` worker) against a real, throwaway approved message with `live=False`, including the real 90-600s sleep (not patched out) and a correct `sent`/null-`gmail_message_id` reservation afterward (docs/13). A macOS-only `rq worker` fork-safety crash was hit and worked around (`OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES`), not a bug in this codebase. Two more real bugs found and fixed: an overlapping-run double-send gap (`_reserve_fn` now re-checks the message is still `approved` under the lock before reserving it) and `send/orchestrator.py`'s broad exception handler silently swallowing RQ's own `JobTimeoutException` (which would have let a job run forever past its `job_timeout` instead of being killed) — both closed, the second covered by a new pure test.
- **The first real send.** A real campaign (`dentists-austin-tx-round-1`, 6 real qualified Austin dentist leads from the existing `dentists-austin-tx` run) was built, one message (Austin Cosmetic Dentistry) was reviewed and approved by the user in the actual browser, and sent for real via the campaigns page's Start sending button + an `rq worker` — a real `gmail_message_id` landed in Postgres. Confirms the whole discover → crawl → qualify → compose → approve → send chain works end to end with real data. The other 5 messages in that campaign are untouched (`queued`, never approved).
- The manual suppression-list UI (docs/14): add/duplicate-rejection/malformed-input all verified against the live server, `value` confirmed to normalise the same way `send/suppression.py` checks it (a full URL for a domain suppression resolves to the same string a real crawled site's domain would). A second instance of docs/09's `Form(...)`-empty-string bug caught and fixed the same way.
- The orchestration loop's preflight token-health check (docs/16): a
  disposable mailbox with a deliberately garbage refresh token got a
  real `invalid_grant` rejection from Google and had every one of its
  jobs blocked immediately (no sleep, no reservation, message left
  `approved`), while the one real authorized mailbox's disposable test
  message went through the full loop normally in the same run — both
  against real Postgres, all test rows deleted after.
- HTTP Basic Auth on the whole web UI (docs/17): hit the real running
  server with `curl` (not just `TestClient`) — no credentials and wrong
  credentials both `401` with `WWW-Authenticate: Basic` on every page
  checked (`/targets`, `/campaigns`, `/suppressions`, `/runs`), correct
  credentials `200` with real page content; `scripts/dev.sh up`'s new
  preflight check confirmed to refuse starting when the credentials are
  unset in `.env` instead of coming up silently broken.
- The `/mailboxes` health page (docs/18): hit the real running server —
  the one real mailbox showed a genuine live "token ok" (an actual
  successful Google refresh-grant call made during the request), the
  correct `1/50` sent-today count, and "none since last success"; a
  disposable mailbox with a deliberately garbage refresh token showed
  "token dead" with Google's real `invalid_grant` response and its
  pre-set `last_send_error` text, both rendered correctly — then
  deleted, only the one real mailbox left afterward.
- Five smaller items (docs/19), all against real Postgres/the real
  running server: `/runs` and `/campaigns` pagination each verified with
  30 disposable rows (correct page counts, correct per-page contents);
  bulk-approve verified end to end (wrong phrase rejected with nothing
  approved, correct phrase approved all 4 queued messages in one
  request); mailbox reassignment verified with a two-mailbox pool (one
  active, one inactive) — `build_send_jobs(reassign=True)` reassigned
  and persisted the change (confirmed from a *separate* session read
  afterward), `reassign=False` left it completely untouched; the
  mid-run `is_active` re-check's core mechanism (a fresh column-select
  seeing another session's concurrent commit within the same open
  transaction) confirmed directly, not just inferred; target-profile
  edit-in-place verified live (created a real disposable profile,
  edited its radius via the real form, confirmed the file on disk
  actually changed) then cleaned up.

Every step through message approval, the orchestration loop, sending
from the UI, and manual suppression is done: history/review (docs/09) →
scan-builder (docs/10) → campaigns + message approval (docs/11) →
orchestration loop (docs/12) → send-from-the-UI (docs/13) →
suppression-list UI (docs/14). The `last_content_year` regex bug (docs/07)
is now fixed (docs/15) — a copyright-footer year no longer gets mistaken
for fresh content — the orchestration loop's preflight token-health
check (docs/16) is done, the web UI now requires a login (docs/17), a
`/mailboxes` health page exists (docs/18, scoped to what's actually
knowable without new OAuth scopes — no real spam/reputation signal is
available yet), and five smaller items are done (docs/19): target-profile
edit-in-place, `/runs`/`/targets`/`/campaigns` pagination, bulk-approve,
mailbox reassignment for a since-deactivated mailbox, and a mid-run
`is_active` re-check in the orchestration loop. Not built, in priority
order:

1. **Step 7**: bounce/reply monitoring — needs a Google CASA review for
   the restricted `gmail.readonly`/`gmail.modify` scopes. Would give
   `suppressions` its automatic writer; docs/14's UI is the manual
   stopgap until then. Deliberately deferred (2026-09-13) — see
   ROADMAP.md for the reasoning on why a real CASA review is likely
   avoidable for this internal tool.
2. Google Places as a second discover source (designed for in
   PROJECT.md, not implemented) — only worth it if Overpass coverage
   proves thin for a real target vertical/city.

For the reasoning behind any specific decision mentioned above, the
matching file in [docs/](docs/README.md) has it in full — this manual
gives you the map, not the argument.
