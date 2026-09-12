# PROJECT.md — Lead Generation & Cold Outreach System

## What we are building

A self-hosted tool that:

1. **Finds** businesses matching a target profile (type + location + filters)
2. **Enriches** each one by crawling its own website for contact details and factual signals
3. **Generates** a personalised cold email grounded in those signals
4. **Sends** it through each team member's own Gmail account, under strict rate caps
5. **Tracks** replies, bounces and unsubscribes, and suppresses permanently

Small team tool. Runs on one Linux VM. Near-zero running cost.

**Design principle:** the sending ceiling is ~50 emails per mailbox per day. With 3 mailboxes that is 150/day. This system is not optimised for volume — it is optimised for *precision*, because the volume is fixed by deliverability limits regardless of how good the scraper is.

---

## The central abstraction: target profiles

Everything about *who* we contact lives in a YAML file. Changing business type, city, radius or filters must never require a code change. This is the requirement the architecture is built around.

```
targets/
  dentists-gurugram.yaml
  gyms-delhi-ncr.yaml
  plumbers-houston.yaml
```

### Example target profile

```yaml
name: dentists-gurugram
enabled: true

business_type: dentist          # resolved via config/business_types.yaml

location:
  mode: radius                  # radius | bbox | city | admin_area
  center: "Gurugram, Haryana, India"
  radius_km: 15

source:
  primary: overpass             # overpass | places | csv
  fallback: places              # only used to fill gaps; see ToS note below
  max_results: 500

filters:
  must_have_website: true
  must_have_phone: false
  exclude_domains:              # skip franchises / chains / aggregators
    - practo.com
    - justdial.com
  exclude_name_patterns:
    - "(?i)apollo"
    - "(?i)fortis"
  min_name_length: 3

enrichment:
  crawl_pages: [/, /contact, /about, /services, /book]
  max_pages: 8
  signals:
    - no_https
    - no_mobile_viewport
    - no_contact_form
    - no_online_booking
    - site_platform
    - last_content_year
    - page_weight_mb
    - has_whatsapp_link

qualification:                  # a lead is only sendable if this passes
  require_email: true
  require_any_signal: [no_online_booking, no_contact_form, last_content_year]
  min_signal_count: 1

outreach:
  offer_id: appointment-automation
  sender_pool: [sales1, sales2]
  daily_cap_per_mailbox: 40
```

### Business type registry

One file maps a human name to the identifiers each data source uses. Adding a new vertical = adding one block here, no code.

```yaml
# config/business_types.yaml
dentist:
  osm:
    - amenity=dentist
    - healthcare=dentist
  places_types: [dentist]
  keywords: [dental clinic, dentist, orthodontist]

gym:
  osm:
    - leisure=fitness_centre
  places_types: [gym]
  keywords: [gym, fitness centre, crossfit]

plumber:
  osm:
    - craft=plumber
    - shop=trade
  places_types: [plumber]
  keywords: [plumbing, plumber, drain repair]
```

### Location resolution

`location.center` accepts a place name and is resolved to coordinates / a bounding box via **Nominatim** (free, no key, 1 req/sec limit, must send a real User-Agent). Resolved values are cached to disk so the same city is never geocoded twice.

Modes:
- `radius` — centre point + km, becomes an Overpass `around` query
- `bbox` — explicit `[south, west, north, east]`
- `city` / `admin_area` — Overpass area query by OSM relation, more accurate than a circle for irregular city shapes

### Offers

The pitch is also config, because the same lead list gets tested against different angles.

```yaml
# config/offers/appointment-automation.yaml
id: appointment-automation
subject_templates:
  - "{business_name} — missed calls after hours?"
  - "quick question about {business_name}'s bookings"
body_template: templates/appointment_automation.txt
relevant_signals: [no_online_booking, no_contact_form]
cta: "Worth a 10-minute call this week?"
```

---

## Pipeline

