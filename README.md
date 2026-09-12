# Lead Generation & Cold Outreach System

A self-hosted pipeline that finds businesses matching a target profile,
crawls their own websites for contact details and factual signals,
drafts a personalised cold email grounded in those signals, and sends it
through a team member's own Gmail account under strict rate caps — with
a mandatory human-approval step and permanent suppression tracking.

Full spec, data model, and legal posture: [PROJECT.md](PROJECT.md).
Notes for anyone (human or Claude) working in this codebase:
[CLAUDE.md](CLAUDE.md). A running build log, one file per completed
step with the reasoning behind every non-obvious decision: [docs/](docs/).

## Status: build order steps 1–5 done, out of 9

**Discover → enrich → CSV export is a real, runnable pipeline now.
Compose and send are not built yet.** So today you can point this at a
target profile and get back a CSV of businesses, their contact emails,
enrichment signals, and a qualified yes/no — but nothing drafts a
message, and nothing sends. Per PROJECT.md's build order, that's
deliberate: steps 1–5 exist specifically to prove the data is worth
acting on *before* the sending half gets built at all — and step 5's
CSV is meant to be read by a human, 200 rows at a time, before anything
past this point gets built.

What's done:

- Postgres 16 + Redis via Docker Compose; full schema (10 tables),
  migrated with Alembic — **though nothing writes to it yet**, see below
- `normalise_domain()` — the domain-dedup function everything else
  depends on
- A Pydantic-validated config loader for target profiles, business
  types, and offers — bad YAML fails loudly with a file path, not a
  stack trace
- **Discover**: Nominatim geocoding (cached, rate-limited) + an Overpass
  query builder/runner/parser covering all four location modes
  (`radius`/`bbox`/`city`/`admin_area`) — no API key needed — plus
  discovery-time filtering (`exclude_domains`, `must_have_website`, …)
- **Enrich**: a robots.txt-respecting, rate-limited crawler for a
  business's own pages, extracting contact emails (never guessed), the
  8 enrichment signals from PROJECT.md's example list, and a
  qualification check against the profile's rules
- **Pipeline**: `leadgen.pipeline.run_target_profile()` wires all of the
  above together end-to-end and `export_csv()` writes the result — see
  "Running it" below
- 109 passing tests, all against mocked HTTP or static fixtures — no
  real network calls in the test suite

What's not done, and two things worth knowing before trusting this
against real data:

- Compose, send, monitor, and the API/review UI don't exist yet.
- **No database persistence anywhere.** Discover/enrich/pipeline all
  return plain Python objects and write a CSV directly — nothing
  upserts into `businesses`/`contacts`/`enrichment_signals`/
  `crawl_cache` yet. The schema is migrated and ready; the write path
  just hasn't been built, because step 5 doesn't need it.
- **Qualification has a documented gap.** `require_any_signal` entries
  that aren't booleans (`last_content_year`, `page_weight_mb`) count as
  "matched" whenever the crawler found *any* value — not when that
  value indicates an actual problem — because PROJECT.md never defines
  a staleness/weight threshold, and guessing one would quietly decide
  who gets contacted. See `src/leadgen/enrich/qualify.py`'s docstring
  and [docs/05](docs/05-csv-export-and-qualification.md).
