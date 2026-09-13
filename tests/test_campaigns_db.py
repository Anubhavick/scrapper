"""Real-Postgres tests for db/campaigns.py's DB-touching half --
`_select_best_contact` is already covered without a DB (test_campaigns_
logic.py); `create_campaign`/`generate_campaign_messages` need a real
Postgres and were previously verified only by hand (docs/11).
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from leadgen.config.loader import load_offer
from leadgen.db.campaigns import CampaignError, create_campaign, generate_campaign_messages
from leadgen.db.models import CampaignMailbox, EnrichmentSignal, Message, TargetRunBusiness
from leadgen.db.persist import upsert_business
from leadgen.discover.overpass import DiscoveredBusiness
from tests.conftest import make_contact, make_mailbox, make_target_run

REPO_ROOT = Path(__file__).resolve().parent.parent
OFFER = load_offer(REPO_ROOT / "config" / "offers" / "appointment-automation.yaml")


def _qualified_business(db_session, run_id, *, name="Smile Dental", signals=None):
    business = upsert_business(db_session, DiscoveredBusiness(name=name, source_id=f"node/{name}"), "dentist")
    db_session.flush()
    for key, value in (signals or {"no_online_booking": True}).items():
        db_session.add(
            EnrichmentSignal(
                business_id=business.id, key=key, value={"value": value},
                fetched_at=datetime.now(timezone.utc),
            )
        )
    db_session.add(
        TargetRunBusiness(target_run_id=run_id, business_id=business.id, qualified=True, crawl_status="ok", tags=[])
    )
    db_session.flush()
    return business


def test_create_campaign_requires_nonempty_sender_pool(db_session) -> None:
    run = make_target_run(db_session)
    db_session.commit()

    with pytest.raises(CampaignError, match="sender_pool"):
        create_campaign(db_session, target_run_id=run.id, offer_id="o", sender_pool=[])


def test_create_campaign_rejects_unknown_mailbox_name(db_session) -> None:
    run = make_target_run(db_session)
    db_session.commit()

    with pytest.raises(CampaignError, match="unknown mailbox"):
        create_campaign(db_session, target_run_id=run.id, offer_id="o", sender_pool=["does-not-exist"])


def test_create_campaign_creates_campaign_mailboxes_join_rows(db_session) -> None:
    run = make_target_run(db_session)
    mailbox1 = make_mailbox(db_session, name="sales1")
    mailbox2 = make_mailbox(db_session, name="sales2")
    db_session.commit()

    campaign = create_campaign(db_session, target_run_id=run.id, offer_id="o", sender_pool=["sales1", "sales2"])
    db_session.commit()

    joined = db_session.execute(
        select(CampaignMailbox.mailbox_id).where(CampaignMailbox.campaign_id == campaign.id)
    ).scalars().all()
    assert set(joined) == {mailbox1.id, mailbox2.id}


def test_generate_campaign_messages_raises_when_no_qualified_businesses(db_session) -> None:
    run = make_target_run(db_session)
    mailbox = make_mailbox(db_session)
    db_session.commit()
    campaign = create_campaign(db_session, target_run_id=run.id, offer_id=OFFER.id, sender_pool=[mailbox.name])
    db_session.commit()

    with pytest.raises(CampaignError, match="no qualified businesses"):
        generate_campaign_messages(db_session, campaign, offer=OFFER, templates_root=REPO_ROOT)


def test_generate_campaign_messages_creates_one_message_per_qualified_business(db_session) -> None:
    run = make_target_run(db_session)
    mailbox = make_mailbox(db_session)
    db_session.commit()
    business = _qualified_business(db_session, run.id)
    make_contact(db_session, business.id, email="jane@smile-dental.example.com", is_generic=False)
    db_session.commit()

    campaign = create_campaign(db_session, target_run_id=run.id, offer_id=OFFER.id, sender_pool=[mailbox.name])
    db_session.commit()

    created, skipped = generate_campaign_messages(db_session, campaign, offer=OFFER, templates_root=REPO_ROOT)
    db_session.commit()

    assert skipped == []
    assert len(created) == 1
    assert created[0].status == "queued"
    assert "Smile Dental" in created[0].subject
    assert created[0].mailbox_id == mailbox.id


def test_generate_campaign_messages_skips_business_with_no_contact(db_session) -> None:
    run = make_target_run(db_session)
    mailbox = make_mailbox(db_session)
    db_session.commit()
    _qualified_business(db_session, run.id)  # no contact added
    db_session.commit()

    campaign = create_campaign(db_session, target_run_id=run.id, offer_id=OFFER.id, sender_pool=[mailbox.name])
    db_session.commit()

    created, skipped = generate_campaign_messages(db_session, campaign, offer=OFFER, templates_root=REPO_ROOT)

    assert created == []
    assert len(skipped) == 1
    assert "no contact email" in skipped[0]


def test_generate_campaign_messages_is_idempotent_on_rerun(db_session) -> None:
    run = make_target_run(db_session)
    mailbox = make_mailbox(db_session)
    db_session.commit()
    business = _qualified_business(db_session, run.id)
    make_contact(db_session, business.id, email="jane@smile-dental.example.com", is_generic=False)
    db_session.commit()
    campaign = create_campaign(db_session, target_run_id=run.id, offer_id=OFFER.id, sender_pool=[mailbox.name])
    db_session.commit()

    generate_campaign_messages(db_session, campaign, offer=OFFER, templates_root=REPO_ROOT)
    db_session.commit()
    created_again, skipped_again = generate_campaign_messages(db_session, campaign, offer=OFFER, templates_root=REPO_ROOT)
    db_session.commit()

    assert created_again == []
    assert skipped_again == []
    all_messages = db_session.execute(select(Message).where(Message.campaign_id == campaign.id)).scalars().all()
    assert len(all_messages) == 1


def test_generate_campaign_messages_skips_business_whose_signals_no_longer_justify_offer(db_session) -> None:
    run = make_target_run(db_session)
    mailbox = make_mailbox(db_session)
    db_session.commit()
    # Qualified for the run, but none of the offer's relevant_signals are true.
    business = _qualified_business(db_session, run.id, signals={"no_https": True})
    make_contact(db_session, business.id, email="jane@smile-dental.example.com", is_generic=False)
    db_session.commit()

    campaign = create_campaign(db_session, target_run_id=run.id, offer_id=OFFER.id, sender_pool=[mailbox.name])
    db_session.commit()

    created, skipped = generate_campaign_messages(db_session, campaign, offer=OFFER, templates_root=REPO_ROOT)

    assert created == []
    assert len(skipped) == 1
