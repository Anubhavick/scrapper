"""Real-Postgres tests for db/persist.py -- the upserts backing
pipeline.py's discover/enrich/qualify run. Previously verified only by
docs/08's manual run against a real Austin scrape; this covers the same
real bug that run found (a shared-domain multi-location chain) plus the
ordinary upsert/idempotency paths.
"""

from datetime import datetime, timezone

from sqlalchemy import select

from leadgen.db.models import Business, Contact, EnrichmentSignal, TargetRunBusiness
from leadgen.db.persist import (
    create_target_run,
    finish_target_run,
    record_run_business,
    upsert_business,
    upsert_contacts,
    upsert_signals,
)
from leadgen.discover.overpass import DiscoveredBusiness
from leadgen.enrich.signals import ContactCandidate


def test_upsert_business_creates_new_row(db_session) -> None:
    discovered = DiscoveredBusiness(
        name="Smile Dental", source_id="node/1", website_url="https://smile-dental.example.com"
    )
    row = upsert_business(db_session, discovered, "dentist")
    db_session.commit()

    assert row.id is not None
    assert row.normalized_domain == "smile-dental.example.com"
    assert row.business_type == "dentist"


def test_upsert_business_updates_existing_row_on_rerun(db_session) -> None:
    discovered = DiscoveredBusiness(name="Smile Dental", source_id="node/1", phone="512-555-0100")
    row1 = upsert_business(db_session, discovered, "dentist")
    db_session.commit()

    updated = DiscoveredBusiness(name="Smile Dental Clinic", source_id="node/1", phone="512-555-0199")
    row2 = upsert_business(db_session, updated, "dentist")
    db_session.commit()

    assert row1.id == row2.id
    assert row2.name == "Smile Dental Clinic"
    assert row2.phone == "512-555-0199"
    assert db_session.execute(select(Business)).scalars().all() == [row2]


def test_upsert_business_shared_domain_second_source_gets_null_normalized_domain(db_session) -> None:
    """docs/08's real bug: two OSM nodes (two physical branches of one
    chain) sharing a corporate domain used to crash the whole run on the
    partial unique index. The fix: whichever source_id doesn't already
    hold the domain gets normalized_domain=None instead, website_url
    untouched."""
    first = DiscoveredBusiness(
        name="River Rock Dental - North", source_id="node/1", website_url="https://riverrockdental.example.com"
    )
    second = DiscoveredBusiness(
        name="River Rock Dental - South", source_id="node/2", website_url="https://riverrockdental.example.com"
    )

    row1 = upsert_business(db_session, first, "dentist")
    db_session.flush()
    row2 = upsert_business(db_session, second, "dentist")
    db_session.commit()

    assert row1.normalized_domain == "riverrockdental.example.com"
    assert row2.normalized_domain is None
    assert row2.website_url == "https://riverrockdental.example.com"


def test_upsert_contacts_creates_and_updates_in_place(db_session) -> None:
    business = upsert_business(db_session, DiscoveredBusiness(name="B", source_id="node/1"), "dentist")
    db_session.flush()
    fetched_at = datetime.now(timezone.utc)

    upsert_contacts(db_session, business.id, [ContactCandidate(email="info@b.com", is_generic=True)], fetched_at)
    db_session.commit()

    rows = db_session.execute(select(Contact).where(Contact.business_id == business.id)).scalars().all()
    assert len(rows) == 1
    assert rows[0].is_generic is True

    later = datetime.now(timezone.utc)
    upsert_contacts(db_session, business.id, [ContactCandidate(email="info@b.com", is_generic=False)], later)
    db_session.commit()

    rows = db_session.execute(select(Contact).where(Contact.business_id == business.id)).scalars().all()
    assert len(rows) == 1  # updated in place, not duplicated
    assert rows[0].is_generic is False
    assert rows[0].legal_basis == "public_contact_published_on_business_website"


def test_upsert_signals_creates_and_updates_in_place(db_session) -> None:
    business = upsert_business(db_session, DiscoveredBusiness(name="B", source_id="node/1"), "dentist")
    db_session.flush()
    fetched_at = datetime.now(timezone.utc)

    upsert_signals(db_session, business.id, {"no_https": True}, fetched_at)
    db_session.commit()

    rows = db_session.execute(select(EnrichmentSignal).where(EnrichmentSignal.business_id == business.id)).scalars().all()
    assert len(rows) == 1
    assert rows[0].value == {"value": True}

    upsert_signals(db_session, business.id, {"no_https": False}, fetched_at)
    db_session.commit()

    rows = db_session.execute(select(EnrichmentSignal).where(EnrichmentSignal.business_id == business.id)).scalars().all()
    assert len(rows) == 1
    assert rows[0].value == {"value": False}


def test_record_run_business_upserts_the_join_row(db_session) -> None:
    business = upsert_business(db_session, DiscoveredBusiness(name="B", source_id="node/1"), "dentist")
    run = create_target_run(db_session, "t", "hash", "name: t\n")
    db_session.commit()

    record_run_business(db_session, run.id, business.id, qualified=True, crawl_status="ok", tags=["a"])
    db_session.commit()

    record_run_business(db_session, run.id, business.id, qualified=False, crawl_status="partial", tags=[])
    db_session.commit()

    rows = db_session.execute(
        select(TargetRunBusiness).where(TargetRunBusiness.target_run_id == run.id)
    ).scalars().all()
    assert len(rows) == 1  # one row per (run, business), updated in place
    assert rows[0].qualified is False
    assert rows[0].crawl_status == "partial"


def test_create_and_finish_target_run(db_session) -> None:
    run = create_target_run(db_session, "t", "hash", "name: t\n")
    db_session.commit()
    assert run.status == "running"
    assert run.finished_at is None

    finish_target_run(db_session, run.id, status="completed", businesses_found=3)
    db_session.commit()

    db_session.refresh(run)
    assert run.status == "completed"
    assert run.businesses_found == 3
    assert run.finished_at is not None


def test_finish_target_run_records_error_on_failure(db_session) -> None:
    run = create_target_run(db_session, "t", "hash", "name: t\n")
    db_session.commit()

    finish_target_run(db_session, run.id, status="failed", error="Overpass timed out")
    db_session.commit()

    db_session.refresh(run)
    assert run.status == "failed"
    assert run.error == "Overpass timed out"
