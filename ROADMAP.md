# Roadmap

Living backlog for this repo. Update this file (don't just say it in
chat) whenever a backlog item ships or a new one gets found — this is
the place both this session and any other Claude session working on the
repo should check before picking the next thing to build.

For *why* any shipped decision looks the way it does, see the matching
`docs/NN-*.md` file. For the full current-state map, see
[MANUAL.md](MANUAL.md) and [CLAUDE.md](CLAUDE.md).

## Status: everything through the send loop is built. Nothing has sent for real yet.

```
DISCOVER → CRAWL → QUALIFY → COMPOSE → REVIEW → SEND → MONITOR
   ✅        ✅        ✅        ✅        ✅      ⚠️       ❌
                                                 built,
                                              never run
                                               --live
```

## Immediate next step

**Run `scripts/send_approved_messages.py --live` against a real approved
campaign.** Everything up to this is built and dry-run-verified against
real Postgres (docs/12). This is real, external, hard-to-reverse
behavior — it emails real business owners — so it needs an explicit
human decision each time, not something any Claude session should do on
its own initiative. The script itself requires `--live` plus typing back
a confirmation phrase, on top of that.

## Backlog, roughly in priority order

1. **Suppression-list population.** The hard rule checks `suppressions`
   before every send, but nothing writes to it yet. No bounce/reply
   monitoring (item 3 below) and no manual "add to suppression list" UI
   either — right now the only lever is a raw DB insert. Worth a small
   manual-add UI even before step 7 exists, since replies asking to stop
   contact can happen before any monitoring is built.
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

## Explicitly deferred, not forgotten

- **LLM-assisted message drafting.** Considered when message editing was
  built (docs/11). The constraint if it's ever built: an LLM may only
  *rephrase* the already-signal-grounded line, never invent new claims
  about a business — `compose/render.py`'s whole safety property is that
  nothing reaches a lead's inbox that isn't tied to a real crawled
  signal, and free-drafting would risk asserting something false about a
  business, a real reputational/legal problem for unsolicited outreach.
- **Scheduling the orchestration loop.** It's a script a human runs, on
  purpose, until step 7 exists — no cron/RQ job invokes it automatically.
