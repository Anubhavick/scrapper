"""DB-touching glue for the orchestration loop -- CLAUDE.md's "next
concrete step": read `messages` where `status='approved'`, send each via
Gmail respecting caps/suppression/the randomised gap, and record the
outcome. Deliberately not covered by the mocked/pure test suite, same
reasoning as `db/repository.py`/`db/campaigns.py` -- `Message`/`Mailbox`
use the same Postgres-only JSONB/UUID columns SQLite can't stand in for.
See docs/12 for the manual verification run (dry-run only).

The actual decision logic (which mailbox goes next, cap-block-stops-the-
mailbox vs. suppression-block-skips-one-message) lives in
`send/orchestrator.py` and *is* covered by the pure test suite -- this
module only builds the `SendJob`s that logic consumes and implements the
callables it calls back into (`reserve_fn`, `send_fn`, `on_sent`, ...)
against real Postgres/Gmail.

Reservation shape (why `reserve_fn` marks the message `sent` before the
Gmail call, not after): `db.repository.reserve_send_slot`'s docstring
requires committing the reservation *inside* the advisory-locked
transaction and doing the network send only after that transaction has
committed -- holding the lock through a Gmail API call would block every
other worker on this mailbox for its duration. `count_sent_today` counts
`status='sent'` rows, so the reservation that actually holds a cap slot
against a concurrent worker has to be that same transition, committed
before the lock releases. The accepted failure mode: if the Gmail call
then fails, the message is left `sent`/`sent_at` set with
`gmail_message_id` still null -- a deliberately visible anomaly (query
`messages where status='sent' and gmail_message_id is null`) for a human
to find and follow up on, preferable to either double-sending under a
race or blocking concurrent workers for the duration of a network call.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from leadgen.db.models import CampaignMailbox, Contact, Mailbox, Message
from leadgen.db.repository import count_sent_today, fetch_suppressions
from leadgen.send.crypto import TokenCipher
from leadgen.send.gmail import build_raw_message, send_message
from leadgen.send.oauth import refresh_access_token, validate_token_health
from leadgen.send.orchestrator import SendJob, SendResult, run_orchestration_loop

logger = logging.getLogger(__name__)

__all__ = ["build_send_jobs", "preview_approved_messages", "run_approved_messages"]


def _reassign_if_mailbox_inactive(session: Session, message: Message, now: datetime) -> Mailbox | None:
    """docs/19 (ROADMAP.md's "mailbox_id assigned at generation time, not
    send time" backlog item): a message's `mailbox_id` is set once,
    at campaign-generation time (docs/11) -- if that mailbox gets
    deactivated before send, this used to mean the message was silently
    skipped forever, invisible to both `preview_approved_messages()` and
    `run_approved_messages()`, with no path back to `approved` besides
    manual DB surgery. Now: if the assigned mailbox is inactive, look for
    another active mailbox in the *same campaign's* sender pool (never a
    mailbox outside it -- that pool is what a human picked when the
    campaign was created) and reassign to whichever candidate has sent
    the fewest messages today (spreads load, doesn't just always pick the
    first one alphabetically). The reassignment is persisted immediately
    (`message.mailbox_id` really changes, committed here) since it's a
    correction to a stale fact, not a reservation -- no advisory lock
    needed, unlike `_reserve_fn`'s cap-slot decision below. Returns the
    resolved active `Mailbox`, or `None` if the whole pool is currently
    inactive (message stays exactly as skipped as it was before this)."""
    mailbox = session.get(Mailbox, message.mailbox_id)
    if mailbox is not None and mailbox.is_active:
        return mailbox

    candidates = session.execute(
        select(Mailbox)
        .join(CampaignMailbox, CampaignMailbox.mailbox_id == Mailbox.id)
        .where(CampaignMailbox.campaign_id == message.campaign_id, Mailbox.is_active.is_(True))
    ).scalars().all()
    if not candidates:
        return None

    best = min(candidates, key=lambda m: count_sent_today(session, m.id, now))
    logger.warning(
        "message %s's mailbox %s is inactive; reassigning to %s",
        message.id, message.mailbox_id, best.id,
    )
    message.mailbox_id = best.id
    session.commit()
    return best


def build_send_jobs(session: Session, *, campaign_id=None, reassign: bool = False) -> list[SendJob]:
    """One SendJob per `messages` row with status='approved', ordered
    oldest-approved-first (grouping by mailbox happens downstream in
    `send/orchestrator.py`, which preserves this relative order within
    each mailbox's queue).

    `reassign`, when `True`, resolves a message whose assigned mailbox
    has gone inactive to another active mailbox in the same campaign's
    pool (see `_reassign_if_mailbox_inactive`) -- writes to the DB as a
    side effect. Defaults to `False` so `preview_approved_messages()`'s
    "genuinely read-only, zero writes" guarantee (docs/12) holds
    regardless of this feature; only `run_approved_messages()` (the real
    run path) opts in. With `reassign=False`, a message assigned to an
    inactive mailbox is simply excluded, the original (pre-docs/19)
    behavior.

    `campaign_id`, when given, narrows this to one campaign's approved
    messages -- used by `api/campaigns.py`'s per-campaign "Send" section
    (docs/13) so a click there only ever acts on that campaign, never
    every approved message system-wide. The per-mailbox daily cap this
    feeds into `preview_approved_messages()`/`run_approved_messages()`
    is still the mailbox's *global* `count_sent_today` either way -- the
    cap is a property of the mailbox, not of any one campaign."""
    conditions = [Message.status == "approved"]
    if campaign_id is not None:
        conditions.append(Message.campaign_id == campaign_id)
    rows = session.execute(
        select(Message, Contact)
        .join(Contact, Message.contact_id == Contact.id)
        .where(*conditions)
        .order_by(Message.created_at)
    ).all()

    now = datetime.now(timezone.utc)
    jobs = []
    for message, contact in rows:
        if reassign:
            mailbox = _reassign_if_mailbox_inactive(session, message, now)
        else:
            mailbox = session.get(Mailbox, message.mailbox_id)
            if mailbox is not None and not mailbox.is_active:
                mailbox = None
        if mailbox is None:
            continue
        jobs.append(
            SendJob(message_id=message.id, mailbox_id=mailbox.id, email=contact.email, daily_cap=mailbox.daily_cap)
        )
    return jobs


def preview_approved_messages(session: Session, *, campaign_id=None) -> list[dict]:
    """A genuinely read-only look at what `run_approved_messages(dry_run=
    True)` would act on -- no advisory lock, no reservation, no write of
    any kind. This exists because "dry run" turned out to be a misleading
    name for what that function actually does: it fakes the *Gmail* call,
    but the reservation it makes along the way is real -- every eligible
    message really does transition to `status='sent'` in Postgres (see
    docs/12's "reservation shape" section for why that has to be a real
    commit, not a simulated one, to test cap-blocking meaningfully). That
    is fine, even necessary, for verifying the loop against disposable
    test data, but it means `dry_run=True` is *not* safe to point at a
    real campaign expecting a no-op preview -- it would silently consume
    those messages (flip them to `sent` with no email ever sent, leaving
    them unrecoverable by a later `--live` run without manual DB
    surgery). This function is what a real "just show me what would
    happen" default should call instead. `campaign_id` narrows this to
    one campaign's approved messages (see `build_send_jobs`'s docstring);
    `sent_today`/`daily_cap` still reflect the mailbox's real global
    count either way."""
    jobs = build_send_jobs(session, campaign_id=campaign_id)
    now = datetime.now(timezone.utc)
    by_mailbox: dict = {}
    for job in jobs:
        by_mailbox.setdefault(job.mailbox_id, []).append(job)

    summary = []
    for mailbox_id, mailbox_jobs in by_mailbox.items():
        daily_cap = mailbox_jobs[0].daily_cap
        sent_today = count_sent_today(session, mailbox_id, now)
        would_send = max(0, min(len(mailbox_jobs), daily_cap - sent_today))
        summary.append(
            {
                "mailbox_id": mailbox_id,
                "queued": len(mailbox_jobs),
                "sent_today": sent_today,
                "daily_cap": daily_cap,
                "would_send_now": would_send,
                "would_be_cap_blocked": len(mailbox_jobs) - would_send,
            }
        )
    return summary


def _reserve_fn(session: Session, message_id, mailbox_id, daily_cap: int, now: datetime):
    """The `sent_today_fn` `send_next()` calls once suppression is
    confirmed clear (see send/queue.py's docstring on why order matters
    here). Takes the advisory lock, counts today's sends, and -- only if
    there's room -- transitions this message to `sent` and commits before
    returning, all inside the one locked transaction. Always returns the
    *pre*-reservation count so `send_next()`'s own can_send recomputation
    agrees with what was just decided.

    Also re-checks that `message.status` is still `approved` under the
    lock before reserving it. The advisory lock alone only serialises the
    *cap count*; without this check, two overlapping runs over the same
    mailbox (the campaigns UI now makes accidentally starting a second
    run -- e.g. two browser tabs -- easier than the CLI ever was, see
    docs/13) could both have already fetched the same message as
    `approved` before either reserved it, and the second run would flip
    an already-`sent` message back through the whole send path a second
    time -- a real duplicate email, not just a duplicate DB write. If the
    status has moved on, this returns `daily_cap` so `can_send` reports
    "no room" and `send_next` raises `SendBlocked` (reason `"cap"`)
    instead of proceeding -- an intentionally conservative label for an
    unusual case, not a true cap exhaustion, but it produces the one
    behavior that actually matters here: never call `send_fn` twice for
    the same message."""
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": str(mailbox_id)})
    message = session.get(Message, message_id)
    if message.status != "approved":
        session.rollback()
        return daily_cap
    sent_today = count_sent_today(session, mailbox_id, now)
    if sent_today < daily_cap:
        message.status = "sent"
        message.sent_at = now
        session.commit()
    else:
        session.rollback()
    return sent_today


def run_approved_messages(
    session: Session,
    *,
    client: httpx.Client,
    client_id: str,
    client_secret: str,
    cipher: TokenCipher,
    dry_run: bool = True,
    campaign_id=None,
) -> list[SendResult]:
    """Builds jobs from every `approved` message with an active mailbox
    and drives them through `send/orchestrator.py`'s
    `run_orchestration_loop`. `dry_run=True` (the default) still runs the
    full loop for real against Postgres -- sleeps, fresh suppression/cap
    checks, and the reservation commit that flips each message's status
    to `sent` -- and only fakes the actual Gmail API call.

    **`dry_run=True` is not a safe no-op preview against real data.** It
    really consumes every eligible message (transitions it to `sent`,
    unrecoverable by a later real run without manual DB surgery) even
    though no email goes out. Use `preview_approved_messages()` above for
    a genuine read-only look at what would happen; reserve calling this
    function at all (`dry_run` either way) for disposable test data or a
    human-confirmed real send (`dry_run=False`).

    `campaign_id`, when given, narrows this to one campaign's approved
    messages -- see `build_send_jobs`'s docstring. `jobs.py`'s
    `send_campaign_messages_job()` (docs/13) always passes this; only
    the CLI script leaves it unset to act on every approved message.

    Runs `send/oauth.py`'s `validate_token_health()` once per mailbox
    before that mailbox's queue starts (docs/16, ROADMAP.md item 4) --
    a dead refresh token blocks every job for that mailbox up front
    (`reason="token"`) instead of surfacing as the same per-message
    error repeated once per remaining job, each only discovered after
    its own 90-600s sleep. Runs in `dry_run` mode too: this makes a real
    call to Google's token endpoint regardless, the same as the
    per-mailbox access-token refresh below -- `dry_run` only fakes the
    Gmail *send* itself.

    Also records real-send outcomes on the mailbox itself (docs/18,
    ROADMAP.md item 6): a real failure sets `Mailbox.last_send_error`/
    `last_send_error_at`; a subsequent real success clears both. Not run
    for `dry_run` -- the faked Gmail call there can't fail, and any
    reserve-level error in that mode isn't a signal about the mailbox's
    real-world health. `api/mailboxes.py`'s health page reads these
    alongside a live `validate_token_health()` call and the daily cap.

    Also reassigns a message stuck on a since-deactivated mailbox to
    another active mailbox in the same campaign's pool (docs/19,
    ROADMAP.md's mailbox-reassignment backlog item) -- `build_send_jobs(reassign=True)`, the real-run
    opt-in that `preview_approved_messages()` deliberately doesn't take.
    """
    jobs = build_send_jobs(session, campaign_id=campaign_id, reassign=True)
    if not jobs:
        return []

    mailboxes = {
        mailbox.id: mailbox
        for mailbox in session.execute(
            select(Mailbox).where(Mailbox.id.in_({job.mailbox_id for job in jobs}))
        ).scalars()
    }
    messages = {
        message.id: message
        for message in session.execute(
            select(Message).where(Message.id.in_({job.message_id for job in jobs}))
        ).scalars()
    }
    refresh_tokens = {
        mailbox_id: cipher.decrypt(mailbox.oauth_refresh_token_encrypted)
        for mailbox_id, mailbox in mailboxes.items()
    }
    # One access token per mailbox, refreshed lazily and reused for that
    # mailbox's whole queue -- Gmail access tokens last ~1 hour and this
    # loop's gaps are 90-600s, so refreshing per-mailbox (not per-message)
    # is enough while still never risking a stale token across mailboxes.
    access_tokens: dict = {}

    def _access_token(mailbox_id) -> str:
        if mailbox_id not in access_tokens:
            tokens = refresh_access_token(
                client,
                client_id=client_id,
                client_secret=client_secret,
                refresh_token=refresh_tokens[mailbox_id],
            )
            access_tokens[mailbox_id] = tokens["access_token"]
        return access_tokens[mailbox_id]

    # One token-health check per mailbox, cached for the same reason as
    # access_tokens above -- validate_token_health() makes a real refresh
    # call to Google, and this loop's own gaps are long enough that
    # asking once per mailbox up front is enough to catch a dead token
    # before any of that mailbox's messages sleep through their own
    # 90-600s gap only to fail identically at the end of it.
    token_health_cache: dict = {}

    def token_health_fn(mailbox_id):
        if mailbox_id not in token_health_cache:
            token_health_cache[mailbox_id] = validate_token_health(
                client,
                client_id=client_id,
                client_secret=client_secret,
                refresh_token=refresh_tokens[mailbox_id],
            )
        return token_health_cache[mailbox_id]

    def mailbox_active_fn(mailbox_id) -> bool:
        # A plain column select, not session.get(Mailbox, ...) -- the
        # latter would return the same identity-mapped object already
        # loaded into `mailboxes` above, whose is_active could be stale
        # by the time this mailbox's turn in the loop actually comes up
        # (docs/19, ROADMAP.md's mid-run is_active re-check backlog item).
        # This always issues a fresh SELECT.
        return bool(session.execute(select(Mailbox.is_active).where(Mailbox.id == mailbox_id)).scalar_one())

    def reserve_fn(job: SendJob) -> int:
        return _reserve_fn(session, job.message_id, job.mailbox_id, job.daily_cap, datetime.now(timezone.utc))

    def send_fn(job: SendJob) -> dict:
        if dry_run:
            logger.info("[dry-run] would send message %s to %s", job.message_id, job.email)
            return {"id": "dry-run", "threadId": "dry-run"}
        mailbox = mailboxes[job.mailbox_id]
        message = messages[job.message_id]
        raw = build_raw_message(
            from_addr=mailbox.email_address,
            to_addr=job.email,
            subject=message.subject,
            body=message.body,
        )
        return send_message(client, access_token=_access_token(job.mailbox_id), raw_message=raw)

    def on_sent(job: SendJob, gmail_response: dict) -> None:
        if dry_run:
            logger.info("[dry-run] message %s reserved (marked sent) but not actually sent", job.message_id)
            return
        message = session.get(Message, job.message_id)
        message.gmail_message_id = gmail_response.get("id")
        message.gmail_thread_id = gmail_response.get("threadId")
        # A successful real send is the clearest evidence a mailbox is
        # currently healthy -- clear any earlier failure rather than
        # leaving a stale error visible on /mailboxes (docs/18) forever.
        mailbox = session.get(Mailbox, job.mailbox_id)
        mailbox.last_send_error = None
        mailbox.last_send_error_at = None
        session.commit()
        logger.info("sent message %s (gmail id=%s)", job.message_id, gmail_response.get("id"))

    def on_blocked(job: SendJob, exc) -> None:
        logger.warning("message %s blocked (%s): %s", job.message_id, exc.reason, exc)

    def on_error(job: SendJob, exc: Exception) -> None:
        logger.error("message %s failed: %s", job.message_id, exc)
        # dry_run's send_fn is faked and never actually raises -- an error
        # here in dry_run mode would be a reserve_fn oddity, not a real
        # signal about the mailbox's real-world health, so only persist
        # it for a real send attempt (mirrors on_sent's own dry_run gate).
        if not dry_run:
            mailbox = session.get(Mailbox, job.mailbox_id)
            mailbox.last_send_error = str(exc)
            mailbox.last_send_error_at = datetime.now(timezone.utc)
            session.commit()

    return run_orchestration_loop(
        jobs,
        suppressed_emails_fn=lambda: fetch_suppressions(session)[0],
        suppressed_domains_fn=lambda: fetch_suppressions(session)[1],
        reserve_fn=reserve_fn,
        send_fn=send_fn,
        on_sent=on_sent,
        on_blocked=on_blocked,
        on_error=on_error,
        token_health_fn=token_health_fn,
        mailbox_active_fn=mailbox_active_fn,
    )
