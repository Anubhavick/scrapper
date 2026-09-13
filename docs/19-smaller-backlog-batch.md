# 19 — Five smaller backlog items: edit-in-place, pagination, bulk-approve, mailbox reassignment, mid-run health re-check

ROADMAP.md item 8's "smaller, lower-urgency items" list, five of them
picked up together in one batch:

1. Target profiles were create-only, no edit-in-place.
2. No pagination on `/runs`, `/targets`, or `/campaigns`.
3. No bulk-approve for a large campaign.
4. `mailbox_id` assigned at message-generation time, not send time — a
   deactivated mailbox's message was just skipped, never reassigned.
5. No re-check of `mailboxes.is_active` mid-run in the orchestration
   loop — a single invocation snapshotted active mailboxes once, up
   front.

## 1. Target-profile edit-in-place (`api/targets.py`)

`GET`/`POST /targets/{name}/edit`, reusing `create_target`'s exact
validation path (`TargetProfile.model_validate()` + the
business_type/offer_id existence checks) via a new shared
`_validate_profile()` helper — editing can never be more permissive than
creating. The name is fixed (rendered as a disabled field, and forced
server-side regardless of what the form submits) — renaming isn't
supported; use Duplicate for a new name, same as before.

**The real trade-off, called out explicitly rather than silently
accepted:** saving an edit rewrites the whole file via the same
`yaml.safe_dump()` create already uses — there is no comment-preserving
round-trip (that would mean a `ruamel.yaml`-shaped rewrite of how this
module reads/writes YAML, out of scope for closing a "lower-urgency"
backlog item). Any hand-written comment in the file (the exact kind
docs/07 introduced for `exclude_domains`) is lost on save. The edit
form shows a standing warning about this before every save, naming the
file directly and pointing at hand-editing as the alternative if
comments matter. `/targets`'s index and `/targets/{name}`'s own page
both gained an Edit link/button alongside View/Duplicate/History.

## 2. Pagination (`api/nav.py`, `/runs`, `/targets`, `/campaigns`)

One shared `pagination_bar(page, total, base_url, extra_params=None,
page_size=PAGE_SIZE)` in `api/nav.py` (already the shared module for
exactly this kind of cross-page snippet) — returns `""` when everything
fits on one page, so every call site can drop it in unconditionally.
`PAGE_SIZE = 25`.

- `/runs`: `.offset()/.limit()` at the SQL level, a separate `COUNT(*)`
  query for the total (respecting the existing `?target_name=` filter,
  carried through Prev/Next via `extra_params` so paging never silently
  drops it).
- `/campaigns`: same shape — a `COUNT(*)` plus `.offset()/.limit()` on
  the `Campaign` query; the existing per-campaign target/counts/mailbox
  lookups now only run for the one page's worth of campaigns instead of
  every campaign ever created, a free efficiency side effect.
- `/targets`: file-based, no DB — plain Python slicing over the sorted
  `targets/*.yaml` file list.

**A real bug caught while testing this:** `pagination_bar`'s `page_size`
parameter defaulted to the module-level `PAGE_SIZE` *at nav.py's own
import time* — a normal Python default-argument gotcha. A test that
monkeypatched `PAGE_SIZE` down to 2 (to test pagination without writing
25+ fixture rows) still saw the un-patched value of 25 in the rendered
page, because the default was already baked into the function's
signature before the patch ever ran. Fixed by having every call site
pass `page_size=PAGE_SIZE` explicitly (reading its own module's current
`PAGE_SIZE` value at call time, not at import time) rather than relying
on the default.

## 3. Bulk-approve (`api/campaigns.py`)

A new "Approve all queued (N)" control, shown only once a campaign has
2+ `queued` messages (a single one already has its own Approve button).
Requires typing back `approve all <N>` (the exact current count,
recomputed server-side at submit time, not trusted from what the page
happened to show when it loaded) before `POST
/campaigns/{id}/bulk-approve` does anything — the same "type something
back" friction `jobs.py`'s `CONFIRMATION_PHRASE` already uses for a real
send, scaled to this smaller-but-still-irreversible action, and
deliberately not a JS `confirm()` dialog (this codebase has zero
client-side JavaScript anywhere; staying that way was worth the extra
few lines of server-rendered form over introducing a first `<script>`
tag for one button).

