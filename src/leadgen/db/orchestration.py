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

from leadgen.db.models import Contact, Mailbox, Message
from leadgen.db.repository import count_sent_today, fetch_suppressions
from leadgen.send.crypto import TokenCipher
from leadgen.send.gmail import build_raw_message, send_message
from leadgen.send.oauth import refresh_access_token
from leadgen.send.orchestrator import SendJob, SendResult, run_orchestration_loop

logger = logging.getLogger(__name__)

__all__ = ["build_send_jobs", "preview_approved_messages", "run_approved_messages"]


def build_send_jobs(session: Session) -> list[SendJob]:
    """One SendJob per `messages` row with status='approved' whose
    mailbox is active, ordered so each mailbox's own queue is processed
    oldest-approved-first. A message whose mailbox was deactivated after
    approval (docs/11 flags this as a known gap -- mailbox_id is assigned
    at message-generation time, not send time) is skipped rather than
    sent from a mailbox nobody vouched for as currently active."""
    rows = session.execute(
        select(Message, Mailbox, Contact)
        .join(Mailbox, Message.mailbox_id == Mailbox.id)
        .join(Contact, Message.contact_id == Contact.id)
        .where(Message.status == "approved", Mailbox.is_active.is_(True))
        .order_by(Mailbox.id, Message.created_at)
    ).all()
    return [
        SendJob(
            message_id=message.id,
            mailbox_id=mailbox.id,
            email=contact.email,
            daily_cap=mailbox.daily_cap,
        )
        for message, mailbox, contact in rows
    ]


def preview_approved_messages(session: Session) -> list[dict]:
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
    happen" default should call instead."""
    jobs = build_send_jobs(session)
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
    agrees with what was just decided."""
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": str(mailbox_id)})
    sent_today = count_sent_today(session, mailbox_id, now)
    if sent_today < daily_cap:
        message = session.get(Message, message_id)
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
    """
    jobs = build_send_jobs(session)
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
        session.commit()
        logger.info("sent message %s (gmail id=%s)", job.message_id, gmail_response.get("id"))

    def on_blocked(job: SendJob, exc) -> None:
        logger.warning("message %s blocked (%s): %s", job.message_id, exc.reason, exc)

    def on_error(job: SendJob, exc: Exception) -> None:
        logger.error("message %s failed: %s", job.message_id, exc)

    return run_orchestration_loop(
        jobs,
        suppressed_emails_fn=lambda: fetch_suppressions(session)[0],
        suppressed_domains_fn=lambda: fetch_suppressions(session)[1],
        reserve_fn=reserve_fn,
        send_fn=send_fn,
        on_sent=on_sent,
        on_blocked=on_blocked,
        on_error=on_error,
    )
