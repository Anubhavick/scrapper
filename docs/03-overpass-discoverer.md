# 03 — Overpass discoverer

Build order step 3: "no API key needed — testable immediately."

## What was built

- `src/leadgen/discover/geocode.py` — `NominatimClient`: resolves a
  place name to `(lat, lon, osm_type, osm_id)` via Nominatim, with:
  - a disk cache keyed by a hash of the (lowercased, stripped) place
    name, so the same city is never geocoded twice, per PROJECT.md §
    Location resolution
  - a 1 request/second rate limiter (Nominatim's usage policy), enforced
    in code, not left to the caller to remember
  - a hard requirement for a real, identifying User-Agent — the
    constructor raises `ValueError` immediately if one isn't given
- `src/leadgen/discover/overpass.py`:
  - `build_query()` builds the Overpass QL string for all four location
    modes: `radius` → `around:<radius_m>,<lat>,<lon>`; `bbox` → the
    explicit box, no geocoding needed; `city`/`admin_area` → an
    `area(...)` clause, computed from the geocoded OSM relation/way id
    (`area_id = osm_id + 3_600_000_000` for a relation, `+
    2_400_000_000` for a way — Overpass's own convention). A place that
    geocodes to a bare point (a node) can't be turned into an area, so
    `city`/`admin_area` mode raises a clear `OverpassError` telling the
    caller to use `radius` or `bbox` instead.
  - `run_query()` executes the query and returns the raw `elements`.
  - `parse_elements()` turns elements into `DiscoveredBusiness` rows,
    skipping (not erroring on) any element with no `name` tag or no
    resolvable coordinate — one malformed element from Overpass
    shouldn't fail the whole discovery run.
  - `discover()` chains all three, capped at the profile's
    `source.max_results`.
- `tests/test_geocode.py`, `tests/test_overpass.py` — 16 new tests, all
  against `httpx.MockTransport`, no real network calls in the suite.

## Decisions made while building this

- **Every network call takes an injected `httpx.Client`** (and
  `NominatimClient` an optional one) rather than constructing its own
  internally with no way to override it. This is what makes the whole
  module testable without hitting real APIs on every `pytest` run — the
  test suite substitutes `httpx.MockTransport` everywhere.
- **The rate limiter lives inside `NominatimClient`, not the caller.**
  A caller forgetting to sleep between geocode calls is exactly the
  kind of mistake that gets an IP banned from Nominatim; making it
  structurally impossible to skip is worth the small constructor
  complexity.
- **Persistence is explicitly out of scope for this step.** `discover()`
  returns plain `DiscoveredBusiness` dataclasses, not `db.models.Business`
  rows, and nothing here touches a database session. Upserting into
  `businesses` (with the dedupe-by-domain-vs-source rules from step 1)
  is a separate concern once there's an actual orchestration entry point
  calling this — don't bolt it on here just because the table exists.

## Verification

- `uv run pytest`: 66/66 passing (50 from steps 1–2, 16 new), all
  against mocked HTTP.
- Attempted a live smoke test against the real Overpass + Nominatim
  APIs using `targets/dentists-gurugram.yaml`, two attempts (a 15km
  radius, then a 2km radius with a 25s client timeout) — both hung
  indefinitely with zero output rather than completing or raising a
  timeout error, and were killed after 18 and ~5 minutes respectively.
  This points at something in the current sandbox/network blocking or
  silently dropping traffic to these specific hosts, not a code defect:
  the exact same `build_query` → `run_query` → `parse_elements` path is
  exercised end-to-end by `test_discover_end_to_end` against mocked
  HTTP and passes, and generic large HTTPS downloads (e.g. GitHub
  release assets, in step 1's setup) did complete on this same network,
  just slowly. Re-run the smoke test script from a normal machine
  before trusting this code against production data for the first
  time — don't take "the mocked tests pass" as proof the real Overpass
  API is reachable from wherever this actually deploys.
