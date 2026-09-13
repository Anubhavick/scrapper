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
**suppression-list population (item 1) shipped (docs/14)**, so item 2
(`last_content_year`'s regex bug) is next up.

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
2. **`last_content_year`'s regex bug** (flagged since docs/07, still
   unfixed). It regexes a whole page for the largest 4-digit year and
   reliably mistakes a copyright-footer year for real content freshness.
   Directly affects lead quality — a real business with a stale site can
   look "fresh" purely because of a footer.
3. **Step 7: bounce/reply monitoring.** Needs a Google CASA security
   review before Google allows the restricted `gmail.readonly`/
   `gmail.modify` scopes in production (this system currently only holds
   `gmail.send`). Once granted: poll each mailbox's inbox for bounces
   (delivery-failure notifications) and replies to sent messages
   (correlate via `messages.gmail_thread_id`, already stored for this),
   write a `suppressions` row on either, and mark the `messages` row
   `bounced`/`replied`. This is the piece that makes the suppression list
   actually maintain itself instead of relying on manual entries.
4. **No preflight token-health check in the orchestration loop**
   (docs/12). `send/oauth.py` already has `validate_token_health()`; the
   loop doesn't call it before starting a mailbox's queue, so a dead
   refresh token currently surfaces as a per-message error repeated for
   every remaining message in that queue, each wasting its own 90-600s
   sleep first. Not needed for a small, human-supervised first run; worth
   adding before this runs unattended for long stretches.
5. **No auth on the web UI at all.** Fine for one person on localhost; a
   real problem the moment it's reachable by anyone else — no login
   currently stands between a visitor and creating a campaign or
   approving a send.
6. **Mailbox health/reputation has no visibility beyond token validity.**
   No way to tell from inside the app if Gmail has started flagging a
   sending account as spam. Matters more as volume goes up.
7. **Google Places as a fallback discover source** — designed for in the
   schema (`businesses.source_raw_expires_at`, the 30-day Places ToS
   note), never implemented. Only worth it if Overpass coverage proves
   thin for some vertical/city.
8. Smaller, lower-urgency items:
   - Target profiles are create-only — no edit-in-place, only Duplicate.
   - No pagination on `/runs`, `/targets`, or `/campaigns` as data grows.
   - No bulk-approve for a large campaign (every message is approved
     one at a time).
   - `mailbox_id` is assigned to a message at generation time, not send
     time (docs/11) — a mailbox deactivated after approval but before
     send is currently just skipped, not reassigned.
   - No re-check of `mailboxes.is_active` mid-run in the orchestration
     loop (docs/12) — a single invocation snapshots active mailboxes
     once, up front.
   - The Send UI (docs/13) has no progress bar, ETA, or cancel button —
     just "reload the page and look at the messages table" — and no
     history of past send jobs beyond what `messages.status`/`sent_at`
     already record.

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
