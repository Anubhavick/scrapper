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
health visibility (item 6) shipped, scoped down (docs/18)**, and **five
of item 8's smaller items shipped (docs/19)**: target-profile
edit-in-place, pagination on `/runs`/`/targets`/`/campaigns`,
bulk-approve, mailbox reassignment, and the orchestration loop's mid-run
`is_active` re-check. Item 3 (step 7: bounce/reply monitoring) is
blocked on a Google CASA review (deferred, per user decision on
2026-09-13 — see below). What's left: item 7 (Google Places, "only
worth it if Overpass coverage proves thin"), and item 8's one remaining
bullet (Send UI progress/ETA/cancel/history).

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
   thin for some vertical/city.
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
