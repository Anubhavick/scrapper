"""Real-Postgres, real-HTTP (FastAPI TestClient) tests for api/campaigns
.py's approve/reject/edit routes -- the actual UI surface for PROJECT.md's
hard rule "no message leaves the system without a person clicking
approve on the final rendered text." Previously verified only by hand
(docs/11); `test_campaigns_api.py` covers the pure-rendering helpers but
none of these DB-backed transitions.
"""

import uuid

from sqlalchemy.orm import sessionmaker

from leadgen.db.models import Message
from tests.conftest import make_business, make_campaign, make_contact, make_mailbox, make_message, make_target_run


def _setup_queued_message(db_env):
    session_factory = sessionmaker(bind=db_env)
    setup = session_factory()
    business = make_business(setup)
    contact = make_contact(setup, business.id)
    mailbox = make_mailbox(setup)
    run = make_target_run(setup)
    campaign = make_campaign(setup, run.id, [mailbox.id])
    message = make_message(setup, campaign.id, contact.id, mailbox.id, status="queued", subject="Original subject")
    setup.commit()
    ids = (campaign.id, message.id)
    setup.close()
    return ids


def test_approve_message_requires_approver_name(client, db_env) -> None:
    campaign_id, message_id = _setup_queued_message(db_env)

    resp = client.post(f"/campaigns/{campaign_id}/messages/{message_id}/approve", data={"approver": ""})

    assert resp.status_code == 400
    assert "Approving as" in resp.text


def test_approve_message_transitions_queued_to_approved(client, db_env) -> None:
    campaign_id, message_id = _setup_queued_message(db_env)

    resp = client.post(
        f"/campaigns/{campaign_id}/messages/{message_id}/approve",
        data={"approver": "Alice"},
        follow_redirects=False,
    )

    assert resp.status_code == 303

    session_factory = sessionmaker(bind=db_env)
    verify = session_factory()
    message = verify.get(Message, message_id)
    assert message.status == "approved"
    assert message.approved_by == "Alice"
    assert message.approved_at is not None
    verify.close()


def test_reject_message_transitions_queued_to_rejected(client, db_env) -> None:
    campaign_id, message_id = _setup_queued_message(db_env)

    resp = client.post(
        f"/campaigns/{campaign_id}/messages/{message_id}/reject",
        data={"approver": "Alice"},
        follow_redirects=False,
    )

    assert resp.status_code == 303

    session_factory = sessionmaker(bind=db_env)
    verify = session_factory()
    message = verify.get(Message, message_id)
    assert message.status == "rejected"
    verify.close()


def test_edit_message_updates_subject_and_body_while_queued(client, db_env) -> None:
    campaign_id, message_id = _setup_queued_message(db_env)

    resp = client.post(
        f"/campaigns/{campaign_id}/messages/{message_id}/edit",
        data={"subject": "Edited subject", "body": "Edited body", "approver": "Alice"},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert "edit_error" not in resp.headers["location"]

    session_factory = sessionmaker(bind=db_env)
    verify = session_factory()
    message = verify.get(Message, message_id)
    assert message.subject == "Edited subject"
    assert message.body == "Edited body"
    verify.close()


def test_edit_message_is_a_no_op_once_approved(client, db_env) -> None:
    campaign_id, message_id = _setup_queued_message(db_env)
    client.post(f"/campaigns/{campaign_id}/messages/{message_id}/approve", data={"approver": "Alice"})

    resp = client.post(
        f"/campaigns/{campaign_id}/messages/{message_id}/edit",
        data={"subject": "Attempted post-approval edit", "body": "Should not stick", "approver": "Alice"},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert "edit_error" in resp.headers["location"]

    session_factory = sessionmaker(bind=db_env)
    verify = session_factory()
    message = verify.get(Message, message_id)
    assert message.subject == "Original subject"  # unchanged -- the hard rule held
    assert message.status == "approved"
    verify.close()


def test_approve_message_not_found_returns_404(client, db_env) -> None:
    resp = client.post(
        f"/campaigns/{uuid.uuid4()}/messages/{uuid.uuid4()}/approve", data={"approver": "Alice"}
    )
    assert resp.status_code == 404
