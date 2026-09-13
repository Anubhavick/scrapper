# 11 — Campaigns and message approval

Third of the three UI pages (history/review → scan-builder → campaigns,
per the order the user chose) and the piece CLAUDE.md's "next concrete
step" pointed at: `campaigns`/`messages` existed in the schema, migrated,
untouched, since step 1. This is what finally writes to them, and gives
the hard rule "no send without a human clicking approve on the final
rendered text" something to actually click.

## What was built

- `src/leadgen/db/campaigns.py`:
  - `create_campaign(session, target_run_id, offer_id, sender_pool, name=None)`
    — creates the `campaigns` row and its `campaign_mailboxes` join rows.
    `sender_pool` names must already be real, authorized `mailboxes` rows
    (`scripts/authorize_mailbox.py`, HOWTO.md) — refuses to invent a
    sending identity with no real OAuth credentials behind it.
  - `generate_campaign_messages(session, campaign, offer, templates_root)`
    — one rendered `messages` row (status `queued`) per qualified business
    in the campaign's `target_run`, via `compose/render.py` (already
    built, untouched). Idempotent: re-running against the same campaign
    skips businesses that already have a message in it. Returns
    `(created, skipped)` rather than raising on a per-lead problem — one
    bad lead (no contact, or a signal set that no longer justifies the
    offer) is skipped and reported, not allowed to abort the whole batch.
  - `_select_best_contact()`: **one message per business, not one per
    contact.** A business with nine published addresses (BLVD Dentistry,
    real example from docs/09's data) gets one outreach message, to its
    best contact — a named address (`is_generic=False`) preferred over a
    generic one (`info@`, `contact@`, ...) when both exist. This is a
    real scope decision: `messages` is unique on `(campaign_id,
    contact_id)`, which *permits* multiple contacts per business, but
    spamming every published address at one business is obviously not
    the intent.
- `src/leadgen/api/campaigns.py`, mounted onto the same `app`:
  - `GET /campaigns` — every campaign, target/offer/sender-pool summary,
    a status-count breakdown (queued/approved/sent/rejected/...).
  - `GET /campaigns/new` + `POST /campaigns` — pick a completed
    `target_run` with at least one qualified business, an offer (from
    `config/offers/`), and a sender pool (checkboxes of real, active
    `mailboxes` rows) — creates the campaign and generates its messages
    in one step.
  - `GET /campaigns/{id}` — the approval screen: one row per message,
    full subject + body behind a `<details>` disclosure (no JS, same
    convention as the rest of this UI), Approve/Reject buttons.
    Approving requires typing a name into "Approving as" first — a
    genuinely blank `approved_by` would make the human-approval hard
    rule unauditable, not just informally satisfied. The name carries
    through the URL (`?approver=...`) so it doesn't need retyping per
    row while working through a list.
  - `nav.py` gained a fourth link ("Campaigns").
  - `POST /campaigns/{id}/messages/{message_id}/edit` (added after the
    user pointed out real campaigns need per-business tweaks, not just
    accept/reject on the template output verbatim) — while a message is
    still `queued`, its subject/body render as editable fields instead of
    a read-only `<details>` block, with a "Save changes" button. Refuses
    to edit anything not `queued`: once approved, `messages.subject`/
    `body` is the record of exactly what text a human signed off on
    (CLAUDE.md's schema-decisions note on why `messages` stores rendered
    text, not a template reference) — allowing an edit after approval
    would make that record describe a message that was never actually
    approved. Verified this refusal for real: a raw POST to an already-
    approved message's edit endpoint left `status`/`subject` completely
    unchanged.

## Two real bugs a browser session caught (same class as docs/09's)

1. **`DetachedInstanceError` on `/campaigns/new`.** `_render_new_form()`
   was called *after* its `with session_scope() as session:` block had
   closed, but it reads `run.id`/`run.target_name`/`m.name` from ORM
   objects fetched inside that block — SQLAlchemy expires those
   attributes on commit, so accessing them post-close tried to reload
   from a session that no longer existed. Same root cause as docs/09's
   `/runs/{id}` bug, same fix: move the render call *inside* the `with`
   block (three call sites: the GET form, and both error-paths in
   `POST /campaigns`) rather than extracting plain values first this
   time, since the form needs the full list of runs/mailboxes, not one
   scalar. Worth calling out as a pattern now: **any route that renders
   HTML from ORM objects must do the rendering before the session
   closes, full stop** — this is the second time this exact mistake
   shipped and was only caught by actually clicking through in a
   browser, not by anything in the automated suite.
2. Not a new bug, but confirmed not reintroduced: the empty-string
   `Form(...)` issue from docs/09 doesn't recur here because every
   `Form(...)` field in this module (`approver`) has a non-empty default
   (`Form("")`), not a required one with no default.

## What this deliberately doesn't do

- **Doesn't send anything.** No route here calls `send/queue.py` or
  `send/gmail.py`. That's the actual orchestration loop — a process that
  reads `approved` messages and, respecting the 90–600s gap and
  re-checking suppression/caps against *fresh* state right before each
  send (`send/queue.py`'s `send_next()` already implements this
  ordering), calls Gmail. Deliberately not built in this step: a real
  send touches real external mailboxes and real business owners' inboxes
  — an irreversible, externally-visible action — and building +
  personally triggering it are being kept as two separate, separately
  authorized steps.
- **No re-render of an already-*approved* message, and no LLM-assisted
  drafting.** Editing is deliberately scoped to plain manual text editing
  of a `queued` message only (see above) — not an automated rewrite step.
  The user raised the idea of using an LLM to personalise messages
  per-business; the design decision (not yet built) is to constrain any
  such assistance to *rephrasing the already-signal-grounded line*, never
  to invent new claims about a business, since `compose/render.py`'s
  entire safety property is that nothing reaches a lead's inbox that
  isn't tied to a real, crawled signal — an LLM free to draft the whole
  email would risk asserting something false about a business, which is
  a real reputational/legal problem for unsolicited outreach, not just a
  quality one.
- **No real auth behind "Approving as."** It's a free-text name, not an
  authenticated identity — good enough for a single small team using this
  tool locally, not something to trust as an audit control beyond that.
- **`mailbox_id` is assigned at message-generation time** (round-robin
  across the campaign's sender pool), not at send time. This is a
  simplification worth revisiting once the orchestration loop exists:
  `send/queue.py`'s design assumes suppression/cap state is re-checked
  immediately before sending, which is compatible with a pre-assigned
  mailbox, but a mailbox that goes inactive between approval and send
  isn't currently reassigned.

## Verification

`uv run pytest`: 191/191 passing (188 prior + 3 new pure tests for
`_select_best_contact` in `tests/test_campaigns_logic.py` — the
DB-touching functions in `db/campaigns.py` and every route in
`api/campaigns.py` aren't covered by the automated suite, same reasoning
as `db/persist.py`/docs/09's `/runs` routes: `Campaign`/`Message` use the
same Postgres-only JSONB/UUID columns SQLite can't stand in for).

Verified for real against the live Postgres from docs/08/docs/09's runs:
created a real campaign (`dentists-austin-tx`'s 6-qualified-lead run,
`appointment-automation` offer, `sales1` sender pool — a real,
`scripts/authorize_mailbox.py`-authorized mailbox) through the actual
browser UI. All 6 messages generated with correctly grounded subject/body
text (e.g. "I didn't see an online booking option on your site" for a
business actually missing one), each addressed to a real business's best
contact. Confirmed the approve guard actually blocks (400) an approve
attempt with no approver name set, confirmed a real approve sets
`approved_by`/`approved_at` and displays them, confirmed reject sets
`status='rejected'` and removes the action buttons, confirmed the
campaign index's status-breakdown counts (`approved: 1, queued: 4,
rejected: 1`) matched the approve/reject clicks exactly.

Edit flow verified separately, also for real: created a second test
campaign, edited a `queued` message's subject and body through the
browser form, confirmed "Save changes" persisted the new text (visible
on page reload, not just in the form), then approved that same message
and confirmed the approved (now read-only) view showed the *edited* text,
not the original template output. Confirmed the lock: a raw `curl POST`
to that now-approved message's `/edit` endpoint returned a 303 (the route
always redirects, edit or not) but left `status`/`subject` in Postgres
completely unchanged, verified directly via `psql`/a session query, not
just by trusting the redirect. Both test campaigns and their messages
deleted from Postgres after verification, not left behind as leftover
data.
