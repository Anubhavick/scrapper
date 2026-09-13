# 10 — Scan-builder UI

Second of the three UI pages the user asked for (history/review, done in
docs/09, was first): a form that writes a `targets/*.yaml` file, so
starting a new scan no longer requires hand-editing YAML and knowing
`TargetProfile`'s shape from memory.

## What was built

- `src/leadgen/api/targets.py` — new routes mounted onto the existing
  `app` from `api/review.py` via `app.include_router(...)`:
  - `GET /targets` — every `targets/*.yaml` file, parsed through the real
    loader (`load_target_profile`) so business_type/offer_id references
    that don't actually exist show up as "Invalid" inline rather than
    crashing the whole page. Each row links to View / Duplicate / History
    (the last goes to `/runs?target_name=...`, see below).
  - `GET /targets/new` (optionally `?from=<name>` to prefill from an
    existing profile) and `POST /targets` — the form itself. Every field
    maps onto a `TargetProfile` field; submission is validated by
    `TargetProfile.model_validate()` directly, the same model
    `config/loader.py` uses, so a profile built here can't be more
    permissive than one written by hand. Business_type/offer_id are also
    cross-checked against `config/business_types.yaml`/`config/offers/`,
    same as the loader does for a hand-written file.
  - `GET /targets/{name}` — the raw YAML plus the copy-pasteable
    `run_pipeline.py` command. Does **not** trigger a scan itself — see
    "What this deliberately doesn't do" below.
- `src/leadgen/api/nav.py` — a three-link nav bar (Targets / Run history
  / Lead review) shared by every page across `review.py` and
  `targets.py`, in its own tiny module specifically so the two files
  don't have to import each other.
- `src/leadgen/api/review.py` — `/runs` gained an optional
  `?target_name=` filter, so "History" on a target's row (or its own
  page) shows only that target's runs instead of every run ever.

## Two things worth knowing about how it writes files

1. **Create-only, never overwrite.** Submitting a name that already
   matches `targets/<name>.yaml` is rejected with an inline error, not
   silently clobbered — someone's hand-tuned `exclude_domains` list (the
   whole point of docs/07) shouldn't be one form submission away from
   being erased. Edit an existing file by hand; use this UI to start a
   new one, optionally seeded from an existing one via "Duplicate."
2. **The `name` field is a filename, so it's sanitised like one.** Only
   lowercase letters, digits, and hyphens are accepted
   (`^[a-z0-9][a-z0-9-]{0,62}$`) — this is what stops
   `../../etc/passwd`-shaped input from writing (or, on the view route,
   reading) outside `targets/`. Covered by
   `test_create_target_rejects_unsafe_or_invalid_names` and
   `test_show_target_404_for_path_traversal_attempt`.

## What this deliberately doesn't do

- **Doesn't trigger a scan.** `run_target_profile()` makes real Overpass
  and per-business HTTP calls (`robots.txt`-respecting, rate-limited) and
  can take minutes — running it synchronously inside an HTTP request
  handler would mean either a browser-timeout footgun or building a
  background-job system, which is squarely the orchestration-loop work
  CLAUDE.md already defers to the campaigns step. `/targets/{name}` shows
  the exact CLI command instead.
- **No edit-in-place for an existing profile.** Once created, a profile
  is either hand-edited or duplicated into a new one — no `PUT`/edit
  form. Revisit if this turns out to be annoying in practice.
- **`sender_pool` names aren't validated against real mailboxes.** Same
  reason `outreach` in general is mostly inert right now: `mailboxes`
  rows only exist for accounts actually run through
  `scripts/authorize_mailbox.py` (HOWTO.md), and nothing yet cross-checks
  a profile's `sender_pool` against that table — this is really a
  campaigns-step concern (a campaign is where sender pool meets real
  mailboxes), not this form's.

## Verification

`uv run pytest`: 188/188 passing (172 prior + 16 new in
`tests/test_targets_api.py`, all against an isolated `tmp_path`
targets/business_types/offers set via `monkeypatch` — this module has no
Postgres dependency at all, so unlike docs/09's routes it's fully covered
by the automated suite).

Verified for real in a browser (Playwright): filled out the full form for
a new `gyms-denver-co` profile (radius location, two enrichment signals,
one qualification signal, a sender pool) and submitted it; the written
`targets/gyms-denver-co.yaml` loaded successfully through the real
`load_target_profile()` (not a test double) with correct types (e.g.
`radius_km: 10.0`, `sender_pool: [sales1]`). Re-submitting the same name
was rejected with both the expected pydantic error (`radius_km` at 0
violates `gt=0`) and the duplicate-file error shown together, the
existing file confirmed byte-for-byte unchanged afterward. Confirmed
`/targets/new?from=dentists-austin-tx` prefills every field including
`exclude_domains: [touchto.io]` from the real target file. Test artifact
removed after verification, not left in the repo.
