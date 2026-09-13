"""Real-Postgres tests for db/orchestration.py -- the DB-touching glue
that turns `approved` messages into real sends. This is the highest-risk
module in the codebase (caps, suppression, no-double-send, the
legal_region gate from docs/20) and, until this file, had zero automated
coverage -- every real bug in it (docs/12, docs/13, docs/19, docs/20) was
caught by manual verification against a real Postgres, never a test.

`send/queue.py`'s real 90-600s randomised gap is monkeypatched to zero
throughout -- these tests need the real cap/suppression/reservation
*logic*, not the real wait, which would make this file take tens of
minutes to run.
"""

import threading
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

import leadgen.db.orchestration as orchestration
import leadgen.send.queue as queue_module
from leadgen.db.models import Business, Campaign, CampaignMailbox, Contact, Mailbox, Message, TargetRun
from leadgen.db.orchestration import _reserve_fn, build_send_jobs, preview_approved_messages, run_approved_messages
from leadgen.send.crypto import TokenCipher, generate_key
from leadgen.send.oauth import TokenHealth
from tests.conftest import make_business, make_campaign, make_contact, make_mailbox, make_message, make_target_run

REPO_ROOT = Path(__file__).resolve().parent.parent

EU_PROFILE_YAML = yaml.safe_dump({
    "name": "eu-profile", "business_type": "dentist", "legal_region": "eu_uk", "requires_opt_in": True,
    "location": {"mode": "radius", "center": "Berlin", "radius_km": 10},
    "source": {"primary": "overpass"}, "outreach": {"offer_id": "o", "sender_pool": ["s1"]},
})
US_PROFILE_YAML = yaml.safe_dump({
    "name": "us-profile", "business_type": "dentist", "legal_region": "us",
    "location": {"mode": "radius", "center": "Austin", "radius_km": 10},
    "source": {"primary": "overpass"}, "outreach": {"offer_id": "o", "sender_pool": ["s1"]},
})


def _setup_one_approved_message(db_session, **mailbox_overrides):
    business = make_business(db_session)
    contact = make_contact(db_session, business.id)
    mailbox = make_mailbox(db_session, **mailbox_overrides)
    run = make_target_run(db_session, profile_yaml=US_PROFILE_YAML)
    campaign = make_campaign(db_session, run.id, [mailbox.id])
    message = make_message(db_session, campaign.id, contact.id, mailbox.id, status="approved")
    db_session.commit()
    return business, contact, mailbox, run, campaign, message


# ------------------------------------------------------------- build_send_jobs


def test_build_send_jobs_only_includes_approved_messages(db_session) -> None:
    business = make_business(db_session)
    contact1 = make_contact(db_session, business.id)
    contact2 = make_contact(db_session, business.id)
    mailbox = make_mailbox(db_session)
    run = make_target_run(db_session, profile_yaml=US_PROFILE_YAML)
    campaign = make_campaign(db_session, run.id, [mailbox.id])
    approved = make_message(db_session, campaign.id, contact1.id, mailbox.id, status="approved")
    make_message(db_session, campaign.id, contact2.id, mailbox.id, status="queued")
    db_session.commit()

    jobs = build_send_jobs(db_session, campaign_id=campaign.id)

    assert [j.message_id for j in jobs] == [approved.id]


def test_build_send_jobs_excludes_inactive_mailbox_without_reassign(db_session) -> None:
    _, _, mailbox, _, campaign, _ = _setup_one_approved_message(db_session, is_active=False)

    jobs = build_send_jobs(db_session, campaign_id=campaign.id, reassign=False)

    assert jobs == []


def test_build_send_jobs_reassigns_to_active_mailbox_in_same_campaign_pool(db_session) -> None:
    business = make_business(db_session)
    contact = make_contact(db_session, business.id)
    dead_mailbox = make_mailbox(db_session, is_active=False)
    live_mailbox = make_mailbox(db_session, is_active=True)
    run = make_target_run(db_session, profile_yaml=US_PROFILE_YAML)
    campaign = make_campaign(db_session, run.id, [dead_mailbox.id, live_mailbox.id])
    message = make_message(db_session, campaign.id, contact.id, dead_mailbox.id, status="approved")
    db_session.commit()

    jobs = build_send_jobs(db_session, campaign_id=campaign.id, reassign=True)
    db_session.commit()

    assert len(jobs) == 1
    assert jobs[0].mailbox_id == live_mailbox.id
    db_session.refresh(message)
    assert message.mailbox_id == live_mailbox.id


