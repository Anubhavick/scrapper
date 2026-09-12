# 06 — Gmail OAuth, compose, and send-queue logic

Build order step 6: "Gmail OAuth + send queue + caps + suppression."

## A deliberate deviation from PROJECT.md's own build order

PROJECT.md frames step 5's CSV export as a hard gate: **read 200 rows by
hand** before building anything past it, specifically so the sending half
only gets built if the discover/enrich data is actually good enough to
justify it. That hand-review hasn't happened — there's no confirmed live
run against real Overpass/Nominatim data yet (see docs/03, docs/05).

This step was built anyway, on direct instruction, ahead of that review.
Nothing here required real target data to build or test — the sends
themselves are still entirely gated behind human approval and the parts
that touch real Google accounts need API keys that don't exist yet either
— but the ordering is still worth flagging so it isn't mistaken for "the
data was reviewed and passed."

## What was built

- `src/leadgen/send/crypto.py` — `TokenCipher`, a thin wrapper around
  `cryptography.fernet.Fernet` for `mailboxes.oauth_refresh_token_encrypted`.
  Wrong-key or tampered ciphertext raises `DecryptionError` rather than
  returning garbage.
- `src/leadgen/send/oauth.py` — the Gmail OAuth authorization-code flow:
  `build_authorization_url()`, `exchange_code_for_tokens()`,
  `refresh_access_token()`. Scope is `gmail.send` only — see the note
  below on why that matters.
- `src/leadgen/send/gmail.py` — `build_raw_message()` (RFC 2822 → base64url,
  what the Gmail API's `raw` field requires) and `send_message()`
  (`users.messages.send`). Nothing here reads a mailbox; that's stage 7
  and a different, more sensitive scope.
- `src/leadgen/compose/render.py` — `render_message()`: fills an offer's
  subject/body template with one generated line grounded in a real
  boolean signal (`SIGNAL_LINES`). Raises `ComposeError` if none of the
  offer's `relevant_signals` were actually truthy for this business —
  treated as a bug upstream (qualification should have filtered it out),
  not something to paper over with a generic line.
- `templates/appointment_automation.txt` — the example offer's body
  template; referenced by `config/offers/appointment-automation.yaml` but
  didn't exist until now.
- `src/leadgen/send/caps.py` — `can_send(sent_today, daily_cap)`, pure.
  `start_of_day_utc()` defines "today" as the UTC calendar day (see its
  docstring — PROJECT.md doesn't specify a timezone and mailboxes don't
  carry one).
- `src/leadgen/send/suppression.py` — `is_suppressed()`, pure: checks an
  email against both a suppressed-email set and a suppressed-domain set
  (via `normalise_domain()`), matching the `(scope, value)` shape of the
  `suppressions` table.
- `src/leadgen/send/queue.py` — `check_sendable()` composes suppression +
  cap into one call that raises `SendBlocked` (with a reason) instead of
  silently skipping; `random_delay_seconds()` / `wait_before_next_send()`
  implement the 90–600s randomised gap. No burst logic needed — a single
  gap enforced between every send *is* the anti-burst mechanism.
- `src/leadgen/db/session.py` — `get_engine()` / `get_sessionmaker()` /
  `session_scope()`, reading `DATABASE_URL` the same way `alembic/env.py`
  already does. This didn't exist before step 6 because nothing had
  needed to talk to the database yet.
- `src/leadgen/db/repository.py` — `count_sent_today()` and
  `fetch_suppressions()`, the actual queries behind `caps.py` /
  `suppression.py`'s pure decisions. See the testing note below.
- 28 new tests across `test_crypto.py`, `test_oauth.py`, `test_gmail.py`,
  `test_compose.py`, `test_caps.py`, `test_suppression.py`,
  `test_queue.py` — all pure-function or mocked-`httpx` tests, no real
  network, no real database.

## Why decision logic and database queries are split apart

`db/models.py` uses Postgres-specific `JSONB`/`UUID` column types (a
deliberate choice from step 1 — see CLAUDE.md), which means an in-memory
SQLite substitute can't stand in for real tests the way it could for a
simpler schema. Rather than fight that with dialect hacks, or skip testing
this step's logic at all, the actual decisions (`can_send`,
`is_suppressed`, `check_sendable`) are pure functions that take
already-fetched Python values — sets, ints, an email string — and are
fully covered by the pure test suite. The two functions that actually
query Postgres (`count_sent_today`, `fetch_suppressions`) are kept
deliberately thin and are **not** covered by that suite; they need a real,
migrated Postgres to verify, the same honesty applied to Overpass/
Nominatim reachability in docs/03. Run them against `docker compose up -d`
+ `alembic upgrade head` before trusting them, not just against this
test run.

