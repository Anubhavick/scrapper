# 02 — Config loader: target profiles, business types, offers

Build order step 2.

## What was built

- `src/leadgen/config/models.py` — Pydantic schemas for the three YAML
  surfaces PROJECT.md defines:
  - `TargetProfile` (with `Location` as a discriminated union on
    `mode`: `radius` | `bbox` | `city` | `admin_area`, each mode
    requiring different fields)
  - `BusinessTypeDef` (the `business_types.yaml` registry)
  - `Offer` (`config/offers/*.yaml`)
  - `extra="forbid"` on every model, so a typo'd YAML key fails at load
    time instead of being silently dropped.
  - `KNOWN_SIGNALS` — the enrichment signal names from PROJECT.md's
    example list. Any signal referenced in a profile's `qualification`
    section or an offer's `relevant_signals` is checked against this
    set — an unrecognised signal name is treated as a typo, not a
    forward-compatible extension point.
- `src/leadgen/config/loader.py` — `load_business_types`, `load_offer`,
  `load_offers`, `load_target_profile`, `load_target_profiles`. Every
  failure path — missing file, malformed YAML, schema violation,
  unknown `business_type`, unknown `offer_id` — raises `ConfigError`
  with the file path in the message, never a bare
  `pydantic.ValidationError` or `yaml.YAMLError`.
- Real example config files, taken directly from PROJECT.md's own
  examples, so the loader has something real to load:
  - `config/business_types.yaml` (dentist, gym, plumber)
  - `config/offers/appointment-automation.yaml`
  - `targets/dentists-gurugram.yaml`
- `tests/test_config.py` — loads the real example files end-to-end, plus
  synthetic `tmp_path` fixtures for every error path listed above.

## Decisions made while building this

- **Cross-file validation is explicit, not automatic.** `business_type`
  and `outreach.offer_id` are checked against the loaded registries only
  when those registries are passed in — `load_target_profile(path,
  business_types)` will validate structure but skip the offer check if
  `offers` isn't given. This matches how the loader will actually be
  called (business types are always available; offers might not be, if
  someone's only validating profile syntax).
- **Offer `id` must match its filename stem.** Not required by
  PROJECT.md, but `config/offers/foo.yaml` containing `id:
  something-else` is exactly the kind of thing that causes a silent
  mismatch later when a profile's `outreach.offer_id` references the
  filename instead of the internal id (or vice versa). Enforcing they're
  the same removes an entire class of bug for one cheap check.
- **`body_template`'s file existence is not validated here.** It's a
  plain string field. Whether `templates/appointment_automation.txt`
  actually exists is the compose stage's problem (step 4) — the config
  loader validates the *shape* of config, not every filesystem
  reference inside it. Revisit this if it turns out to be a frequent
  source of late-discovered breakage.
- **Bbox coordinates are range- and order-checked** (`south < north`,
  `west < east`, valid lat/lng ranges) since a reversed or malformed
  bbox produces a query that Overpass will happily run and return
  either nothing or the wrong hemisphere's businesses — worth catching
  before hitting the API, not after getting an empty result set and
  wondering why.

## Verification

`uv run pytest`: 50/50 passing (31 from step 1, 19 new).
