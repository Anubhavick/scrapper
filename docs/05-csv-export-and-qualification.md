# 05 — Filters, qualification, and CSV export

Build order step 5: "CSV export, then read 200 rows by hand before
building anything else."

## What was built

- `src/leadgen/discover/filters.py` — `apply_filters()`: applies a
  target profile's `filters` section (`must_have_website`,
  `must_have_phone`, `min_name_length`, `exclude_domains`,
  `exclude_name_patterns`) to a batch of `DiscoveredBusiness` rows
  *before* any crawling happens — filtering here, not after
  enrichment, is what keeps the 1-req/sec crawl budget from being spent
  on businesses the profile explicitly excludes (chain listings,
  aggregators, short/garbage names).
- `src/leadgen/enrich/qualify.py` — `is_qualified()`: applies a
  profile's `qualification` section (`require_email`,
  `require_any_signal`, `min_signal_count`) to one business's crawl
  results. See the flagged gap below before trusting this against real
  data.
- `src/leadgen/pipeline.py` — `run_target_profile()` and `export_csv()`:
  the actual end-to-end wiring — discover → filter → crawl each
  survivor → qualify → one `LeadRow` per business → CSV. This is the
  first module that ties discover and enrich together into something
  runnable against a real target profile.
- `tests/test_filters.py`, `tests/test_qualify.py`, `tests/test_pipeline.py`
  — 16 new tests, all against mocked HTTP or in-memory fixtures.

## The gap worth reading before using this for real

**`qualify.py`'s handling of non-boolean signals is a placeholder, not
a considered answer.** PROJECT.md's own example profile puts
`last_content_year` in `require_any_signal` — a signal whose value is a
year, not a bool — but never says what value should count as "stale."
Is 2022 old? 2018? There's no threshold anywhere in the spec. Inventing
one here (e.g. "counts if more than 2 years old") would be quietly
deciding which real businesses get contacted, which is a product
decision, not a plumbing detail, and picking an arbitrary number
wasn't something to bury in code without saying so.

What's actually implemented: a non-boolean signal counts toward
`require_any_signal` when its value is truthy (the crawler found *some*
value at all) — not when the value indicates an actual problem. This
means, right now, `last_content_year` and `page_weight_mb` are
functionally almost useless as qualification signals (they'll count as
"present" for nearly every business with any footer year or any page
weight at all). Boolean signals (`no_https`, `no_mobile_viewport`,
`no_contact_form`, `no_online_booking`, `has_whatsapp_link`) are
unaffected by this — they're unambiguous.

**This is exactly what the "read 200 rows by hand" step exists to
surface.** Run the pipeline against a real profile, look at the CSV,
and see whether qualification is letting through businesses it
shouldn't (or excluding ones it shouldn't) because of this gap. If so,
the fix is a config decision (a threshold, or a per-signal comparison
operator in the YAML schema) — flag it back and it can be designed
properly instead of guessed at here.

## Other decisions made while building this

- **Still no database.** `run_target_profile()` returns in-memory
  `LeadRow` objects; `export_csv()` writes them straight to a file. No
  session, no upsert, nothing touches `businesses`/`contacts`/
  `enrichment_signals`/`crawl_cache`. PROJECT.md's step 5 is explicitly
  about proving the data is worth acting on *before* investing in
  anything else — that includes the persistence layer, which isn't
  needed to produce a CSV and would be premature to build before
  knowing whether steps 1–4's output quality justifies it.
- **A business with no website still gets a row**, with `qualified`
  almost certainly `False` (no email, unless `require_email: false`).
  Excluding no-website businesses entirely from the CSV would hide
  exactly the kind of "how many did discover even find" signal that
  the hand-review step needs to see.
- **`exclude_domains` matching reuses `normalise_domain()`** rather
  than a raw string comparison — `www.Practo.com` and `practo.com` are
  the same exclusion either way, which is the whole point of that
  function existing in step 1.

## Verification

`uv run pytest`: 109/109 passing (93 from steps 1–4, 16 new).

No live run against a real target profile yet — this needs the same
Overpass/Nominatim reachability check flagged in docs/03, plus an
actual website to crawl. Worth doing as the very next thing, on a
normal network, before deciding whether this gap in `qualify.py`
matters in practice.
