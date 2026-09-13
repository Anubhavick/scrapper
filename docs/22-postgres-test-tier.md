# 22 — a real Postgres-backed test tier

Flagged as a gap review found (ROADMAP.md item 12): `db/repository.py`,
`db/persist.py`, `db/campaigns.py`, `db/orchestration.py`, and every
DB-backed `api/` route were excluded from `pytest` by construction —
their JSONB/UUID columns aren't SQLite-compatible (CLAUDE.md's
Architecture notes). Worth naming plainly: every real bug found across
docs/08 through docs/20 (the shared-domain crash, the eager-evaluation
suppression-order bug, the double-reservation race under two overlapping
runs, the `Form(...)`-empty-string footgun, the `RQ` timeout being
swallowed, ...) was caught by manual verification against a real
Postgres, never by an automated test — because nothing automated
watched these modules at all.

## What was built

- **`testcontainers[postgres]`** added to the `dev` dependency group
  (`pyproject.toml`) — spins up a completely separate, ephemeral
  Postgres 16 container per test session, **never** the dev
  `DATABASE_URL` from `.env`. This was a deliberate, non-negotiable
  design constraint: that database holds real campaign/send data (a
  real send happened 2026-09-13, see CLAUDE.md), and reusing it for
  tests — even read-mostly ones — would risk a test's cleanup step
  touching a real record. An ephemeral per-session container makes that
  structurally impossible instead of relying on every future test
  author remembering not to point at the wrong URL.
- **`tests/conftest.py`** — the fixtures every `test_*_db.py` file
  builds on:
  - `db_engine` (session-scoped): starts the container, applies the
    **real Alembic migration history** (`alembic.command.upgrade(cfg,
    "head")`, not `Base.metadata.create_all()` — this exercises the
    same schema-creation path production does, migration bugs
    included), and yields a plain SQLAlchemy engine. Skips (not errors)
    the whole tier if Docker isn't reachable — confirmed by running the
    full suite with `DOCKER_HOST` pointed at a nonexistent socket: 263
    passed, 53 (every new DB-tier test) skipped, 0 failed. `uv run
    pytest` on a machine with no Docker daemon still passes.
  - `db_session` (function-scoped): one `Session` per test wrapped in
    the standard SQLAlchemy join-to-an-external-transaction/SAVEPOINT
    recipe — even a real `session.commit()` inside the code under test
    (`db/orchestration.py`'s `_reserve_fn` commits on purpose, mid-lock)
    rolls back at teardown instead of leaking into the next test. For
    direct `db/*.py` function tests.
  - `db_env` (function-scoped): monkeypatches
    `leadgen.db.session`'s cached `_engine`/`_sessionmaker` globals to
    point at the container, so real API routes (`session_scope()` is a
    module-level function they call directly, not a FastAPI `Depends` —
    can't be swapped via `dependency_overrides`) transparently use the
    test database. Cleans up with a real `TRUNCATE ... CASCADE` after
    each test, since route code commits for real against its own pooled
    connections (not the single connection `db_session` controls).
  - `client`: an authenticated `TestClient` for the real app, built on
    `db_env`.
  - Plain factory helpers (`make_business`, `make_contact`,
    `make_mailbox`, `make_target_run`, `make_campaign`, `make_message`)
    — every required-per-constraint field gets a valid default so each
    test only spells out what's actually different about it.
- **Seven new test files**, ~50 new tests, all against the real
  container:
  - `test_repository_db.py` — `count_sent_today`, `reserve_send_slot`,
    `fetch_suppressions`, plus a deliberate "isolation canary" pair of
    tests proving the SAVEPOINT-rollback fixture itself actually
    isolates tests from each other (not just asserting it works, but
    checking a row written in one test is genuinely gone in the next).
  - `test_persist_db.py` — including docs/08's exact real bug (two
    OSM nodes sharing one corporate domain: the second gets
    `normalized_domain=None` instead of crashing on the partial unique
    index) as a permanent regression test.
  - `test_campaigns_db.py` — `create_campaign`, `generate_campaign_
    messages` (idempotency on rerun, the no-contact skip path, the
    signals-no-longer-justify-the-offer skip path), using the real
    `appointment-automation` offer/template files, not fakes.
  - `test_orchestration_db.py` — the highest-value file: `build_send_
    jobs` (approved-only filter, inactive-mailbox exclusion, mailbox
    reassignment, and docs/20's `legal_region` gate, all now automated
    instead of only manually verified), `preview_approved_messages`'s
    genuine zero-write guarantee, `run_approved_messages` end-to-end
    with a faked Gmail call (send success, send failure recording
    `Mailbox.last_send_error`, an unhealthy-token preflight block), and
    — the one that matters most — **a real two-thread concurrency test
    of `_reserve_fn`'s advisory lock**: two independent Postgres
    connections race the same mailbox's last cap slot, and the test
    asserts exactly one message ends up `sent`, never both. Correctness
    here comes from Postgres serialising the two transactions on
    `pg_advisory_xact_lock`, not from Python-level timing, so this is a
    deterministic test, not a flaky race — the double-send-prevention
    property this whole system depends on had never been exercised by
    an automated test before this.
  - `test_campaigns_api_db.py` — the real HTTP surface for PROJECT.md's
    "no send without a human clicking approve" hard rule: approve/
    reject transitions, and specifically that editing a message is a
    silent no-op once it's `approved` (subject/body provably unchanged
    after a POST that tries to change them).
  - `test_suppressions_api_db.py` — the real create/normalise/
    duplicate-reject/empty-reason-rejects-with-400-not-422 flow.
  - `test_review_api_db.py` — `/runs` and `/runs/{id}`'s real
    Postgres-backed history view and its qualified/name filters.