def test_build_send_jobs_blocks_eu_uk_campaign_entirely(db_session) -> None:
    business = make_business(db_session)
    contact = make_contact(db_session, business.id)
    mailbox = make_mailbox(db_session)
    run = make_target_run(db_session, profile_yaml=EU_PROFILE_YAML)
    campaign = make_campaign(db_session, run.id, [mailbox.id])
    make_message(db_session, campaign.id, contact.id, mailbox.id, status="approved")
    db_session.commit()

    jobs = build_send_jobs(db_session, campaign_id=campaign.id)

    assert jobs == []


def test_build_send_jobs_us_campaign_unaffected_by_legal_region_gate(db_session) -> None:
    _, _, _, _, campaign, message = _setup_one_approved_message(db_session)

    jobs = build_send_jobs(db_session, campaign_id=campaign.id)

    assert [j.message_id for j in jobs] == [message.id]


# --------------------------------------------------------- preview_approved_messages


def test_preview_approved_messages_reports_would_send_and_cap_blocked(db_session) -> None:
    business = make_business(db_session)
    mailbox = make_mailbox(db_session, daily_cap=1)
    run = make_target_run(db_session, profile_yaml=US_PROFILE_YAML)
    campaign = make_campaign(db_session, run.id, [mailbox.id])
    contact1 = make_contact(db_session, business.id)
    contact2 = make_contact(db_session, business.id)
    make_message(db_session, campaign.id, contact1.id, mailbox.id, status="approved")
    make_message(db_session, campaign.id, contact2.id, mailbox.id, status="approved")
    db_session.commit()

    summary = preview_approved_messages(db_session, campaign_id=campaign.id)

    assert len(summary) == 1
    row = summary[0]
    assert row["queued"] == 2
    assert row["daily_cap"] == 1
    assert row["sent_today"] == 0
    assert row["would_send_now"] == 1
    assert row["would_be_cap_blocked"] == 1

    # Genuinely read-only: no message actually changed status.
    still_approved = db_session.execute(
        select(Message).where(Message.campaign_id == campaign.id, Message.status == "approved")
    ).scalars().all()
    assert len(still_approved) == 2


# ----------------------------------------------------------- run_approved_messages


def _fake_httpx_client() -> httpx.Client:
    return httpx.Client()


def test_run_approved_messages_sends_and_records_gmail_ids(db_session, monkeypatch) -> None:
    monkeypatch.setattr(queue_module, "wait_before_next_send", lambda: None)
    monkeypatch.setattr(orchestration, "validate_token_health", lambda *a, **k: TokenHealth(healthy=True))
    monkeypatch.setattr(orchestration, "refresh_access_token", lambda *a, **k: {"access_token": "fake-token"})
    monkeypatch.setattr(
        orchestration, "send_message",
        lambda *a, **k: {"id": "gmail-real-id-123", "threadId": "gmail-thread-456"},
    )

    key = generate_key()
    cipher = TokenCipher(key)
    _, _, mailbox, _, campaign, message = _setup_one_approved_message(
        db_session, oauth_refresh_token_encrypted=cipher.encrypt("refresh-token-placeholder"),
    )

    results = run_approved_messages(
        db_session, client=_fake_httpx_client(), client_id="cid", client_secret="secret",
        cipher=cipher, dry_run=False, campaign_id=campaign.id,
    )

    assert len(results) == 1
    assert results[0].outcome == "sent"

    db_session.refresh(message)
    assert message.status == "sent"
    assert message.gmail_message_id == "gmail-real-id-123"
    assert message.gmail_thread_id == "gmail-thread-456"

    db_session.refresh(mailbox)
    assert mailbox.last_send_error is None


