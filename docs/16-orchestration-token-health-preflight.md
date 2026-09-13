# 16 — Preflight token-health check in the orchestration loop

ROADMAP.md item 4, flagged since docs/12 shipped: `send/oauth.py` already
had `validate_token_health()` (a real refresh-grant call to Google, since
that's the standard way to check a refresh token is still alive), but
nothing in the orchestration loop called it before starting a mailbox's
queue. A dead token (revoked consent, the 7-day Testing-mode expiry,
an account security event) used to only surface once the loop actually
tried to send: `send_fn` would raise, get caught by
`run_orchestration_loop`'s generic `except Exception`, get logged as a
per-message `"error"`, and the loop would move on to the *next* message
in that same mailbox's queue — which would fail identically, after
sleeping through its own 90-600s gap first, and so on for every
remaining message. Not wrong, just slow and noisy for something knowable
up front in one call.

## What was built

- **`src/leadgen/send/orchestrator.py`** — `run_orchestration_loop()`
  gained an optional `token_health_fn: Callable[[mailbox_id],
  TokenHealth] | None` parameter, called once per mailbox *before* that
  mailbox's first job is attempted (not per message). When it reports
  unhealthy, every job for that mailbox is immediately reported blocked
  (`SendBlocked(reason="token")`, via the existing `on_blocked` callback
  and `SendResult`s) — no sleep, no `reserve_fn`, no `send_fn` call for
  any of them. This mirrors how a `"cap"` block already stops the rest
  of a mailbox's queue, just decided up front instead of discovered
  after the first job fails. Omitting the parameter (`None`, the
  default) reproduces the exact old behavior — existing callers that
  don't pass it are unaffected. `TokenHealth` is only imported under
  `TYPE_CHECKING` (matching `signals.py`'s `PageFetch` pattern) so this
  module keeps its zero-hard-dependency, fully-mocked-testable shape.
- **`src/leadgen/db/orchestration.py`** — `run_approved_messages()` now
  builds a `token_health_fn` that calls `validate_token_health()` once
  per mailbox and caches the result for the rest of that call, the same
  caching shape as the existing per-mailbox `_access_token()` lazy
  refresh just below it. Runs in `dry_run` mode too, deliberately: this
  makes a real call to Google's token endpoint either way, exactly like
  the per-mailbox access-token refresh already does — `dry_run` only
  ever fakes the Gmail *send* call itself, never the OAuth calls around
  it.

## Tests

`tests/test_orchestrator.py`: three new pure tests — an unhealthy
mailbox blocks every one of its jobs without ever reaching `reserve_fn`/
`send_fn` (while an unaffected second mailbox sends normally), a healthy
mailbox is checked exactly once regardless of how many jobs it has, and
omitting `token_health_fn` entirely reproduces the old no-preflight
behavior exactly (a regression guard for existing callers).

## Verification against real Postgres + real Google OAuth

`db/orchestration.py` is excluded from the automated suite for the usual
JSONB/UUID-vs-SQLite reason (CLAUDE.md/MANUAL.md §7) — verified by hand
instead, against real Postgres and a real call to Google's token
endpoint, using disposable test data cleaned up immediately after:

- Built one throwaway `target_run` → `campaign` → two `messages`
  (`status='approved'`), one addressed through the one real, already-
  authorized mailbox in this DB (`anubhav.ickk@gmail.com`), the other
  through a brand-new throwaway `mailboxes` row whose
  `oauth_refresh_token_encrypted` was a deliberately garbage string (not
  a mock — the real encryption round-trip, then a real refresh-grant
  call against Google).
- Ran `run_approved_messages(session, dry_run=True, campaign_id=<the
  throwaway campaign>)`.
- **Result:** the garbage-token mailbox's message was blocked
  immediately (`reason="token"`, Google's real response:
  `{"error": "invalid_grant", "error_description": "Bad Request"}`),
  left `status='approved'`/`sent_at=None` — never reserved, never slept
  through a gap it was always going to fail after. The real mailbox's
  message went through the full loop normally (real sleep, real
  reservation, `status` flipped to `sent` per `dry_run`'s existing
  semantics).
- All disposable rows (the throwaway mailbox, target_run, campaign,
  two businesses/contacts/messages) deleted afterward; confirmed by
  re-querying that only the one real mailbox and zero `test*`-named rows
  remain.

`uv run pytest`: 210/210 passing (207 before this batch, +3 new).

## Not done here, still real gaps

- **No re-check mid-run.** The health check runs once per mailbox at the
  very start of that mailbox's queue; a token that dies *during* a long
  run (rare, but possible across many 90-600s gaps) still surfaces the
  old way, as a per-message error on whichever send happens to hit it.
  Not worth a periodic re-check for how rare and short-lived that window
  is in practice.
- **No UI surface for a token-health failure yet.** It's logged
  (`on_blocked`) and shows up as a `"blocked"`/`reason="token"` result
  from the CLI script or the campaigns-UI send job, same as a cap or
  suppression block — there's no dedicated "this mailbox needs
  re-authorizing" banner anywhere. Worth adding if/when ROADMAP.md's
  item 6 (mailbox health/reputation visibility) gets picked up.