- `tests/conftest.py`'s `make_target_run` default `profile_yaml` is a
  complete, valid `TargetProfile` YAML (`legal_region: us`) — caught
  during this work: docs/20's `build_send_jobs()` now parses
  `profile_yaml` for real on every call (including from `/campaigns/
  {id}`'s preview), so a placeholder YAML missing required fields broke
  any route-level test that happened to render a campaign page, not
  just tests that cared about the profile's content.

## Verification

This entire tier *is* the verification layer — there's no separate
manual-verification step the way docs/08 through docs/20 needed, since
these tests run against a real Postgres by construction. What was
checked about the tier itself:

- `uv run pytest`: 316/316 passing (263 pre-existing + 53 new), whole
  suite in ~2.6s (one container, started once per session).
- `DOCKER_HOST=unix:///tmp/nonexistent-docker.sock uv run pytest`:
  263 passed, 53 skipped, 0 failed — confirms the tier degrades
  gracefully rather than breaking `pytest` for anyone without Docker
  running.
- The concurrency test was run repeatedly (not just once) during
  development to confirm it isn't a lucky pass; the underlying
  guarantee is a Postgres transaction-level lock, not a timing
  coincidence, so this wasn't expected to flake and didn't.

## Not done here

- **CI does not yet exercise this tier explicitly as its own step** —
  `.github/workflows/ci.yml` (docs/21) runs `uv run pytest` as one
  step, which now includes these ~50 tests automatically since GitHub's
  `ubuntu-latest` runners have Docker available. Not re-verified by an
  actual triggered GitHub Actions run for the same reason docs/21
  flags: that needs a real push this session didn't make.
- **Not every DB-touching module got equally deep coverage.**
  `db/persist.py`/`db/campaigns.py`/`db/orchestration.py`'s core paths
  are covered; some `api/` routes (mailboxes.py's live-token-health
  page, targets.py's file-based create/edit flows which don't need
  Postgres at all and already had non-DB tests) were left as they were.
  This closes the highest-risk gap (send-path correctness), not every
  gap.