**This does not relax PROJECT.md's hard rule** ("no send without a
human clicking approve on the exact rendered text"): every `queued`
message's full subject/body is already rendered directly in an editable
textarea on the same page, not behind a collapsed `<details>` — bulk-
approving approves text that was already fully visible on screen, not
text nobody looked at. It removes the *n* clicks, not the review.
If the phrase doesn't match the live count exactly (e.g. someone else
rejected a message between page-load and submit), the whole action is
refused with an inline error rather than silently approving a different
set than what was typed.

## 4 & 5. Mailbox reassignment and mid-run `is_active` re-check (`send/orchestrator.py`, `db/orchestration.py`)

These two are related (both about a mailbox's active status changing
after a decision was already made) and shared most of their
implementation:

- **`send/orchestrator.py`**: `run_orchestration_loop()` gained a second
  optional preflight, alongside docs/16's `token_health_fn` — checked at
  the exact same point (once per mailbox, right before that mailbox's
  queue starts, not once for the whole run up front). If the mailbox is
  no longer active, every job in its queue is blocked
  (`reason="mailbox_inactive"`) with no sleep/reservation/send attempt,
  identically to how a dead token or an exhausted cap already blocks a
  mailbox's queue. Checked *before* `token_health_fn` — no point
  refreshing a token for a mailbox nobody should be sending from anyway.
- **`db/orchestration.py`**: implements this with a plain column
  `SELECT is_active FROM mailboxes WHERE id = ...` on every check, not
  `session.get(Mailbox, ...)` — the latter would return the *same*
  identity-mapped ORM object already loaded once at the top of
  `run_approved_messages()` (to build MIME messages, decrypt refresh
  tokens, etc.), whose `is_active` could be stale by the time a later
  mailbox's turn in a long-running loop actually comes up. A plain
  column select always issues a fresh query and reads whatever's
  currently committed (Postgres's default READ COMMITTED isolation), so
  it genuinely sees a concurrent deactivation from a different session —
  verified directly (see below), not assumed.
- **`_reassign_if_mailbox_inactive()`** (new, in `db/orchestration.py`):
  when a message's already-assigned mailbox has gone inactive, looks for
  another active mailbox in the *same campaign's* sender pool (never
  outside it — that pool is what a human chose when building the
  campaign) and reassigns to whichever candidate has sent the fewest
  messages today (spreads load rather than always picking the
  alphabetically-first one). The reassignment is a real, immediately
  committed write to `message.mailbox_id` — a correction to a stale
  fact, not a reservation, so it needs no advisory lock the way the cap
  decision does.
- **`build_send_jobs()`** gained a `reassign: bool = False` parameter.
  With it off (the default), a message on an inactive mailbox is simply
  excluded — the exact pre-existing behavior. `preview_approved_messages()`
  deliberately keeps calling it with the default (`False`) so its
  documented, tested "genuinely read-only, zero writes" guarantee
  (docs/12) is untouched by this feature. Only `run_approved_messages()`
  (the real-run path, `dry_run` either way) opts in with
  `reassign=True`. This means: a preview can still show a message as
  missing/unaccounted-for even though a real run would actually catch
  and fix it — an intentional, narrower discrepancy, preferred over
  giving `preview_approved_messages()` a write side effect it has never
  had.

## Tests

- `tests/test_targets_api.py`: 10 new tests for the edit routes (prefill,
  404s for a missing profile and for unparseable YAML, save persists
  changes, rename-via-form is ignored, no false "already exists"
  rejection on self-overwrite, invalid submissions leave the file
  untouched) plus 2 for pagination (no pager under one page, correct
  page-1/page-2 contents with `PAGE_SIZE` monkeypatched down to 2).
- `tests/test_nav.py` (new): 8 pure tests for `pagination_bar` (empty on
  one page or zero total, correct page/total text, Prev/Next link
  presence at the first/middle/last page, `extra_params` carried
  through, ceil-division on a partial last page).
