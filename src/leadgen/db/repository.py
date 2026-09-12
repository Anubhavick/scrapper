"""Thin query functions backing the send package's decision logic
(`send/caps.py`, `send/suppression.py`). Deliberately not covered by the
mocked/pure test suite — these need a real Postgres (JSONB/UUID columns
aren't SQLite-compatible) to exercise meaningfully. Verify these against
`docker compose up -d` + a migrated database before relying on them, the
same way docs/03 flags live Overpass/Nominatim reachability as unverified.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from leadgen.db.models import Message, Suppression
from leadgen.send.caps import can_send, start_of_day_utc

__all__ = ["count_sent_today", "fetch_suppressions", "reserve_send_slot"]


def count_sent_today(session: Session, mailbox_id, now: datetime) -> int:
    cutoff = start_of_day_utc(now)
    stmt = select(func.count()).select_from(Message).where(
        Message.mailbox_id == mailbox_id,
        Message.status == "sent",
        Message.sent_at >= cutoff,
    )
    return session.execute(stmt).scalar_one()


def reserve_send_slot(session: Session, mailbox_id, daily_cap: int, now: datetime) -> bool:
    """Atomically decide whether `mailbox_id` has room for one more send
    today, inside the caller's open transaction.

    `mailboxes` has no counter column to increment (CLAUDE.md's schema
    decisions — a mutable counter needs a correctly-timed reset job, and
    getting that wrong silently blows the 50/day cap that is this
    system's entire value proposition). `count_sent_today`'s COUNT(*) is
    correct in isolation but not under concurrency: two workers can both
    read "39 sent, cap 40" before either commits its own send, and both
    proceed.

    This takes a Postgres transaction-level advisory lock keyed on the
    mailbox id (`pg_advisory_xact_lock`, auto-released at commit/rollback,
    never left dangling by a crashed worker) before re-running the count.
    A second worker calling this for the same mailbox blocks here until
    the first worker's transaction ends — at which point it sees that
    worker's newly-committed `messages` row and re-counts against it.

    Callers MUST, in the same transaction: call this, and if it returns
    True, insert (or transition) the `messages` row that represents the
    send being reserved, then commit promptly — the lock is held for the
    lifetime of the transaction, so anything slow inside it (the actual
    Gmail API call included) blocks every other worker on this mailbox.
    Do the network send after committing the reservation, not inside it.
    """
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": str(mailbox_id)})
    sent_today = count_sent_today(session, mailbox_id, now)
    return can_send(sent_today, daily_cap)


def fetch_suppressions(session: Session) -> tuple[set[str], set[str]]:
    """Returns (suppressed_emails, suppressed_domains), both lowercase."""
    rows = session.execute(select(Suppression.scope, Suppression.value)).all()
    emails = {value for scope, value in rows if scope == "email"}
    domains = {value for scope, value in rows if scope == "domain"}
    return emails, domains