```
  target profile (YAML)
        │
        ▼
  [1] DISCOVER ──────► businesses          (Overpass / Places / CSV)
        │
        ▼
  [2] CRAWL ─────────► contacts + signals  (their own website only)
        │
        ▼
  [3] QUALIFY ───────► sendable leads      (rules from the profile)
        │
        ▼
  [4] COMPOSE ───────► drafts              (template + 1 generated line)
        │
        ▼
  [5] REVIEW ────────► human approval      (mandatory, in the UI)
        │
        ▼
  [6] SEND ──────────► Gmail API, capped, randomised delays
        │
        ▼
  [7] MONITOR ───────► replies / bounces / unsubscribes → suppression
```

Each stage is independently runnable and idempotent. Re-running stage 2 on an
existing dataset must not duplicate rows or re-crawl anything still fresh.

---

## Data model

| Table | Purpose | Key constraints |
|---|---|---|
| `businesses` | one row per company | unique on normalised domain; unique on `(source, source_id)` |
| `contacts` | emails found for a business | unique on `(business_id, email)`; flag `is_generic` for `info@`-style |
| `enrichment_signals` | key/value facts from the site | unique on `(business_id, key)` |
| `crawl_cache` | raw HTML + fetched_at | prevents re-crawling during development |
| `target_runs` | one row per profile execution | records the profile hash so results are reproducible |
| `campaigns` | profile + offer + sender pool | |
| `messages` | queued / approved / sent / bounced / replied | unique on `(campaign_id, contact_id)` |
| `suppressions` | email or domain, with reason | checked before **every** send, forever |
| `mailboxes` | OAuth refresh token, daily counter | |

**Domain normalisation** (lowercase, strip `www.`, strip path/query, strip trailing dot) is the single most important function in the codebase. Every dedupe path goes through it. It gets unit tests first.

---

## Hard rules

These are non-negotiable and enforced in code, not by configuration:

- **Send caps.** Max 50/mailbox/day, randomised 90–600s gaps. No bursts.
- **Suppression check** before every single send. Unsubscribes are permanent and global across all campaigns.
- **Provenance.** Every contact row records source, fetched_at, and legal basis.
- **Crawling.** `robots.txt` respected, 1 req/sec per host, identifying User-Agent with a contact URL, raw HTML cached.
- **No email guessing.** Only addresses present on the business's own site or in OSM tags. No `firstname@domain` permutation, no SMTP probing.
- **Google Places content is not persisted beyond 30 days** — only `place_id` is storable long-term (Maps Platform ToS). OSM data (ODbL) has no such restriction, which is why Overpass is the primary source.
- **Human approval gate.** No message leaves the system without a person clicking approve on the final text.
- **Secrets** never in git, never in logs. OAuth refresh tokens encrypted at rest.

---

## Legal posture

The target country is a **config flag**, because it changes what is permitted:

| Region | Basis | Requirement |
|---|---|---|
| US | CAN-SPAM | physical address in footer, working opt-out, truthful headers |
| EU / UK | GDPR | legitimate interest is contested; some states are opt-in only. Profile must set `requires_opt_in: true` and the send stage refuses to run |
| India | DPDP | prefer generic role addresses (`info@`, `contact@`) over named individuals |

Default posture is the strictest of the three. Not legal advice — verify before the first send.

---

## Stack

Python 3.12 + `uv` · FastAPI · Postgres 16 · Redis + RQ · `httpx` + `selectolax` · Alembic · pytest · Docker Compose.

Crawler runs from a residential IP (a laptop) where possible — datacenter IPs get blocked far faster. App and database run on the VM.

---

## Build order

1. Skeleton, docker-compose, Alembic, schema, domain normalisation + its tests
2. Config loader: target profiles, business types, offers — with Pydantic validation and clear errors on a bad YAML
3. Overpass discoverer (no API key needed — testable immediately)
4. Site crawler → contacts + signals
5. CSV export, then **read 200 rows by hand** before building anything else
6. Gmail OAuth + send queue + caps + suppression
7. Bounce and reply handling
8. FastAPI + minimal review UI
9. Deploy

Steps 1–5 require no domains, no API keys and no money. They tell us whether the data quality justifies building the second half.

## Parallel track (start now, has a waiting period)

Register the sending domain, create mailboxes, configure SPF/DKIM/DMARC, begin warmup. Takes 2–3 weeks and cannot be compressed by writing code faster.
