# 20 — legal_region: making PROJECT.md's GDPR/DPDP posture real

Flagged as a gap review found: PROJECT.md's legal-posture table says a
GDPR-region profile "must set `requires_opt_in: true` and the send stage
refuses to run" — but there was no `legal_region` or `requires_opt_in`
field anywhere in `config/models.py`, and nothing in the send path
checked either. Nothing stopped this system from being pointed at
EU/UK leads with zero opt-in guard; the hard rule existed only in
PROJECT.md's prose.

## What was built

- **`src/leadgen/config/models.py`** — `TargetProfile` gained
  `legal_region: Literal["us", "eu_uk", "india"]` (**required, no
  default** -- PROJECT.md frames this as "the target country is a
  config flag," so a silent default would mean an author who forgets
  the field gets whatever posture the default happens to be, unnoticed;
  required forces the same explicit per-profile decision for every
  profile that has ever existed or ever will) and `requires_opt_in: bool
  = False`. Named `legal_region`, not `region` — `Business.region`
  (db/models.py) already means "state/province of a scraped address";
  reusing the name would have been a real source of confusion. A new
  `@model_validator(mode="after")` raises at config-load time if
  `legal_region == "eu_uk"` and `requires_opt_in` isn't `True`.
- **`src/leadgen/db/orchestration.py`** — the actual send-stage refusal.
  `requires_opt_in: true` on an eu_uk profile only makes the requirement
  visible in the YAML; it does not make a send legal, because nothing in
  this codebase collects or records consent anywhere (leads come from
  public business listings, not a signup form). So `build_send_jobs()`
  now resolves each message's campaign → `target_runs.profile_yaml` (the
  immutable per-run snapshot already stored for reproducibility, see
  CLAUDE.md) and excludes any message whose profile is `legal_region:
  eu_uk` — unconditionally, `requires_opt_in` or not — logged at
  **ERROR** (a compliance block, not an operational one, unlike the
  inactive-mailbox exclusion next to it which logs at WARNING). This
  covers both `preview_approved_messages()` and `run_approved_messages()`
  since both call `build_send_jobs()`. Resolution is cached per
  `campaign_id` for the life of one call — a profile snapshot can't
  change mid-run, so re-parsing its YAML per message would be wasted
  work, never a freshness concern.
- **`src/leadgen/api/targets.py`** — the scan-builder form gained a
  required "Legal region / posture" select (no preselected value) and a
  "Requires opt-in" checkbox in the Identity fieldset, with inline
  copy stating plainly that checking it does not unlock sending. Without
  this the create/edit form would have started rejecting every
  submission the moment `legal_region` became required.
- **`targets/dentists-austin-tx.yaml`** → `legal_region: us`;
  **`targets/dentists-gurugram.yaml`** → `legal_region: india`. Both
  real, existing profiles needed this added or they'd stop loading.
- Tests: `tests/test_config.py` (missing field / eu_uk-without-opt-in /
  eu_uk-with-opt-in / us-defaults-false / unknown-region, all via
  `ConfigError`), `tests/test_targets_api.py` (create rejects missing
  region and eu_uk-without-opt-in, accepts eu_uk-with-opt-in). Existing
  fixtures in `test_qualify.py`, `test_overpass.py`, `test_pipeline.py`,
  and `test_targets_api.py`'s shared helpers updated with a
  `legal_region` so they kept validating at all.

## What this does *not* do

- **India/DPDP's softer requirement is still unenforced.** PROJECT.md's
  table says DPDP leads should "prefer generic role addresses (`info@`,
  `contact@`) over named individuals" — a preference, not a "refuses to
  run" hard rule like GDPR's, and `db/campaigns.py`'s
  `_select_best_contact()` still does the opposite (prefers a named
  contact) regardless of `legal_region`. Left alone here to keep this
  change scoped to the one hard rule that was actually missing; worth
  a follow-up if DPDP-region sending becomes real rather than
  hypothetical.
- **No UI visibility for a legal_region-blocked message.** It's excluded
  from `build_send_jobs()`'s output silently (from the campaigns UI's
  perspective) and only visible via the ERROR-level log line — there's
  no "blocked, and here's why" banner anywhere in `/campaigns/{id}`.
  Not urgent: no real eu_uk profile exists yet, and creating one is now
  gated hard enough (config load fails without `requires_opt_in: true`)
  that this isn't a silent trap the way an inactive mailbox was.

## Verification

Real Postgres (`docker compose up -d` + `alembic upgrade head`, both
already applied — no new migration needed, this is config-layer +
application-layer only, no schema change). Ran a disposable script:
created two full scenarios (business/contact/mailbox/target_run/
campaign/message) end to end, one with a `legal_region: eu_uk,
requires_opt_in: true` profile, one with `legal_region: us` — called
`build_send_jobs(session, campaign_id=...)` against real Postgres for
each.

- **Result:** eu_uk campaign → 0 jobs (fully blocked, ERROR logged:
  `target profile legal_region='eu_uk' has no built opt-in mechanism --
  sends are blocked until one exists`); us campaign → 1 job, unaffected.
  All disposable rows deleted afterward.
- `uv run pytest`: 263/263 passing (was 253 before this change; +10 new
  across `test_config.py` and `test_targets_api.py`, plus the 4 fixture
  updates needed to keep existing tests validating).
