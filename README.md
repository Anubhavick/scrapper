# Lead Generation & Cold Outreach System

A self-hosted pipeline that finds businesses matching a target profile,
crawls their own websites for contact details and factual signals,
drafts a personalised cold email grounded in those signals, and sends it
through a team member's own Gmail account under strict rate caps — with
a mandatory human-approval step and permanent suppression tracking.

Full spec, data model, and legal posture: [PROJECT.md](PROJECT.md).
**New here? Start with [MANUAL.md](MANUAL.md)** — architecture, module
reference, and current status in one place. **Operator's guide — exact
commands, step by step, from a fresh clone to a real test email:
[HOWTO.md](HOWTO.md).** Notes for anyone (human or Claude) working in
this codebase: [CLAUDE.md](CLAUDE.md). A running build log, one file per
completed step with the reasoning behind every non-obvious decision:
[docs/](docs/). **What's left and in what order: [ROADMAP.md](ROADMAP.md).**

## Status: build order steps 1–6 done, step 8 done, out of 9

**Discover → enrich → CSV/Postgres is a real, runnable, and now
persisted pipeline (steps 1–5, plus DB persistence added afterward).
Step 6 added the Gmail OAuth flow, message composition, and the
send-queue decision logic — verified end-to-end against a real Gmail
account (a real message was actually sent and received). Step 8's full
web UI now exists: browse/create target profiles, browse run history,
and build a campaign from a completed run and approve each rendered
message by hand. The orchestration loop that reads an approved message
and actually sends it is now built too (docs/12) — dry-run-verified
against real Postgres, but not yet run with `--live` against a real
campaign. That's the last step before this system can send for real.**

**Worth knowing:** PROJECT.md's build order frames step 5's CSV as a
hard gate — read 200 rows by hand *before* building anything past it,
to confirm the data justifies building the sending half at all. That
review has now happened, at smaller volume than 200 (31 real rows) —
see [docs/07](docs/07-review-ui.md) for what it found and fixed. Worth
repeating at larger volume before trusting qualification broadly across
other business types/cities. Step 6 was originally built *ahead* of
that review, on direct instruction — see
[docs/06](docs/06-gmail-oauth-and-send-queue.md).

What's done:

- Postgres 16 + Redis via Docker Compose; full schema (11 tables),
  migrated with Alembic. **Now actually written to** —
  `businesses`/`contacts`/`enrichment_signals`/`target_runs`/
  `target_run_businesses` are populated by every persistence-enabled
  pipeline run (docs/08, docs/09); `campaigns`/`campaign_mailboxes`/
  `messages` are populated by the campaigns UI (docs/11). `mailboxes` is
  written separately by `scripts/authorize_mailbox.py`; `suppressions`
  is migrated but still unwritten.
- `normalise_domain()` — the domain-dedup function everything else
  depends on
- A Pydantic-validated config loader for target profiles, business
  types, and offers — bad YAML fails loudly with a file path, not a
  stack trace
- **Discover**: Nominatim geocoding (cached, rate-limited) + an Overpass
  query builder/runner/parser covering all four location modes
  (`radius`/`bbox`/`city`/`admin_area`) — no API key needed — plus
  discovery-time filtering (`exclude_domains`, `must_have_website`, …).
  Confirmed reachable from a real machine against real Overpass/
  Nominatim, not just mocks (docs/03).
- **Enrich**: a robots.txt-respecting, rate-limited crawler for a
  business's own pages, extracting contact emails (never guessed), the
  8 enrichment signals from PROJECT.md's example list, and a
  qualification check against the profile's rules
- **Pipeline**: `leadgen.pipeline.run_target_profile()` wires all of the
  above together end-to-end, optionally persisting to Postgres as it
  goes, and `export_csv()` writes a CSV either way. Each row also gets
  auto-generated **tags** (`no-website`, `no-booking`,
  `platform-wordpress`, `content-year-2019`, …) and a `crawl_status`
  (`ok`/`partial`/`unreachable`/`no_website`) so a dead site can't be
  mistaken for one with genuinely empty signals.
- **Web UI**: one FastAPI app (`uv run uvicorn leadgen.api.review:app
  --reload`) covering all of step 8: `/` filters a pipeline CSV and
  rejects bad OSM matches with one click (docs/07); `/targets` browses
  and creates target profiles from a form instead of hand-editing YAML
  (docs/10); `/runs`/`/runs/{id}` browses every past scan from Postgres
  (docs/09); `/campaigns` turns a completed run's qualified leads into
  rendered messages a human approves or rejects one by one (docs/11).
