# 07 — Lead-review UI (scoped-down step 8), plus fixes from reading real data

Build order step 8 is "FastAPI + minimal review UI." Step 7 (bounce/reply
handling) is still untouched — this is the same kind of out-of-order build
docs/06 already flagged for step 6, done for the same reason: there was a
concrete, immediate need (a human had to read real scrape output) and no
dependency on the skipped step.

## Why this, and why now

The 3km-radius Austin dentist profile only ever found 6 businesses — too
few to be the "read 200 rows by hand" checkpoint PROJECT.md's build order
calls for after step 5. Widening it to 15km and actually reading the
32-row (then 31, after a fix below) result surfaced three real problems
that a raw CSV made hard to see:

- Sites that failed every crawl attempt looked identical to sites with
  genuinely empty signals — both just showed `{}` and no tags, unless you
  went and parsed the `tags` string for `error-` substrings.
- A site that failed on all 4 configured crawl pages got the same error
  tag repeated 4 times (`error-connection;error-connection;...`),
  because `tags = compute_tags(...) + errors` was never deduped.
- OSM tags `touchto.io` (a digital agency) as a `dentist`. There's no
  cross-source validation to catch a mistagged node — see CLAUDE.md's
  schema-decisions note on `businesses` having no automatic merge/
  validation logic. A human has to catch this by eye, every time.

## What was built

- `src/leadgen/pipeline.py`: added a `crawl_status` CSV column (`ok` /
  `partial` / `unreachable` / `no_website`), computed from whether the
  business had a website, whether any pages were actually fetched, and
  whether any errors occurred. Tags are now deduped
  (`dict.fromkeys(...)`) before being joined — an unreachable site now
  shows `error-connection` once, not once per configured crawl page.
- `targets/dentists-austin-tx.yaml`: `radius_km` 3 → 15, and `touchto.io`
  added to `filters.exclude_domains` with a comment explaining why. This
  is the reusable fix for OSM mistagging — not a code change, just using
  a filter that already existed.
- `src/leadgen/api/review.py`: a FastAPI app — the actual step 8 work,
  deliberately scoped down. It reads the CSV `pipeline.py` already
  writes (not a database — persistence is still unbuilt, see CLAUDE.md),
  renders a filterable/searchable table (qualified, crawl_status, name
  search), and gives every row with a website a **Reject** button that
  calls `add_excluded_domain()` — a targeted text edit (not a
  `yaml.safe_dump` round-trip, which would silently drop the
  hand-written comment next to `exclude_domains`) that appends a domain
  to the profile's existing denylist. Run it with:
  ```
  uv run uvicorn leadgen.api.review:app --reload
  ```
  then open `http://127.0.0.1:8000/?csv=<path>&profile=<path>`.
- `tests/test_review_api.py`: 7 tests via `fastapi.testclient.TestClient`
  covering rendering, both filters, the reject POST + redirect, and
  `add_excluded_domain()`'s idempotency and comment-preservation.
- Added `python-multipart` to `pyproject.toml` — required by FastAPI for
  any `Form(...)` parameter, only discovered by actually running the
  test suite against the new module.

## What this UI deliberately does not do

**It does not approve outreach messages before send.** PROJECT.md's hard
rule — no send without a human clicking approve on the exact rendered
text — needs `campaigns`/`messages` rows to exist to approve in the first
place, and nothing creates those yet (see CLAUDE.md's orchestration-loop
gap, also still open). Building a fake approval flow against data that
doesn't exist yet would be worse than not building it. This UI is the
**lead-review** half of step 8 only: browse real discover/enrich output,
reject bad matches. The **message-review** half is follow-on work, gated
on the orchestration loop existing first.

## A real bug caught by actually opening a browser

The first version of `_row_html` did
`escape(row.get('emails', '') or '<span ...>none</span>')` — escaping the
literal fallback HTML along with the real data, so any row with no email
rendered the raw `<span style="color:#cf222e;">none</span>` tag as visible
text instead of a styled badge. Caught by loading the page in a real
browser (Playwright) rather than trusting the unit tests, which only
asserted the *data* appeared, not that the markup around it was valid.
Fixed by escaping only the actual email string and treating the fallback
as already-safe literal HTML.

## Not done here, still real gaps

- **`last_content_year`'s regex-over-full-page-text approach is still
  unfixed.** Flagged during the same review (it picks up "© 2026" footers
  as the page's "last content"), not part of this batch of fixes since it
  wasn't blocking anything and wasn't asked for this round.
- **No visual indication in the UI that a row's domain is already in
  `exclude_domains`** from a previous session — reject is a write, not a
  read-then-render. A rejected row stays visible (correctly reflecting
  that the current CSV was generated before the exclusion existed) until
  the next real scrape drops it.
- Still reads a CSV path, not a database — multiple people reviewing the
  same run, or reviewing across runs, isn't supported yet.

## Verification

`uv run pytest`: 170/170 passing (163 before this batch, 7 new).
Manually exercised end-to-end in a real browser (Playwright): loaded the
real 31-row Austin CSV, confirmed filters narrow the table correctly,
clicked Reject on a row against scratch copies of the CSV/profile (not
the real files) and confirmed the redirect, the on-page notice, and the
actual text of the scratch profile file all updated correctly with the
existing comment preserved.