- **Live network reachability to Overpass/Nominatim hasn't been
  confirmed from every environment.** It hung indefinitely in the
  sandbox this was built in (see
  [docs/03](docs/03-overpass-discoverer.md)'s Verification section) —
  the mocked test suite is solid, but that's not proof the real APIs
  are reachable from wherever this actually runs. Check that — and
  actually crawl a real site — before reading the CSV output as
  meaningful.

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

There's no CLI yet — call the pipeline directly:

```python
import httpx
from pathlib import Path

from leadgen.config.loader import load_business_types, load_target_profile
from leadgen.discover.geocode import NominatimClient
from leadgen.pipeline import export_csv, run_target_profile

business_types = load_business_types(Path("config/business_types.yaml"))
profile = load_target_profile(Path("targets/dentists-gurugram.yaml"), business_types)

user_agent = "your-bot/0.1 (+contact: you@example.com)"  # a real one — see .env.example
geocoder = NominatimClient(user_agent=user_agent, cache_dir=Path(".cache/nominatim"))

with httpx.Client(timeout=60.0) as overpass_client, httpx.Client(timeout=30.0) as crawl_client:
    rows = run_target_profile(
        profile,
        business_types[profile.business_type],
        overpass_client=overpass_client,
        crawl_client=crawl_client,
        user_agent=user_agent,
        geocoder=geocoder,
    )

export_csv(rows, Path("leads.csv"))
```

This is exactly the "read 200 rows by hand" checkpoint from PROJECT.md's
build order — run it against a real profile, open `leads.csv`, and
actually read it before anything past step 5 gets built.

## How the pipeline is designed to work

Everything about *who* gets contacted lives in a target-profile YAML
file (`targets/*.yaml`, not yet created) — business type, city, radius,
filters. Changing what you target should never require a code change.
See PROJECT.md for the full YAML format. The pipeline, once built, runs
in these stages:

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
[4] COMPOSE   ──► drafts              (template + one generated line)
      ▼
[5] REVIEW    ──► human approval      (mandatory — nothing skips this)
      ▼
[6] SEND      ──► Gmail API, capped, randomised delays
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
- Both discover and enrich still return plain Python objects
  (`DiscoveredBusiness`, `CrawlResult`) rather than writing to the
  database — nothing upserts into
  `businesses`/`contacts`/`enrichment_signals` yet, and raw HTML caching
  (`crawl_cache`, so re-running during development never re-crawls a
  page that's still fresh) is designed into the schema but not wired up.
  That persistence layer is intentionally deferred past the "read 200
  rows by hand" checkpoint below.

Per PROJECT.md's build order: config loader → Overpass discoverer →
site crawler → CSV export (all four done) → **read 200 rows by hand
before building anything past this point** — the point is to find out
whether the data is good enough to justify building the sending half at
all. That hand-review is a human task now, not a build step.

### How sending automation will work

- Each **mailbox** (a team member's own Gmail account) authenticates via
  OAuth; the refresh token is stored encrypted, never in git or logs.
- A **campaign** pairs a completed target run with an offer (the pitch,
  also YAML — `config/offers/*.yaml`) and a pool of sender mailboxes.
- **Compose** renders a subject + one generated line per lead from the
  offer's template, grounded in that business's actual signals — no
  message is sent without a human clicking approve on the exact
  rendered text first.
- **Send** enforces, in code, not config:
  - max 50 emails per mailbox per day
  - randomised 90–600 second gaps between sends, no bursts
  - a suppression check (email *or* domain) before every single send —
    unsubscribes are permanent and global across every campaign, forever
- **Monitor** watches for bounces, replies, and unsubscribes via the
  Gmail API and writes to the suppression list automatically.
- The target country is a config flag, because what's legally permitted
  differs — see PROJECT.md's legal posture table (CAN-SPAM / GDPR /
  DPDP). Default posture is the strictest of the three. **This is not
  legal advice — verify before the first real send.**

Also none of this exists yet — it's steps 6–7 of the build order, well
after discover/enrich/qualify have proven the data is worth acting on.

## Project layout

```
src/leadgen/
  config/     target profiles, business types, offers  (implemented)
  discover/   Overpass geocoding + query/parse + filters (implemented; Places/CSV not yet)
  enrich/     site crawler → contacts + signals + qualify (implemented)
  pipeline.py discover → filter → crawl → qualify → CSV  (implemented)
  compose/    template + generated line → draft messages (not yet implemented)
  send/       Gmail OAuth, send queue, caps, suppression (not yet implemented)
  db/         SQLAlchemy models (implemented; nothing writes to it yet)
  api/        FastAPI + review UI                        (not yet implemented)
  util/       normalise_domain() and friends (implemented)
alembic/      migrations
config/       business_types.yaml, offers/*.yaml (example data)
targets/      target profile YAML files (example data)
docs/         one file per completed build-order step
tests/
```