- **Compose**: `leadgen.compose.render.render_message()` fills an offer's
  subject/body template with one generated line grounded in a real
  boolean signal (e.g. "no online booking found")
- **Send**: `leadgen.send.{crypto,oauth,gmail,caps,suppression,queue}` —
  refresh-token encryption, the Gmail OAuth authorization-code flow
  (`gmail.send` scope only), MIME message building + the actual Gmail
  API send call, and the pure decision logic for the 50/day cap,
  suppression checks, and the 90–600s randomised gap between sends.
  **Verified against a real Gmail account** — `scripts/authorize_
  mailbox.py` + `scripts/send_test_email.py` sent and received a real
  message.
- **Campaigns**: `leadgen.db.campaigns` + `leadgen.api.campaigns` — turn
  a completed target run's qualified leads into a `campaigns` row and
  one rendered `messages` row per business (addressed to its best
  contact — a named address preferred over a generic `info@`-style one),
  then approve or reject each one at `/campaigns/{id}`, full rendered
  text visible, approving requires typing a name first. Verified for
  real against a live Postgres run (docs/11).
- **Orchestration loop**: `leadgen.send.orchestrator` (pure decision
  logic) + `leadgen.db.orchestration` (DB glue) +
  `scripts/send_approved_messages.py` (CLI) — reads `messages` where
  `status='approved'`, sends each via `send_next()`'s sleep→re-check→send
  order, using an advisory-lock reservation so concurrent runs can't
  both see "under cap." Three modes: no flags is a genuinely read-only
  preview (zero writes); `--dry-run` runs the real loop against
  disposable test data (fakes only the Gmail call — the reservation
  still really flips messages to `sent`); `--live` plus a typed
  confirmation phrase actually sends. Verified against real Postgres in
  both preview mode (confirmed zero writes) and `--dry-run` mode (happy
  path, a suppression-block, a cap-block) (docs/12). **Not yet run with
  `--live` against a real campaign.**
- 196 passing tests, all against mocked HTTP, pure functions, or static
  fixtures — no real network calls in the test suite itself (real
  verification runs, listed above, were separate manual steps)

What's not done, and things worth knowing before trusting this against
real data or a real send:

- **The orchestration loop has never been run `--live`.** Everything
  through message approval and the send loop itself is built and
  verified against real Postgres (docs/12); pointing it at a real
  campaign with a real Gmail send is a deliberate, separately-confirmed
  next action, not
  something this repo does on its own.
- Bounce/reply monitoring (step 7) doesn't exist — needs the restricted
  `gmail.readonly`/`gmail.modify` scopes and a Google CASA review.
- **Qualification has a documented, partially-closed gap.** Non-boolean
  signals (`last_content_year`, `page_weight_mb`) only count as a real
  problem if a profile opts in via `qualification.stale_content_before_
  year` / `max_page_weight_mb`; without that they still count as
  "matched" whenever truthy, unchanged from before. Separately,
  `last_content_year` itself is still computed by regexing full page
  text for the largest year found, which reliably mistakes a "© 2026"
  footer for real content freshness — flagged, not yet fixed (docs/07).
- `businesses.normalized_domain`'s partial unique index doesn't hold for
  a real multi-location chain sharing one domain — handled, not fully
  solved (see [docs/08](docs/08-persistence.md)).

## Setup

