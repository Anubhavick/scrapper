# 14 — Manual suppression-list UI

ROADMAP.md's top backlog item after the orchestration loop shipped:
`suppressions` had a schema, a migration, and a hard rule requiring it
be checked before every send (`send/suppression.py`) — but nothing
wrote to it. No bounce/reply monitoring exists yet (step 7, still needs
a Google CASA review), and there was no manual way to add an entry
either, short of a raw `psql` insert. A reply asking to stop contact
needs to be actionable the same day it arrives, not whenever step 7
eventually ships.

## What was built

- **`src/leadgen/api/suppressions.py`** — a fifth page on the same
  FastAPI app, `/suppressions`: lists every entry (scope, value, reason,
  added-at) and a form to add one. Mounted in `api/review.py` alongside
  the other three routers; added to `api/nav.py`'s shared nav bar.
- **Create-only, on purpose** — same convention as `api/targets.py`'s
  target profiles. No delete/edit route exists. A suppression is meant
  to be a permanent record (CLAUDE.md's schema-decisions note: "checked
  before every send... forever"); a UI that could un-suppress someone as
  easily as target-profile editing would undermine that guarantee more
  than the inconvenience of fixing a wrong entry directly in Postgres is
  worth. Worth revisiting only if this becomes a frequent need.
- **`value` is normalised exactly how the real send path reads it**:
  `_normalise_email()` (new, lowercase + a minimal shape check) for
  `scope=email`, the existing `util/domains.normalise_domain()` for
  `scope=domain` — so a domain pasted as a full URL
  (`https://www.example.com/path`) or mixed case resolves to the same
  string `send/suppression.py`'s `is_suppressed()` would compare
  against. Rejects a duplicate `(scope, value)` with a friendly message
  (checked before insert, not by catching the unique constraint) rather
  than a raw integrity-error 500.
- **A second instance of docs/09's `Form(...)`-treats-empty-as-missing
  bug**, caught the same way (verifying against the live server before
  calling this done, not by inspection): `reason: str = Form(...)`
  raised FastAPI's raw 422 JSON error the moment `reason` was submitted
  as an empty string, instead of reaching this route's own validation at
  all. Same fix as `campaigns.py`'s `approver` field: `Form("")` (a
  non-required field with an empty default) plus manual `if not reason`
  validation in the handler body, which is what actually produces the
  friendly inline error message.

## Verification

`uv run pytest`: 203/203 passing (197 prior + 6 new pure tests for
`_normalise_email()` in `tests/test_suppressions_api.py` — the routes
themselves aren't covered by the automated suite, same JSONB/UUID
reason as every other DB-touching `api/` module).

Verified for real against the live Postgres + running `uvicorn`
(`scripts/dev.sh up`, docs/13's own new shortcut) via direct HTTP
requests:

- **Empty state**: GET showed "0 entries" and "No suppressions yet."
- **Add succeeds, value normalised**: POSTed `Test@Example.COM` /
  `email` — stored and displayed as `test@example.com`.
- **Duplicate rejected**: POSTing the same normalised value again
  returned 400 with "'test@example.com' is already suppressed (added
  2026-09-13)." — not a 500, not a silent no-op.
- **Malformed email rejected**: `not-an-email` → "doesn't look like an
  email address," 400, original values preserved in the re-rendered
  form.
- **Domain normalisation**: POSTed a full URL,
  `https://www.Example-Biz.com/path?x=1`, `scope=domain` — stored as
  `example-biz.com`, matching what `normalise_domain()` would produce
  from a real business's crawled site.
- **The `Form(...)` bug above**: caught live (raw 422 with an empty
  `reason`), fixed, re-verified — empty `reason` alone, and all three
  fields empty at once, both now render the friendly inline error list
  instead of FastAPI's default response.

All test rows (`test@example.com`, `example-biz.com`) deleted from
Postgres after verification — nothing left behind.

## What this deliberately doesn't do

- **No delete/edit.** See "Create-only, on purpose" above.
- **No bulk import.** One entry per form submission; fine for the
  occasional manual add this is meant for, not a real migration path if
  a large suppression list ever needs importing.
- **Doesn't check for a domain suppression's effect on already-approved
  messages.** Adding a domain to suppressions here doesn't retroactively
  reject any `queued`/`approved` message to that domain — the
  suppression check only runs at actual send time
  (`send/queue.py`'s `check_sendable()`, immediately before each send).
  An already-approved message to a newly-suppressed contact will
  correctly be blocked (`reason="suppressed"`) the moment a send is
  attempted, per docs/12 — it just won't visibly change status until
  then.
