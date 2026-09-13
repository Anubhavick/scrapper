# 13 — Send from the campaigns UI

Following on from docs/12: the orchestration loop existed but the only
way to run it was a terminal command. The user wanted a "Send" button on
the campaigns page instead, without reintroducing the browser-timeout
problem that already kept the scan-builder page (docs/10) from
triggering a scan synchronously -- a real send can take hours (90-600s
between each of up to 50 messages per mailbox), far longer than any HTTP
request should stay open.

## What was built

- **`src/leadgen/queue.py`** -- `get_queue()`, an RQ `Queue` bound to
  `REDIS_URL`, mirroring `db/session.py`'s pattern for `DATABASE_URL`.
  `rq`/`redis` were already project dependencies and Redis was already
  running via `docker-compose.yml` -- nothing in the codebase used them
  until now.
- **`src/leadgen/jobs.py`** -- `send_campaign_messages_job(campaign_id,
  *, live)`, the RQ job body. Takes only plain, picklable arguments (a
  string id, a bool) rather than a `Session`/`httpx.Client`, since those
  can't safely cross the process boundary to the `rq worker` process
  that actually executes this -- it opens its own session/client
  internally, the same way `scripts/send_approved_messages.py` does.
  Also holds `CONFIRMATION_PHRASE = "send real email"`, now the single
  source of truth the CLI script and the new UI form both import from
  (previously only the CLI script defined it).
- **`db/orchestration.py` gained an optional `campaign_id` filter** on
  `build_send_jobs()`, `preview_approved_messages()`, and
  `run_approved_messages()` -- narrows to one campaign's approved
  messages instead of every approved message system-wide. The mailbox's
  `sent_today`/`daily_cap` numbers still reflect that mailbox's real
  global count either way; only which messages count as "queued" for
  this preview/run is scoped.
