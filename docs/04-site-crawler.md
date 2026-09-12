# 04 — Site crawler → contacts + signals

Build order step 4.

## What was built

- `src/leadgen/enrich/robots.py` — `RobotsChecker`: fetches and caches
  `robots.txt` per origin, using the same fail-open/fail-closed
  convention as stdlib `RobotFileParser.read()` (404/unreachable →
  allow all; 401/403 → disallow all). This is the one gate every fetch
  goes through — PROJECT.md's crawling hard rule has no exceptions.
- `src/leadgen/enrich/signals.py`:
  - `extract_emails()` — pulls emails from visible page text and
    `mailto:` links only, lowercases them, flags `is_generic` for
    role-address local parts (`info@`, `contact@`, …), and filters out
    a denylist of placeholder/template domains (`example.com`,
    `wixpress.com`, …) that show up constantly in boilerplate markup
    and are never a real contact.
  - `compute_signals()` — the 8 signals from PROJECT.md's example list:
    `no_https`, `no_mobile_viewport` (homepage only), `no_contact_form`,
    `no_online_booking`, `site_platform` (detected from known
    WordPress/Wix/Squarespace/Shopify/Webflow markers), `last_content_year`
    (max year found across pages), `page_weight_mb`, `has_whatsapp_link`
    — all but the homepage-only two are true if ANY crawled page shows
    them.
- `src/leadgen/enrich/crawler.py`:
  - `fetch_page()` — one page, returns `None` on any failure rather than
    raising, so one broken page doesn't abort a business's whole crawl.
  - `crawl_business()` — orchestrates: for each of the profile's
    `crawl_pages` (capped at `max_pages`), checks robots.txt, waits out
    a 1 request/second rate limit, fetches, then hands the results to
    `signals.py`.
- `tests/test_signals.py`, `tests/test_robots.py`, `tests/test_crawler.py`
  — 27 new tests. Signal/email tests run against static HTML fixtures
  (no network at all); robots and crawl-orchestration tests use
  `httpx.MockTransport`.

## Decisions made while building this

- **No email guessing, enforced by what the function *can* do, not just
  a rule someone has to remember.** `extract_emails()` takes HTML and
  returns exactly what's textually present — there's no code path that
  could construct `firstname@domain` even by accident, because the
  function has no concept of "the business's domain" as an input.
- **A placeholder-domain denylist**, not just a generic-local-part
  flag. Template boilerplate (`yourname@example.com`, `@wixpress.com`
  system addresses) shows up on real business sites constantly and
  would otherwise get stored as a contact. This trades a small chance
  of dropping a real `@example.com`-style address (extremely unlikely)
  for reliably not storing dead addresses — matches PROJECT.md's
  precision-over-volume framing directly.
- **One `RobotsChecker` instance is meant to be reused across a whole
  crawl of one business** (and could be shared across many businesses
  on the same host) — it caches per-origin, so calling `can_fetch()`
  repeatedly against the same site costs one `robots.txt` fetch total,
  not one per page.
- **Signal computation is pure and separate from fetching** —
  `compute_signals(pages)` takes already-fetched `PageFetch` objects, no
  I/O. This is what makes 16 of the 27 new tests instant and
  network-free: they construct HTML fixtures directly rather than
  mocking HTTP at all.
- **Still no persistence.** `crawl_business()` returns a `CrawlResult`
  dataclass (pages, contacts, signals) — nothing here writes to
  `contacts`, `enrichment_signals`, or `crawl_cache`. Same reasoning as
  step 3: that's wiring for an orchestration layer that doesn't exist
  yet, not something to bolt onto the crawler itself.

## Verification

- `uv run pytest`: 93/93 passing (66 from steps 1–3, 27 new).
- No live crawl smoke test was run against a real business site for
  this step — see docs/03's note on this sandbox's networking to
  Overpass/Nominatim; the same environment risk likely applies to
  arbitrary third-party sites and is worth checking from a normal
  network before the first real run, not assumed away because the
  mocked suite is green.