Prerequisites: [uv](https://docs.astral.sh/uv/), Docker Desktop (or
compatible), Python 3.12 (uv will install it for you if it's missing).

```bash
git clone <this repo>
cd scrapper

cp .env.example .env
# edit .env: at minimum leave the Postgres/Redis defaults as-is for
# local dev; fill in Gmail OAuth + TOKEN_ENCRYPTION_KEY later, once the
# send stage exists

uv sync                        # installs deps into .venv
docker compose up -d           # starts postgres:16 + redis:7
uv run alembic upgrade head    # creates all tables

uv run pytest                  # confirm everything works: should be all green
```

If dependency or Python downloads time out, your network may have a
slow ramp-up on large transfers — retry with:

```bash
UV_HTTP_TIMEOUT=240 uv sync
```

## Running it

```bash
uv run python scripts/run_pipeline.py targets/dentists-gurugram.yaml leads.csv
```

This discovers, crawls, and qualifies against a real target profile,
persists everything to Postgres (a `target_runs` row plus
`businesses`/`contacts`/`enrichment_signals`), and writes `leads.csv` too.
Add `--no-db` to skip persistence and only write the CSV. Then review the
result in a browser instead of a raw file:

```bash
uv run uvicorn leadgen.api.review:app --reload
# CSV, no DB needed:
# http://127.0.0.1:8000/?csv=leads.csv&profile=targets/dentists-gurugram.yaml
# every past run, from Postgres:
# http://127.0.0.1:8000/runs
```

This is the "read 200 rows by hand" checkpoint from PROJECT.md's build
order — actually read the leads before trusting qualification, and
before building a campaign from a target profile you haven't run before.
Once a run has qualified leads, `/campaigns/new` turns them into rendered
messages you approve or reject one by one — nothing is sent by doing
this; see "How sending automation works" below.

## How the pipeline is designed to work

Everything about *who* gets contacted lives in a target-profile YAML file
(`targets/*.yaml`, e.g. `targets/dentists-gurugram.yaml`) — business
type, city/radius, filters. Everything about *what you pitch* lives in a
separate offer YAML file (`config/offers/*.yaml`). **Changing either
never requires a code change:**

- Different vertical or city → copy an existing `targets/*.yaml`, edit
  `business_type` and `location`, done. New business type entirely →
  add one block to `config/business_types.yaml` (OSM tags, Places type,
  keywords).
- Different pitch to the same leads (e.g. "automation" vs. "a website
  tool") → add a new `config/offers/*.yaml` with its own
  `subject_templates`/`body_template`/`relevant_signals`/`cta`, and point
  a target profile's `outreach.offer_id` at it.

See PROJECT.md for the full YAML format. The pipeline runs in these
stages:

```
target profile (YAML)
      │
      ▼
[1] DISCOVER  ──► businesses          (OpenStreetMap Overpass / Google Places / CSV)
      ▼
[2] CRAWL     ──► contacts + signals  (their own website only)
      ▼
[3] QUALIFY   ──► sendable leads      (rules from the profile)
      ▼
[4] COMPOSE   ──► drafts              (template + one generated line)      -- wired up via campaigns
      ▼
[5] REVIEW    ──► human approval      (mandatory — nothing skips this)     -- /campaigns/{id}
      ▼
[6] SEND      ──► Gmail API, capped, randomised delays                    -- built (docs/12); dry-run-verified, not yet run --live
      ▼
[7] MONITOR   ──► replies / bounces / unsubscribes → permanent suppression
```

### How scraping (discover + enrich) works — built and tested

- **Discover** (`src/leadgen/discover/`) resolves the profile's
  `location` (a place name, via Nominatim, cached to disk) into a
  bounding box or radius, then queries OpenStreetMap's Overpass API for
  businesses matching the profile's `business_type` — no API key
  needed. Google Places is a configured fallback, used only to fill
  gaps, because Places content can't be persisted beyond 30 days per
  its ToS (OSM's ODbL has no such limit, which is why Overpass is
  primary — the Places fallback itself isn't implemented yet).
- **Enrich** (`src/leadgen/enrich/`) crawls a small, profile-specified
  set of pages on each business's *own* website (`/`, `/contact`,
  `/about`, …) — never a third party — respecting `robots.txt`, at 1
  request/second per host, with a real User-Agent that includes a
  contact URL. It extracts:
  - contact emails (only ones actually present on the site — **no
    guessing, no `firstname@domain` permutation, no SMTP probing**)
  - factual signals the profile asks for (no HTTPS, no mobile viewport,
    no booking widget, site platform, last-updated year, page weight,
    WhatsApp link presence, etc.)
- `leadgen.pipeline.run_target_profile()` wires discover → filter →
  crawl → qualify together and returns one row per surviving business;
  `export_csv()` writes those rows to a file. See "Running it" above.
- `run_target_profile()` optionally persists as it runs — pass `session`/
  `target_name`/`profile_yaml_text` and it upserts into
  `businesses`/`contacts`/`enrichment_signals` and tracks the run itself
  in `target_runs` (docs/08); `scripts/run_pipeline.py` does this by
  default. Raw HTML caching (`crawl_cache`, so re-running during
  development never re-crawls a page that's still fresh) is designed
  into the schema but still not wired up — nothing writes to it yet.

Per PROJECT.md's build order: config loader → Overpass discoverer →
site crawler → CSV export (all four done) → **read 200 rows by hand
before building anything past this point** — the point is to find out
whether the data is good enough to justify building the sending half at
all. That hand-review is a human task now, not a build step.

### How sending automation works — approval and orchestration both built

- Each **mailbox** (a team member's own Gmail account) authenticates via
  OAuth (`leadgen.send.oauth`, `gmail.send` scope only); the refresh
  token is stored encrypted (`leadgen.send.crypto.TokenCipher`), never in
  git or logs.
- A **campaign** pairs a completed target run with an offer (the pitch,
  also YAML — `config/offers/*.yaml`) and a pool of sender mailboxes —
  built via `/campaigns/new`, which also generates one rendered message
  per qualified business in that run (docs/11).
- **Compose** (`leadgen.compose.render.render_message()`) renders a
  subject + one generated line per lead from the offer's template,
  grounded in that business's actual signals — no message is sent
  without a human clicking approve on the exact rendered text first, at
  `/campaigns/{id}` (docs/11).
- **Send** enforces, in code, not config, via `leadgen.send.queue` +
  `leadgen.send.gmail`:
  - max 50 emails per mailbox per day (`send.caps.can_send()`)
  - randomised 90–600 second gaps between sends, no bursts
    (`send.queue.wait_before_next_send()`)
  - a suppression check (email *or* domain) before every single send —
    unsubscribes are permanent and global across every campaign, forever
    (`send.suppression.is_suppressed()`)
  - all three enforced together by `send.queue.check_sendable()`, which
    raises `SendBlocked` with a reason instead of silently skipping
- **Monitor** watches for bounces, replies, and unsubscribes via the
  Gmail API and writes to the suppression list automatically — not
  built; needs the restricted `gmail.readonly`/`gmail.modify` scopes and
  a CASA security assessment, unlike the `gmail.send`-only scope used so
  far.
- The target country is a config flag, because what's legally permitted
  differs — see PROJECT.md's legal posture table (CAN-SPAM / GDPR /
  DPDP). Default posture is the strictest of the three. **This is not
  legal advice — verify before the first real send.**

- **Orchestrate**: `scripts/send_approved_messages.py` reads `messages`
  rows with `status='approved'` and calls the pieces above in order for
  each one, one mailbox's queue at a time, stopping that mailbox's queue
  on a cap-block (a suppression-block only skips the one message). No
  flags: a genuinely read-only preview, zero writes. `--dry-run`: the
  full loop for real (real reservation writes, faked Gmail call —
  disposable test data only, this really consumes real approved messages
  if pointed at them). `--live` plus typing back a confirmation phrase:
  the real thing (docs/12).

Everything through approval and orchestration — campaign creation,
message rendering, human approval, and the send loop itself — is built
and verified (docs/11, docs/12). Real Google OAuth credentials and a
`TOKEN_ENCRYPTION_KEY` already exist and have been verified against a
real account (HOWTO.md, docs/06). **The one thing that hasn't happened
yet is running `scripts/send_approved_messages.py --live` against a real
campaign** — a deliberate, separately-confirmed step, not a missing
capability.

## Project layout

```
src/leadgen/
  config/     target profiles, business types, offers  (implemented)
  discover/   Overpass geocoding + query/parse + filters (implemented; Places/CSV not yet)
  enrich/     site crawler → contacts + signals + qualify (implemented)
  pipeline.py discover → filter → crawl → qualify → CSV + optional Postgres persist (implemented)
  compose/    template + generated line → draft messages (implemented)
  send/       Gmail OAuth, MIME+send, caps, suppression, queue decision logic
              (implemented, verified against a real account; nothing
              orchestrates these into a send loop yet)
  db/         SQLAlchemy models; session + send-side queries; persist.py
              (business/contact/signal/target_run upserts, docs/08);
              campaigns.py (campaign + message generation, docs/11) —
              all implemented, verified against a real Postgres
  api/        FastAPI web UI: lead review (docs/07), scan-builder
              (docs/10), run history (docs/09), campaigns + message
              approval (docs/11) — all implemented. Orchestration
              (actually sending an approved message) is not.
  util/       normalise_domain() and friends (implemented)
alembic/      migrations
config/       business_types.yaml, offers/*.yaml (example data)
targets/      target profile YAML files (example data)
docs/         one file per completed build-order step
tests/
```
