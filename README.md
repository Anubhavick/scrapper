# Lead Generation & Cold Outreach System

A self-hosted pipeline that finds businesses matching a target profile,
crawls their own websites for contact details and factual signals,
drafts a personalised cold email grounded in those signals, and sends it
through a team member's own Gmail account under strict rate caps — with
a mandatory human-approval step and permanent suppression tracking.

Full spec, data model, and legal posture: [PROJECT.md](PROJECT.md).
Notes for anyone (human or Claude) working in this codebase:
[CLAUDE.md](CLAUDE.md).

## Status: skeleton

This repo currently has the schema, the project structure, and one
utility function — **no discovery, crawling, drafting, or sending logic
exists yet.** Nothing in this README's "how it will work" sections below
is runnable today. What *is* here and working:

- Postgres 16 + Redis, via Docker Compose
- The full database schema (10 tables), migrated with Alembic
- `normalise_domain()` — the domain-dedup function everything else will
  depend on — with a passing test suite
- Empty, correctly-structured packages for every pipeline stage

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

### How scraping (discover + enrich) will work

- **Discover** resolves the profile's `location` (a place name, via
  Nominatim, cached to disk) into a bounding box or radius, then queries
  OpenStreetMap's Overpass API for businesses matching the profile's
  `business_type` — no API key needed. Google Places is a configured
  fallback, used only to fill gaps, because Places content can't be
  persisted beyond 30 days per its ToS (OSM's ODbL has no such limit,
  which is why Overpass is primary).
- **Enrich** crawls a small, profile-specified set of pages on each
  business's *own* website (`/`, `/contact`, `/about`, …) — never a
  third party — respecting `robots.txt`, at 1 request/second per host,
  with a real User-Agent that includes a contact URL. It extracts:
  - contact emails (only ones actually present on the site — **no
    guessing, no `firstname@domain` permutation, no SMTP probing**)
  - factual signals the profile asks for (no HTTPS, no mobile viewport,
    no booking widget, site platform, last-updated year, page weight,
    WhatsApp link presence, etc.)
- A business only becomes a **sendable lead** if it passes the profile's
  `qualification` rules (has an email, has at least N of the requested
  signals).
- Raw HTML is cached (`crawl_cache`) so re-running discover/enrich during
  development never re-crawls a page that's still fresh.

None of this exists yet. Build order (from PROJECT.md): config loader →
Overpass discoverer → site crawler → CSV export → **read 200 rows by
hand before building anything past this point** — the point is to find
out whether the data is good enough to justify building the sending
half at all.

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
  config/    target profiles, business types, offers  (not yet implemented)
  discover/  Overpass / Places / CSV business discovery (not yet implemented)
  enrich/    site crawler → contacts + signals          (not yet implemented)
  compose/   template + generated line → draft messages (not yet implemented)
  send/      Gmail OAuth, send queue, caps, suppression (not yet implemented)
  db/        SQLAlchemy models (implemented)
  api/       FastAPI + review UI                        (not yet implemented)
  util/      normalise_domain() and friends (implemented)
alembic/     migrations
tests/
```
