"""Thin query functions backing the send package's decision logic
(`send/caps.py`, `send/suppression.py`). Deliberately not covered by the
mocked/pure test suite — these need a real Postgres (JSONB/UUID columns
aren't SQLite-compatible) to exercise meaningfully. Verify these against
`docker compose up -d` + a migrated database before relying on them, the
same way docs/03 flags live Overpass/Nominatim reachability as unverified.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from leadgen.db.models import Message, Suppression
from leadgen.send.caps import start_of_day_utc

__all__ = ["count_sent_today", "fetch_suppressions"]


def count_sent_today(session: Session, mailbox_id, now: datetime) -> int:
    cutoff = start_of_day_utc(now)
    stmt = select(func.count()).select_from(Message).where(
        Message.mailbox_id == mailbox_id,
        Message.status == "sent",
        Message.sent_at >= cutoff,
    )
    return session.execute(stmt).scalar_one()


def fetch_suppressions(session: Session) -> tuple[set[str], set[str]]:
    """Returns (suppressed_emails, suppressed_domains), both lowercase."""
    rows = session.execute(select(Suppression.scope, Suppression.value)).all()
    emails = {value for scope, value in rows if scope == "email"}
    domains = {value for scope, value in rows if scope == "domain"}
    return emails, domains
