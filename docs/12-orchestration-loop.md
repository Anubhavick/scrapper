# 12 — The orchestration loop

The piece CLAUDE.md's "next concrete step" pointed at, and the last one
before this system can send a real campaign: a process that reads
`messages` where `status='approved'` and actually calls Gmail, in the
order `send/queue.py`'s `send_next()` already specifies (sleep the
90-600s gap first, then re-check fresh suppression/cap state, then
send), using `db/repository.py`'s advisory-lock `reserve_send_slot` so
two concurrent runs can't both observe "under cap."

## What was built

- **`src/leadgen/send/orchestrator.py`** — `run_orchestration_loop()`,
  the pure decision logic: groups a list of `SendJob`s (message id,
  mailbox id, email, daily cap — all opaque values, no ORM dependency)
  by `mailbox_id` and works through each mailbox's queue via
  `send_next()`. A `SendBlocked` with `reason == "cap"` stops that
  mailbox's remaining jobs (every later one would fail the identical
  check) but other mailboxes still get a turn; `reason == "suppressed"`
  only skips that one job and keeps going. Any other exception
  (a Gmail API error, a dead refresh token) is caught, reported via
  `on_error`, and recorded rather than aborting the whole run. Covered
  by `tests/test_orchestrator.py` — five pure tests, no DB, same
  approach as `tests/test_queue.py`.
- **A real ordering bug fixed in `send/queue.py` along the way.**
  `send_next()` used to build one `check_sendable(..., sent_today=
  sent_today_fn())` call — but Python evaluates keyword-argument
  expressions eagerly, so `sent_today_fn()` ran *before*
  `check_sendable`'s body ever checked suppression. That was harmless
  while `sent_today_fn` was a pure read, but this orchestrator needs it
  to carry a side effect (see below) — with the old ordering, a
  suppressed contact's message would get marked "sent" (silently
  burning a cap slot and corrupting the record) before suppression was
  even checked, even though the real Gmail call would never happen.
  Fixed by inlining the suppression check first and only calling
  `sent_today_fn()` once it's confirmed clear. `SendBlocked` also
  gained a `reason` attribute (`"suppressed"` | `"cap"`) so a caller can
  branch on why a send was blocked without parsing the message text —
  existing `match="suppressed"`-style tests still pass unchanged since
  the text itself didn't change.
- **`src/leadgen/db/orchestration.py`** — the DB-touching glue:
  `build_send_jobs()` reads every `approved` message whose mailbox is
  still active, ordered per-mailbox oldest-first; `run_approved_messages()`
  builds the callables `run_orchestration_loop()` needs and calls it.
  Not covered by the automated suite (same JSONB/UUID-vs-SQLite reason
  as `db/repository.py`/`db/campaigns.py`) — verified manually below.
- **`preview_approved_messages()`**, added after a real near-miss caught
  mid-build (see "The 'dry run' footgun" below): a genuinely read-only
  summary (per mailbox: queued, sent today, would-send-now, would-be-
  cap-blocked) — no lock, no reservation, no write of any kind.
- **`scripts/send_approved_messages.py`** — the CLI entry point, three
  modes: no flags (`preview_approved_messages()`, zero writes — the
  default), `--dry-run` (the full loop, fake Gmail call, **real**
  reservation writes — disposable test data only), `--live` (the real
  thing). `--live` requires typing back the literal phrase "send real
  email" on top of the flag itself — this is real, external, hard-to-
  reverse behavior and shouldn't be triggerable by a slipped default or
  a copy-pasted command.

## The "dry run" footgun (caught before it shipped as the default)

The first version of this built `dry_run=True` as the CLI's default,
reasoning that faking the Gmail call made it safe to run against
anything. That's wrong: `run_approved_messages(dry_run=True)` still runs
the *real* reservation — the advisory-locked transaction that flips a
message to `status='sent'` — because that transition is exactly what
`count_sent_today` needs to see to test cap-blocking meaningfully (see
below). Only the Gmail API call itself is faked. Pointed at a real
approved campaign, running the script with no flags under the original
design would have silently consumed every eligible message — flipped to
`sent`, `sent_at` set, no email ever sent, and no way back except manual
DB surgery — while displaying itself as a harmless "dry run."

Caught before any real use: `preview_approved_messages()` is now the
actual no-flag default (zero writes, confirmed below), and the old
`dry_run=True` behavior moved behind an explicit `--dry-run` flag whose
own docstring and CLI banner say plainly that it writes real reservation
state and is for disposable test data only. Worth remembering while
extending this module: **"fakes the network call" is not the same claim
as "makes no writes,"** and a name like `dry_run` inviting that
conflation is itself part of what let this ship as a default the first
time.

## The reservation shape (why a message is marked `sent` before the Gmail call, not after)