- **`api/campaigns.py`'s `/campaigns/{id}` gained a "Send" section**:
  - **Read-only preview**, always shown when nothing is currently
    running: the same per-mailbox breakdown
    `scripts/send_approved_messages.py`'s no-flag mode prints (queued,
    sent today, would-send-now, would-be-cap-blocked), via
    `preview_approved_messages(session, campaign_id=...)`.
  - **A confirm-and-send form**: type `send real email`, click **Start
    sending** → `POST /campaigns/{id}/send` validates the phrase,
    refuses if a send is already running for this campaign, then
    enqueues `send_campaign_messages_job(campaign_id, live=True)` with a
    deterministic job id (`send-campaign-<campaign_id>`) and redirects
    back to the same page.
  - **While a send is running**, the preview/form are replaced with "A
    send is currently queued/started for this campaign," and the page
    adds `<meta http-equiv="refresh" content="15">` so it keeps
    reloading. No JS anywhere in this UI (existing convention) and no
    custom progress tracking either -- the messages table lower on the
    same page already shows each message's live `status`, which is
    exactly what's changing as the job runs. Job status itself
    (queued/started/finished/failed) comes from asking Redis via
    `queue.fetch_job(job_id).get_status()`, wrapped so a Redis hiccup on
    a GET request degrades to "no active job" instead of a 500.
  - **The UI never exposes `--dry-run`.** Only `live=True` is reachable
    from this form, on purpose -- `dry_run=True` still makes real
    reservation writes (see docs/12's "dry run footgun"), and a
    campaign approved through this UI is by definition real data, never
    the disposable fixtures `--dry-run` is for.

## A second real race caught while wiring this up

`db/repository.py`'s advisory lock already prevented two concurrent runs
from both observing "under cap." It did **not** prevent two overlapping
runs from both processing the *same already-fetched* approved message --
each run's `SendJob` list is built once, at the start of that run, from
whatever was `approved` at that moment. If run A reserves and sends
message M, then run B (which had already fetched M into its own job list
before A committed) reaches M later in its own loop, nothing stopped it
from reserving M *again* and calling Gmail a second time for the exact
same message -- a real duplicate email, not just a duplicate DB write.

This was always theoretically possible with two terminal invocations,
but the UI makes it a lot easier to trigger by accident (two browser
tabs, an impatient second click before the page redirects). Closed two
ways:

1. **The RQ job id is deterministic** (`send-campaign-<campaign_id>`),
   and `POST /campaigns/{id}/send` checks for an already-active job
   under that id before enqueueing -- a second click while one is
   running is refused, not queued.
2. **`db/orchestration.py`'s `_reserve_fn` now re-checks `message.status
   == "approved"` under the same advisory lock**, before reserving.
   Belt-and-suspenders in case #1 is ever bypassed (a second CLI
   `--live` run started manually alongside a UI-triggered send, say):
   if the status has already moved on, this returns `daily_cap` as the
   sent-today count, which makes `send_next()`'s own `can_send` check
   fail and raise `SendBlocked(reason="cap")` -- an intentionally
   conservative mislabel (it isn't really a cap issue) that produces the
   one behavior that actually matters: `send_fn` never runs twice for
   the same message.

## A third bug: RQ's own timeout signal was getting swallowed

Caught during this step's own verification, not reasoned out in advance.
`send/orchestrator.py`'s `run_orchestration_loop()` (docs/12) wraps each
`send_next()` call in a broad `except Exception`, deliberately, so one
bad message doesn't kill the whole run. The first verification run used
RQ's *default* `job_timeout` (180 seconds) -- too short for even one
message's 90-600s gap -- and when it fired mid-sleep, the resulting
`rq.timeouts.JobTimeoutException` (which subclasses `Exception`, not
`BaseException`) was caught by that same broad handler, logged as if it
were an ordinary per-message error, and the loop just... kept going. The
job then reported **"Successfully completed"** even though RQ's own
cancellation signal had fired -- exactly the scenario a `job_timeout` is
supposed to prevent (a runaway job), silently defeated by the safety net
built for a different purpose.

Fixed with a soft import in `send/orchestrator.py` (`try: from
rq.timeouts import JobTimeoutException except ImportError:
JobTimeoutException = ()` -- keeps this module's "no hard external
dependency" property even though `rq` happens to be installed in this
project) and one added line: `except JobTimeoutException: raise` ahead
of the broad `except Exception`. Covered by a new pure test
(`test_job_timeout_exception_propagates_instead_of_being_treated_as_a_send_error`)
using the real `rq.timeouts.JobTimeoutException` class, not a stand-in.

Practical fallout: `api/campaigns.py`'s enqueue call sets
`job_timeout=12*60*60` (12 hours) precisely so a real campaign's queue
has room to finish; the CLI script's own direct calls aren't run under
RQ at all, so this only ever mattered for the UI path -- but it would
have mattered a lot, silently, the first time a real queue ran long
enough to hit whatever timeout was configured.

## The macOS `rq worker` fork-safety crash (environment quirk, not a bug here)

Running `uv run rq worker leadgen -u redis://...` on macOS crashed the
first job with `Work-horse terminated unexpectedly ... signal 6` --
`objc[...]: ... may have been in progress in another thread when fork()
was called ... Crashing instead.` RQ's default worker forks a child
process per job; some library already touched by the parent process
initializes Objective-C runtime state that isn't fork-safe, and macOS
aborts the child rather than risk corruption. Not caused by anything in
this codebase -- a known class of issue with fork-based process pools on
macOS. Workaround, set before starting the worker:

```bash
OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES uv run rq worker leadgen -u redis://localhost:6379/0
```

Documented in HOWTO.md's worker-startup step so it isn't rediscovered
the hard way later.

## Verification

`uv run pytest`: 197/197 passing (196 prior + 1 new pure test for the
`JobTimeoutException` fix above).

Verified for real against live Postgres + Redis, disposable data,
`live=False`/a fake enqueued job throughout (nothing here ever called
Gmail or actually let a real send through):

**RQ plumbing, end to end, twice** (the first run is what surfaced the
`JobTimeoutException` bug above; the second re-ran the same scenario
after the fix, with a sane `job_timeout=3600`):
- Created a throwaway target_run/campaign/business/contact/message
  (`approved`, real `sales1` mailbox).
- Enqueued `send_campaign_messages_job(campaign_id=..., live=False)`
  directly (bypassing the web form, which only ever passes `live=True`
  -- the point here was verifying the RQ plumbing and
  `db/orchestration.py`'s `campaign_id` filter, not the form).
- Ran `uv run rq worker leadgen -u redis://localhost:6379/0 --burst`
  (`--burst`: process what's queued, then exit -- right for a one-off
  check). Hit the macOS fork crash above on the first attempt; re-ran
  with `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES` set, which completed
  cleanly both times after that.
- Confirmed the job actually ran the *real* loop, including the real
  `wait_before_next_send()` sleep (not patched out) -- the second run
  took 26 minutes wall-clock (background-process scheduling in this
  sandboxed dev environment, not our own sleep logic, which is bounded
  to 600s by `MAX_DELAY_SECONDS`) and still completed correctly with the
  1-hour `job_timeout`, confirming the fix didn't just move the goalpost.
- Confirmed via a direct query afterward, both times: the message
  transitioned to `sent` with `sent_at` set and `gmail_message_id` still
  null (the `live=False` fake-Gmail-response behavior, same as docs/12's
  CLI verification). All throwaway rows deleted afterward.

**The web form itself, over HTTP** (a real Playwright browser session
was unavailable -- locked by another concurrent session on this
machine -- so this used direct HTTP requests against a locally-running
`uvicorn` instead, which exercises the same route handlers and template
rendering, just not literal mouse clicks):
- **Preview rendering**: GET `/campaigns/{id}` with one real approved
  message showed the correct per-mailbox row (name, "1" queued, correct
  mailbox-wide `sent_today/daily_cap`, correct would-send-now split).
- **Wrong confirmation phrase**: POST with `confirm=wrong phrase`
  redirected with the expected error message, rendered correctly inside
  the Send fieldset on reload; confirmed via direct query the message's
  `status` was untouched (`approved`) and no RQ job was created.
- **Active-job state**: with a job manually enqueued (left `queued`,
  worker not started) for that campaign, GET showed "A send is
  currently queued for this campaign" and the page included
  `<meta http-equiv="refresh" content="15">`.
- **Duplicate-click guard**: POST with the *correct* phrase (`send real
  email`) while that fake job was still active was refused with "A send
  is already in progress" -- proving the confirm-phrase-accepted code
  path is reachable and correctly gated, without ever actually letting
  a `live=True` job get enqueued in the process.
- All fixtures (messages, campaigns, businesses, contacts, target runs,
  the manually-enqueued fake job) deleted afterward; confirmed empty
  tables and the `sales1` mailbox's `daily_cap` back at 50.

**Still not clicked through with literal mouse clicks in a rendered
browser** -- the HTTP-level checks above exercise the same server-side
code the browser would, but they don't catch client-rendering-only
issues (there are none expected, since this UI has no JS, but it's
worth being precise about what was and wasn't checked). Worth a real
click-through when a browser session is free, before fully trusting
this the way docs/09's and docs/11's actual `DetachedInstanceError`
catches did.

## What this deliberately doesn't do

- **No progress bar, no ETA, no cancel button.** Progress is "reload and
  look at the messages table"; there's no way to stop a running send
  from the UI once started (killing the `rq worker` process would abort
  it mid-message, which is fine -- nothing is left half-committed
  thanks to the reservation-before-send ordering from docs/12, but
  there's no *deliberate* stop button).
- **The UI is per-campaign; the CLI is system-wide.** `--live` from the
  terminal still acts on every approved message across every campaign,
  same as before -- the UI doesn't replace that, it adds a scoped
  alternative.
- **No preflight token-health check**, same known gap as docs/12.
- **Job history isn't surfaced anywhere.** A finished/failed RQ job sits
  in Redis until its result TTL expires; there's no UI page listing past
  send jobs. The messages table is the actual record of what happened
  (`status`, `sent_at`, `gmail_message_id`) -- the RQ job itself is
  just the mechanism, not meant to be a durable audit log.