## Gmail scope and OAuth-verification notes (relevant once real keys exist)

- `gmail.send` is a **sensitive** scope, not a **restricted** one — it
  doesn't need a CASA security assessment, just the OAuth consent screen
  out of "Testing" status eventually. `gmail.readonly` / `gmail.modify`
  (needed for stage 7's bounce/reply handling) *are* restricted and do
  need CASA — deliberately not requested by anything built so far.
- While the OAuth consent screen is in **Testing** mode, refresh tokens
  for sensitive scopes expire after **7 days** and have to be re-issued
  by re-running the consent flow. This is a real operational trap for a
  system meant to run unattended — don't discover it in production.
  `prompt=consent` is hardcoded into `build_authorization_url()` because
  without it, a re-auth on an already-granted account silently returns no
  `refresh_token` at all, and the caller has to know to ask for one again.

## Still not built (deliberately, out of scope for this step)

- **Nothing calls any of this yet.** There's no orchestration function
  that reads `messages` rows with `status = 'approved'`, iterates
  mailboxes, calls `check_sendable()` → `gmail.send_message()` → updates
  the row with `sent_at`/`gmail_message_id`/`gmail_thread_id`, and
  sleeps `wait_before_next_send()` between them. That's plumbing over
  what's already built here, not a new decision — worth doing once real
  Gmail API credentials exist to actually exercise it against, rather
  than writing untestable glue against a mocked Gmail API in advance.
- **No campaign/message-creation code.** Nothing yet turns a CSV of
  qualified leads (step 5's output) into `campaigns` + `messages` rows.
  That's also plumbing, not decision logic, and needs the database write
  path this step still doesn't have either.
- **No review/approval UI.** PROJECT.md's hard rule ("no message sends
  without a human clicking approve on the final rendered text") has
  nothing to click yet — that's step 8's FastAPI + minimal review UI.
- **Bounce/reply monitoring** is step 7, needs the restricted
  `gmail.readonly`/`gmail.modify` scopes and CASA, and is untouched.

## `scripts/authorize_mailbox.py` — the operational script that followed

Building the modules above didn't require real Google credentials, but
using them does — so once a Google Cloud OAuth client existed,
`scripts/authorize_mailbox.py` was added as the one-time interactive tool
that turns those credentials into an actual `mailboxes` row:

```
uv run python scripts/authorize_mailbox.py <short-name> <email-address>
```

It opens the browser to `build_authorization_url()`'s URL, catches the
redirect on a local loopback HTTP server (works with a "Desktop app"
OAuth client without registering a redirect URI — Google allows any
`localhost` port for that client type), calls
`exchange_code_for_tokens()`, encrypts the refresh token with
`TokenCipher`, and inserts one `Mailbox` row via `db.session`.

Two real gotchas hit while first running this, worth remembering:

- **Test users aren't automatic.** While the OAuth consent screen is in
  "Testing" status, even the Google account that owns the Cloud project
  gets `Error 403: access_denied` unless it's *also* explicitly added
  under Test users. Owning the project isn't the same as being allowed to
  authorize against it.
- **`gmail.send` doesn't cover `users.getProfile`.** The first version of
  this script fetched the mailbox's email address automatically by
  calling Gmail's profile endpoint with the fresh access token — that
  call 403'd, because the `gmail.send` scope alone doesn't grant read
  access to account profile info. Rather than add a broader scope just to
  look up an address the operator already knows, the script now takes the
  email address as a plain argument instead. One fewer scope requested is
  also just a better default given the hard rule about minimal, auditable
  access.

Verified end-to-end against a real Google account: the mailbox now
exists in Postgres with `oauth_refresh_token_encrypted` populated (228
base64 chars, confirmed via `psql`, not plaintext) and everything else
defaulted (`daily_cap = 50`, `is_active = true`).

## Verification

`uv run pytest`: 137/137 passing (109 from steps 1–5, 28 new).

`scripts/authorize_mailbox.py` has now been run successfully against a
real Google account — see above. `send/gmail.send_message()` (the actual
`users.messages.send` call) is still unverified against the live Gmail
API; that's the natural next thing to test now that a real mailbox and
access token are obtainable.