`db.repository.reserve_send_slot`'s own docstring is explicit: commit
the reservation *inside* the advisory-locked transaction, and do the
network send only after that transaction has committed — holding the
lock through a Gmail API call would block every other worker on that
mailbox for however long that call takes. `count_sent_today` counts
`status='sent'` rows, so the reservation that actually holds a cap slot
against a concurrent worker has to be that same transition (`status`
→`sent`, `sent_at` set), committed before the lock releases.

The accepted failure mode: if the Gmail call then fails, the message is
left `sent`/`sent_at` set with `gmail_message_id` still null. That's a
deliberately detectable anomaly — `select * from messages where
status='sent' and gmail_message_id is null` finds it — rather than a
silently wrong one. The alternative (hold the lock through the network
call) was rejected because it turns a single slow Gmail request into a
stall for every other concurrent sender on that mailbox; the other
alternative (reserve only after the Gmail call succeeds) was rejected
because it reopens the exact race `reserve_send_slot` exists to close —
two workers could both read "39/40 sent" before either commits.

## Verification

`uv run pytest`: 196/196 passing (191 prior + 5 new pure tests in
`tests/test_orchestrator.py`, plus 2 new assertions on `SendBlocked.reason`
in `tests/test_queue.py`).

Verified for real against the live Postgres from docs/08–11's runs. No
Gmail call was made in any of this — items 1–3 used `dry_run=True`
(fakes only the Gmail call, real reservation writes), item 4 used the
genuinely no-write `preview_approved_messages()`:

1. **Happy path.** Created a throwaway target_run/campaign/3 messages,
   all `approved`, assigned to the real `sales1` mailbox. Ran
   `run_approved_messages(dry_run=True)` with the 90-600s sleep patched
   out (so the run takes seconds, not tens of minutes — the sleep
   itself isn't what needed verifying). All 3 messages transitioned to
   `sent` with `sent_at` set and `gmail_message_id` still null (dry run
   correctly skips writing a real Gmail id). Confirmed via a direct
   query, not just the script's own report.
2. **Suppression blocks one message, not the mailbox's queue.** Same
   setup, 3 messages, one contact's email inserted into `suppressions`
   first. That message came back `blocked`/`reason=suppressed` and
   stayed `approved` (untouched); the other two in the same mailbox's
   queue still ran normally afterward.
3. **Cap block stops the rest of that mailbox's queue and leaves them
   `approved` for a later run.** Same setup, mailbox's `daily_cap`
   temporarily set to 1. First eligible message reserved the one slot
   and came back `sent`; the next came back `blocked`/`reason=cap`
   ("mailbox already sent 1/1 today") and was left `approved` — exactly
   what should happen the day the real cap is hit, since tomorrow's
   `count_sent_today` starts back at 0. Mailbox's `daily_cap` restored
   to 50 afterward, all throwaway rows deleted.

4. **`preview_approved_messages()` makes zero writes and reports correct
   numbers.** 3 throwaway `approved` messages, mailbox `daily_cap`
   temporarily set to 2. Read each message's `status` before and after
   calling `preview_approved_messages()` — identical (`approved` ×3,
   confirmed directly, not just by trusting the return value) — and the
   returned summary matched exactly: `queued=3, sent_today=0,
   daily_cap=2, would_send_now=2, would_be_cap_blocked=1`. Also ran the
   CLI with no flags against one real approved message and confirmed its
   human-readable output matches.

All runs used disposable businesses/contacts/campaigns/messages (fake
`@example.invalid` addresses, never real leads), deleted immediately
after each check — no test data or suppression rows were left behind.
(One leftover fixture from testing `preview_approved_messages()` before
this doc's own verification pass — `Preview Test Biz`/
`preview-test@example.invalid` — was found and cleaned up too.)

## What this deliberately doesn't do yet

- **No preflight token-health check.** `send/oauth.py` already has
  `validate_token_health()` for exactly this, but `run_approved_messages()`
  doesn't call it before starting a mailbox's queue — a dead refresh
  token currently surfaces as a per-message `"error"` outcome (via
  `on_error`) that repeats for every remaining message in that
  mailbox's queue, each wasting its own 90-600s sleep first. Worth
  adding before this runs unattended for long stretches; not needed for
  the first real, human-supervised run.
- **No re-check of `mailboxes.is_active` mid-run** — `build_send_jobs()`
  filters to active mailboxes once, up front, same as any other snapshot
  query. Fine for a single invocation of this script.
- **Not scheduled.** This is a script a human runs, on purpose, until
  step 7 (bounce/reply monitoring, still unbuilt) exists — no cron/RQ
  job invokes it automatically yet.
- **Never run with `--live`.** Every verification above used the
  no-write preview or `--dry-run` against throwaway data. This has not
  been pointed at a real campaign yet — that's a separate,
  explicitly-confirmed step.