def test_run_approved_messages_records_last_send_error_on_gmail_failure(db_session, monkeypatch) -> None:
    monkeypatch.setattr(queue_module, "wait_before_next_send", lambda: None)
    monkeypatch.setattr(orchestration, "validate_token_health", lambda *a, **k: TokenHealth(healthy=True))
    monkeypatch.setattr(orchestration, "refresh_access_token", lambda *a, **k: {"access_token": "fake-token"})

    def _boom(*a, **k):
        raise RuntimeError("Gmail API said no")

    monkeypatch.setattr(orchestration, "send_message", _boom)

    key = generate_key()
    cipher = TokenCipher(key)
    _, _, mailbox, _, campaign, message = _setup_one_approved_message(
        db_session, oauth_refresh_token_encrypted=cipher.encrypt("refresh-token-placeholder"),
    )

    results = run_approved_messages(
        db_session, client=_fake_httpx_client(), client_id="cid", client_secret="secret",
        cipher=cipher, dry_run=False, campaign_id=campaign.id,
    )

    assert results[0].outcome == "error"
    db_session.refresh(mailbox)
    assert mailbox.last_send_error is not None
    assert "Gmail API said no" in mailbox.last_send_error


def test_run_approved_messages_blocks_unhealthy_mailbox_without_reserving(db_session, monkeypatch) -> None:
    monkeypatch.setattr(queue_module, "wait_before_next_send", lambda: None)
    monkeypatch.setattr(orchestration, "validate_token_health", lambda *a, **k: TokenHealth(healthy=False, reason="invalid_grant"))
    monkeypatch.setattr(orchestration, "refresh_access_token", lambda *a, **k: {"access_token": "fake-token"})

    key = generate_key()
    cipher = TokenCipher(key)
    _, _, mailbox, _, campaign, message = _setup_one_approved_message(
        db_session, oauth_refresh_token_encrypted=cipher.encrypt("refresh-token-placeholder"),
    )

    results = run_approved_messages(
        db_session, client=_fake_httpx_client(), client_id="cid", client_secret="secret",
        cipher=cipher, dry_run=False, campaign_id=campaign.id,
    )

    assert results[0].outcome == "blocked"
    db_session.refresh(message)
    assert message.status == "approved"  # never reserved
    assert message.sent_at is None


# -------------------------------------------------- _reserve_fn concurrency


def test_reserve_fn_prevents_double_send_at_cap_under_real_concurrent_workers(db_env) -> None:
    """The whole point of `pg_advisory_xact_lock` in `_reserve_fn`: two
    workers racing the same mailbox's last cap slot must not both send.
    Runs two real, independent Postgres connections in real threads --
    correctness here comes from Postgres serialising the two
    transactions on the advisory lock, not from Python-level timing, so
    this is deterministic rather than a flaky race."""
    session_factory = sessionmaker(bind=db_env)
    setup = session_factory()
    business = make_business(setup)
    contact1 = make_contact(setup, business.id)
    contact2 = make_contact(setup, business.id)
    mailbox = make_mailbox(setup, daily_cap=1)
    run = make_target_run(setup, profile_yaml=US_PROFILE_YAML)
    campaign = make_campaign(setup, run.id, [mailbox.id])
    message1 = make_message(setup, campaign.id, contact1.id, mailbox.id, status="approved")
    message2 = make_message(setup, campaign.id, contact2.id, mailbox.id, status="approved")
    setup.commit()
    message1_id, message2_id, mailbox_id, daily_cap = message1.id, message2.id, mailbox.id, mailbox.daily_cap
    setup.close()

    now = datetime.now(timezone.utc)
    results: dict[str, int] = {}

    def worker(name: str, message_id) -> None:
        session = session_factory()
        try:
            results[name] = _reserve_fn(session, message_id, mailbox_id, daily_cap, now)
        finally:
            session.close()

    t1 = threading.Thread(target=worker, args=("a", message1_id))
    t2 = threading.Thread(target=worker, args=("b", message2_id))
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    verify = session_factory()
    statuses = sorted(
        verify.execute(
            select(Message.status).where(Message.id.in_([message1_id, message2_id]))
        ).scalars().all()
    )
    verify.close()

    # Exactly one of the two got the mailbox's one cap slot; the other
    # was correctly refused -- never both "sent" (a real double-send).
    assert statuses == ["approved", "sent"]
    assert sorted(results.values()) == [0, 1]  # one reserved (pre-count 0), one blocked (pre-count already 1)