- `tests/test_campaigns_api.py` (new): 6 pure tests for
  `_bulk_approve_phrase`/`_render_bulk_approve` (hidden at 0 or 1 queued,
  shown at 2+, error rendering, HTML-escaping the approver name).
- `tests/test_orchestrator.py`: 3 new tests for `mailbox_active_fn`
  mirroring docs/16's `token_health_fn` tests exactly (an inactive
  mailbox blocks all its jobs without reaching `reserve_fn`/`send_fn`
  while an unaffected mailbox proceeds normally; `mailbox_active_fn` is
  checked *before* `token_health_fn`, confirmed by asserting the token
  check is never even called when the mailbox is already inactive;
  omitting the parameter reproduces the old no-preflight behavior
  exactly).

`uv run pytest`: 255/255 passing (226 before this batch, +29 new).

## Verification against real Postgres and the real running server

All five verified together in one pass (`scripts/dev.sh up`), disposable
test data only, deleted afterward:

- **`/runs` pagination**: 30 throwaway `target_runs` inserted; `GET
  /runs?target_name=...&page=1` showed "Page 1 of 2" and "30 run(s)",
  `page=2` showed "Page 2 of 2" — against real Postgres, not `TestClient`.
- **`/campaigns` pagination**: 30 more throwaway campaigns; `GET
  /campaigns?page=1` rendered a "Page 1 of ..." pager.
- **Bulk-approve**: a throwaway campaign with 4 real `queued` messages.
  Posting the wrong phrase (`"approve all 999"`) left all 4 still
  `queued` and showed the rejection error; posting the correct phrase
  (`"approve all 4"`) flipped all 4 to `approved` in one request.
- **Mailbox reassignment**: a throwaway campaign with two mailboxes in
  its pool (one active, one inactive) and one `approved` message
  pointed at the inactive one. `build_send_jobs(reassign=True)`
  reassigned it to the active mailbox and the change was confirmed
  persisted in a *separate* session read afterward (not just held
  in-memory); resetting it back and calling `build_send_jobs(reassign=
  False)` confirmed the message is excluded and the mailbox_id is left
  completely untouched — `preview`'s zero-write guarantee holds.
- **Mid-run `is_active` re-check**: within one open session (standing
  in for the orchestration loop's own long-lived one), a fresh
  `SELECT is_active` correctly read `True`; a *different* session then
  committed `is_active = False` for that same mailbox; the first
  session's next fresh `SELECT is_active` — still the same open
  transaction — correctly read `False` immediately. This is the exact
  mechanism `mailbox_active_fn` relies on, confirmed directly rather
  than inferred from Postgres's isolation-level documentation alone.
- **Edit-in-place**, live: created a real disposable
  `targets/test-edit-verification.yaml` via `POST /targets`, confirmed
  `GET .../edit` shows the comment-loss warning, posted a change
  (`radius_km: 10` → `99`), confirmed the file on disk actually changed
  and the page redirected to `?updated=1`. File deleted afterward, nothing
  left in `targets/`.

All disposable rows/files confirmed deleted afterward; only the one real
mailbox and the two real target profiles (`dentists-austin-tx`,
`dentists-gurugram`) remain.

## Not done here, still real gaps

- **No comment-preserving YAML round-trip for target-profile edits** —
  see item 1 above; a real, named trade-off, not an oversight.
- **Bulk-approve has no bulk-*reject*.** Only asked for approve; reject
  is already a single click per message and rejecting in bulk has a
  different (arguably higher) risk profile worth its own consideration
  later, not bundled in here.
- **Mailbox reassignment doesn't re-check `is_active` on the
  *reassigned-to* mailbox at actual send time** — `mailbox_active_fn`
  (item 5, above) still runs afterward in the same loop and would catch
  it if that newly-assigned mailbox itself went inactive in the same
  run, so this is covered end-to-end, just by two different mechanisms
  meeting rather than one doing both jobs.
- **Send UI progress/history** (the sixth, unpicked bullet from
  ROADMAP.md's same "smaller items" list) — not part of this batch.
