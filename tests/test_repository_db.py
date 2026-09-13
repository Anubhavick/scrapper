"""Real-Postgres tests for db/repository.py -- the COUNT(*)/advisory-lock
queries behind send/caps.py's and send/suppression.py's decisions.
Previously verified by hand only (docs/03's reachability-gap framing
applies here too); see tests/conftest.py for the ephemeral-Postgres
fixtures this file depends on.
"""

from datetime import datetime, timedelta, timezone

from tests.conftest import make_business, make_campaign, make_contact, make_mailbox, make_message, make_target_run

from leadgen.db.models import Suppression
from leadgen.db.repository import count_sent_today, fetch_suppressions, reserve_send_slot


def test_count_sent_today_counts_only_todays_sent_messages(db_session) -> None:
    now = datetime.now(timezone.utc)
    business = make_business(db_session)
    contact = make_contact(db_session, business.id)
    mailbox = make_mailbox(db_session)
    run = make_target_run(db_session)
    campaign = make_campaign(db_session, run.id, [mailbox.id])

    sent_today = make_message(db_session, campaign.id, contact.id, mailbox.id, status="sent", sent_at=now)
    contact2 = make_contact(db_session, business.id)
    make_message(
        db_session, campaign.id, contact2.id, mailbox.id,
        status="sent", sent_at=now - timedelta(days=1),
    )
    contact3 = make_contact(db_session, business.id)
    make_message(db_session, campaign.id, contact3.id, mailbox.id, status="queued")
    db_session.commit()

    assert count_sent_today(db_session, mailbox.id, now) == 1
    assert sent_today.status == "sent"


def test_count_sent_today_zero_for_fresh_mailbox(db_session) -> None:
    mailbox = make_mailbox(db_session)
    db_session.commit()
    assert count_sent_today(db_session, mailbox.id, datetime.now(timezone.utc)) == 0


def test_reserve_send_slot_true_under_cap(db_session) -> None:
    mailbox = make_mailbox(db_session, daily_cap=5)
    db_session.commit()
    assert reserve_send_slot(db_session, mailbox.id, mailbox.daily_cap, datetime.now(timezone.utc)) is True


def test_reserve_send_slot_false_at_cap(db_session) -> None:
    now = datetime.now(timezone.utc)
    business = make_business(db_session)
    contact = make_contact(db_session, business.id)
    mailbox = make_mailbox(db_session, daily_cap=1)
    run = make_target_run(db_session)
    campaign = make_campaign(db_session, run.id, [mailbox.id])
    make_message(db_session, campaign.id, contact.id, mailbox.id, status="sent", sent_at=now)
    db_session.commit()

    assert reserve_send_slot(db_session, mailbox.id, mailbox.daily_cap, now) is False


def test_fetch_suppressions_splits_by_scope_and_lowercases_are_preserved_as_stored(db_session) -> None:
    db_session.add(Suppression(scope="email", value="blocked@example.com", reason="unsubscribed"))
    db_session.add(Suppression(scope="domain", value="blocked.com", reason="complaint"))
    db_session.commit()

    emails, domains = fetch_suppressions(db_session)

    assert emails == {"blocked@example.com"}
    assert domains == {"blocked.com"}


def test_fetch_suppressions_empty_when_no_rows(db_session) -> None:
    emails, domains = fetch_suppressions(db_session)
    assert emails == set()
    assert domains == set()


def test_db_session_rolls_back_between_tests_part_one(db_session) -> None:
    """Paired with the _part_two test below -- if the SAVEPOINT-rollback
    fixture in conftest.py were broken, this row would still be visible
    there and that test would fail instead of (correctly) finding
    nothing. Confirms test isolation itself, not just repository.py."""
    make_mailbox(db_session, name="isolation-canary")
    db_session.commit()


def test_db_session_rolls_back_between_tests_part_two(db_session) -> None:
    from leadgen.db.models import Mailbox
    from sqlalchemy import select

    canary = db_session.execute(
        select(Mailbox).where(Mailbox.name == "isolation-canary")
    ).scalar_one_or_none()
    assert canary is None
