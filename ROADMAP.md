# Roadmap

Living backlog for this repo. Update this file (don't just say it in
chat) whenever a backlog item ships or a new one gets found — this is
the place both this session and any other Claude session working on the
repo should check before picking the next thing to build.

For *why* any shipped decision looks the way it does, see the matching
`docs/NN-*.md` file. For the full current-state map, see
[MANUAL.md](MANUAL.md) and [CLAUDE.md](CLAUDE.md).

## Status: the first real send has happened. The system works end to end.

```
DISCOVER → CRAWL → QUALIFY → COMPOSE → REVIEW → SEND → MONITOR
   ✅        ✅        ✅        ✅        ✅      ✅       ❌
                                                 real send
                                                confirmed
```

2026-09-13: a real campaign (`dentists-austin-tx-round-1`, 6 real
qualified Austin dentist leads) was built from the existing
`dentists-austin-tx` run, one message (Austin Cosmetic Dentistry) was
reviewed and approved by the user in the actual browser UI, and sent for
real via the campaigns page's **Start sending** button + an `rq worker`
— a real Gmail message id was recorded
(`messages.gmail_message_id`), confirming the whole discover → crawl →
qualify → compose → approve → send chain works with real data, not just
disposable fixtures. The other 5 messages in that campaign are still
`queued` (never approved), untouched.

`scripts/dev.sh up` (docs/13/HOWTO.md) now starts everything (docker +
migrations + web UI + worker) in one command instead of four separate
manual ones.

## Immediate next step

Nothing is blocking on a decision right now. Pick up wherever the next
message says, or the next item in the backlog below —
**suppression-list population (item 1) shipped (docs/14)**,
**`last_content_year`'s regex bug (item 2) shipped (docs/15)**,
**the orchestration loop's preflight token-health check (item 4) shipped
(docs/16)**, **web UI auth (item 5) shipped (docs/17)**, **mailbox
health visibility (item 6) shipped, scoped down (docs/18)**, **five
of item 8's smaller items shipped (docs/19)**: target-profile
edit-in-place, pagination on `/runs`/`/targets`/`/campaigns`,
bulk-approve, mailbox reassignment, and the orchestration loop's mid-run
`is_active` re-check, and **the GDPR/DPDP legal-posture gap (item 9)
shipped (docs/20)**: `legal_region` is now a required `TargetProfile`
field and the send stage hard-refuses any `eu_uk` profile, regardless of
`requires_opt_in`, since no opt-in mechanism exists to make a send
actually legal. Item 3 (step 7: bounce/reply monitoring) is blocked on a
Google CASA review (deferred, per user decision on 2026-09-13 — see
below). **CI (item 11) shipped (docs/21)**: a GitHub Actions workflow
now runs `pytest` + `alembic check` against a real Postgres service
container on every push/PR — see docs/21 for the one thing about it
that's still unverified (an actual triggered run). **The Postgres-backed
test tier (item 12) shipped (docs/22)**: ~50 new tests against a real
ephemeral testcontainers Postgres across `db/repository.py`,
`db/persist.py`, `db/campaigns.py`, `db/orchestration.py`, and three
DB-backed `api/` routers — including a real concurrency test proving
the send-cap advisory lock actually prevents a double-send. What's left:
item 7 (Google Places, "only worth it if Overpass coverage proves
thin"), item 8's one remaining bullet (Send UI progress/ETA/cancel/
history), and item 10 (the 200-rows checkpoint at real volume — skipped
for now per user decision on 2026-09-13, revisit once there's a concrete
new vertical/city to scan).

Still worth doing at some point, not urgent: **click through the Send UI
with an actual mouse in a browser** — its own verification (docs/13)
used direct HTTP requests because a real Playwright session was locked
by another concurrent session at the time, not literal clicks the way
docs/09's and docs/11's `DetachedInstanceError` bugs were caught.

## Backlog, roughly in priority order

1. ~~**Suppression-list population.**~~ **Done (docs/14).** `/suppressions`
   -- create-only, `value` normalised the same way the real send path
   checks it, duplicate/malformed input rejected with a friendly error
   instead of a 500. Still no *automatic* writer (that's step 7, below)
   -- this is the manual lever for a reply that arrives before then.
2. ~~**`last_content_year`'s regex bug.**~~ **Done (docs/15).** Years
   immediately after a copyright marker (`©`/`(c)`/"copyright") are now
   excluded before taking the max — a copyright-only page now reports
   `None` instead of a fabricated "fresh" signal. Verified against real
   re-crawled Austin dentist sites: 3 of 4 previously-`2026` sites now
   show a real year or `None`; the 4th genuinely has an unrelated 2026
   date (JSON-LD review timestamps), noted as a separate, narrower gap
   in docs/15, not fixed as part of this.
3. **Step 7: bounce/reply monitoring.** Needs a Google CASA security
   review before Google allows the restricted `gmail.readonly`/
   `gmail.modify` scopes in production (this system currently only holds
   `gmail.send`). Once granted: poll each mailbox's inbox for bounces
   (delivery-failure notifications) and replies to sent messages
   (correlate via `messages.gmail_thread_id`, already stored for this),
   write a `suppressions` row on either, and mark the `messages` row
   `bounced`/`replied`. This is the piece that makes the suppression list
   actually maintain itself instead of relying on manual entries.
   **Explicitly deferred, 2026-09-13** — a real CASA review is a paid
   (roughly $500-$4,500), multi-week third-party assessment meant for
   production apps serving external users; for this single-team internal
   tool, staying in the OAuth consent screen's Testing mode and adding
   each sending mailbox as a test user very likely avoids needing CASA
   at all (restricted scopes work fine for listed test users), at the
   cost of refresh tokens expiring every 7 days until/unless the app is
   ever verified. Worth re-evaluating that Testing-mode path before
   assuming a full CASA review is required when this gets picked up.
4. ~~**No preflight token-health check in the orchestration loop.**~~
   **Done (docs/16).** `run_orchestration_loop()` now calls
   `validate_token_health()` once per mailbox before that mailbox's
   queue starts; an unhealthy token blocks every job for that mailbox
   immediately (no sleep, no reservation) instead of failing once per
   remaining message. Verified against real Postgres + a real Google
   OAuth rejection (`invalid_grant`) with disposable test data.
5. ~~**No auth on the web UI at all.**~~ **Done (docs/17).** HTTP Basic
   Auth (`api/auth.py`'s `require_auth`), wired at the `FastAPI(
   dependencies=...)` level so every route on the shared app is
   covered — one shared team credential (`WEB_UI_USERNAME`/
   `WEB_UI_PASSWORD` in `.env`), not per-user accounts (see docs/17 for
   why that's the right scope here). Verified against the real running
   server: no/wrong credentials → 401 on every page checked
   (`/targets`, `/campaigns`, `/suppressions`, `/runs`), correct
   credentials → 200 with real content. `scripts/dev.sh up` now refuses
   to start if the credentials aren't set in `.env`, instead of silently
   coming up broken.
6. ~~**Mailbox health/reputation has no visibility beyond token
   validity.**~~ **Done, scoped down (docs/18).** New `/mailboxes` page:
   live token-health check (real call to Google on every page load),
   today's sent count vs. daily cap, and the last real send error (if
   any) with when it happened — `Mailbox.last_send_error`/
   `last_send_error_at`, cleared on the next real success. **A real
   spam/reputation signal itself is still not available** — that needs
   the same restricted-scope/CASA path step 7 is deferred on, or a
   verified-domain-only tool (Postmaster Tools) that doesn't apply to
   personal Gmail accounts; this only surfaces what's actually knowable
   today (token validity, cap usage, real send failures). Verified
   against the real running server, including a disposable mailbox with
   a genuinely dead token; the `on_sent`/`on_error` wiring that writes
   these columns was **not** exercised via a real `--live` send (would
   require an actual send — not triggered by this session on its own
   initiative) — see docs/18 for what was and wasn't proven.
7. **Google Places as a fallback discover source** — designed for in the
   schema (`businesses.source_raw_expires_at`, the 30-day Places ToS
   note), never implemented. Only worth it if Overpass coverage proves
   thin for some vertical/city. **That condition has now been met** —
   see item 13: the aviation-training vertical is effectively absent
   from OSM, so this or a `csv` source is the only way that vertical
   ever produces leads.
8. Smaller, lower-urgency items:
   - ~~Target profiles are create-only — no edit-in-place, only
     Duplicate.~~ **Done (docs/19).** `GET`/`POST /targets/{name}/edit`,
     same validation as create. Saving rewrites the whole file (no
     comment-preserving round-trip), a warning says so before every save.
   - ~~No pagination on `/runs`, `/targets`, or `/campaigns` as data
     grows.~~ **Done (docs/19).** Shared `pagination_bar()` in
     `api/nav.py`, 25/page, verified against real Postgres with 30+
     disposable rows on `/runs` and `/campaigns`.
   - ~~No bulk-approve for a large campaign.~~ **Done (docs/19).**
     "Approve all queued (N)" once a campaign has 2+ queued messages,
     gated on typing back the exact count (`"approve all N"`) — doesn't
     relax the hard rule, since every queued message's text is already
     rendered on the same page before this control ever appears.
   - ~~`mailbox_id` is assigned to a message at generation time, not send
     time — a mailbox deactivated after approval but before send is
     currently just skipped, not reassigned.~~ **Done (docs/19).**
     `build_send_jobs(reassign=True)` (only the real-run path, never
     `preview_approved_messages()`) reassigns to the least-loaded active
     mailbox in the same campaign's pool and persists it immediately.
   - ~~No re-check of `mailboxes.is_active` mid-run in the orchestration
     loop — a single invocation snapshots active mailboxes once, up
     front.~~ **Done (docs/19).** `run_orchestration_loop()`'s new
     `mailbox_active_fn`, checked once per mailbox right before its
     queue starts (same point as docs/16's token-health check, and
     checked first) — a fresh column-select, not the stale
     already-loaded ORM object, confirmed directly to see a concurrent
     external deactivation mid-transaction.
   - The Send UI (docs/13) has no progress bar, ETA, or cancel button —
     just "reload the page and look at the messages table" — and no
     history of past send jobs beyond what `messages.status`/`sent_at`
     already record. Not part of docs/19's batch.
9. ~~**GDPR/DPDP legal-posture enforcement was spec-only.**~~ **Done
   (docs/20).** `TargetProfile.legal_region` (required, no default) +
   `requires_opt_in`, validated at config-load time; the send stage
   (`db/orchestration.py`'s `build_send_jobs()`) hard-refuses every
   `eu_uk` message regardless of `requires_opt_in`, since no opt-in
   mechanism exists anywhere in this system to make that flag actually
   true. Verified against real Postgres: an eu_uk campaign produces zero
   send jobs, a us campaign is unaffected. DPDP's softer "prefer generic
   over named contacts" preference is still unenforced — noted in
   docs/20, not fixed, to keep this change scoped to the one hard rule
   that was actually missing.
10. **The 200-rows-by-hand checkpoint only ever happened at 31 rows, for
    one vertical (dentists, Austin TX — docs/07).** MANUAL.md already
    flags this itself as worth redoing "at real volume before trusting
    qualification broadly across other business types/cities" — it's
    real advice sitting in a docs file, not a tracked backlog item, and
    now that real campaigns actually go out the risk of a systematic
    qualification bug in an untested vertical is real, not theoretical.
    Needs a human to actually read output by hand (this is a judgment
    call about lead quality, not something to automate); scoping which
    business type/city to try next is a decision for whoever picks this
    up, not made here.
11. ~~**No CI.**~~ **Done (docs/21).** `.github/workflows/ci.yml`: a
    real `postgres:16` service container, `uv sync`, `uv run pytest`,
    then `uv run alembic upgrade head` + `uv run alembic check` — on
    every push to `main` and every pull request. The constituent
    commands were verified against a real local dockerized Postgres;
    the workflow itself has not yet been exercised by an actual GitHub
    Actions run (needs a real push to trigger) — see docs/21 for what
    that leaves unverified.
12. ~~**The DB-touching half of the codebase has zero automated
    coverage, by construction.**~~ **Done (docs/22).**
    `testcontainers[postgres]` (dev dependency) + `tests/conftest.py`:
    an ephemeral Postgres 16 container per test session, migrated via
    real Alembic history, never the dev `DATABASE_URL` (which holds
    real send data). ~50 new tests across `test_repository_db.py`,
    `test_persist_db.py`, `test_campaigns_db.py`,
    `test_orchestration_db.py` (including a real two-thread concurrency
    test proving `_reserve_fn`'s advisory lock actually prevents a
    double-send under two racing workers — the property this whole
    system's cap logic depends on, never exercised automatically
    before), `test_campaigns_api_db.py`, `test_suppressions_api_db.py`,
    and `test_review_api_db.py`. Degrades gracefully: confirmed `uv run
    pytest` still passes (263/263, this tier's ~50 tests skipped) with
    Docker unreachable. Runs automatically as part of item 11's CI job
    (GitHub's `ubuntu-latest` runners have Docker available) — not yet
    confirmed by an actual triggered run, same caveat as docs/21.

13. **A `csv` discover source — the only route into verticals OSM
    doesn't map.** `config/models.py`'s `KNOWN_SOURCES` and
    `SourceConfig.primary` have accepted `"csv"` since step 2, but
    `discover/` only implements Overpass, so a profile declaring it
    would fail at runtime against a value the schema says is legal.
    This stopped being theoretical on 2026-09-18, when the
    aviation-training vertical was set up (`flight_school` in
    `config/business_types.yaml`, `targets/aviation-academies-india.yaml`,
    `targets/flight-schools-usa.yaml`): measured against real Overpass,
    `amenity=flight_school` + `club=aviation` return **369 objects in
    the entire planet file** (241 with a `website` tag), of which
    **111 are in the USA (79 surviving `must_have_website`) and 0 are
    in India**. The India profile discovers literally nothing and
    cannot be fixed by changing its location, radius, or tags — OSM has
    no data to find. Since the goal there is a ~500-lead Indian
    academy list, a `csv` source (name + website URL per row, then the
    existing crawl → email-extract → qualify → compose chain unchanged,
    so PROJECT.md's no-guessed-emails rule still holds — addresses
    still come only off the business's own site) is the smallest
    change that unblocks it, and is reusable for every future vertical
    OSM ignores. Worth doing before item 7: no API key, no Places
    30-day-retention purge job, no per-call cost.

## Explicitly deferred, not forgotten

- **LLM-assisted message drafting.** Considered when message editing was
  built (docs/11). The constraint if it's ever built: an LLM may only
  *rephrase* the already-signal-grounded line, never invent new claims
  about a business — `compose/render.py`'s whole safety property is that
  nothing reaches a lead's inbox that isn't tied to a real crawled
  signal, and free-drafting would risk asserting something false about a
  business, a real reputational/legal problem for unsolicited outreach.
- **Scheduling the orchestration loop.** A human still has to trigger
  every send, on purpose, until step 7 exists — the UI's "Start sending"
  button (docs/13) runs it via `rq worker` instead of blocking on the
  page, but that's still a human click, not a schedule. No cron or
  automatic trigger exists.
