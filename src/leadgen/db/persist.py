"""Upserts backing pipeline.py's discover/enrich/qualify run against
Postgres. Deliberately not covered by the mocked/pure test suite — like
db/repository.py, these need a real Postgres (JSONB/UUID columns aren't
SQLite-compatible) to exercise meaningfully. See docs/08 for the real
verification run this went through.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from leadgen.db.models import Business, Contact, EnrichmentSignal, TargetRun
from leadgen.discover.overpass import DiscoveredBusiness
from leadgen.enrich.signals import ContactCandidate
from leadgen.util.domains import normalise_domain

__all__ = [
    "CONTACT_LEGAL_BASIS_PLACEHOLDER",
    "create_target_run",
    "finish_target_run",
    "upsert_business",
    "upsert_contacts",
    "upsert_signals",
]

# PROJECT.md's legal posture section requires every contacts row to carry
# a legal_basis, but doesn't specify a fixed value for "email found on a
# business's own public website" and PROJECT.md is explicit that this
# isn't legal advice. This records the actual fact (where the address was
# published) rather than asserting a legal conclusion ("legitimate
# interest applies") on nobody's authority -- treat it as a placeholder
# pending real legal review, not a settled determination.
CONTACT_LEGAL_BASIS_PLACEHOLDER = "public_contact_published_on_business_website"


def upsert_business(session: Session, business: DiscoveredBusiness, business_type: str) -> Business:
    """Dedup key is (source, source_id) -- the per-source identity
    Overpass/Places/CSV each already guarantee uniqueness for. Matching
    across sources on normalized_domain is explicitly not attempted here
    (CLAUDE.md's schema-decisions note: no automatic cross-source merge).

    `businesses.normalized_domain` also carries a *partial unique index*
    (same CLAUDE.md note), which real data violates in a way that note
    didn't anticipate: a multi-location chain (e.g. two "River Rock
    Dental" branches, real example from a live Austin scrape) is two
    distinct OSM nodes with two distinct source_ids sharing one corporate
    domain. Rather than let the second one crash the whole run, whichever
    source_id doesn't already hold that domain gets stored with
    normalized_domain=None instead -- website_url is untouched, only the
    dedup/uniqueness slot is skipped. Which of the two "wins" the slot on
    a given run depends on discover() row order, which isn't guaranteed
    stable -- an accepted limitation, not a crash, per the same note's
    framing of cross-business dedup as an application-level problem."""
    normalized_domain = None
    if business.website_url:
        try:
            normalized_domain = normalise_domain(business.website_url)
        except ValueError:
            normalized_domain = None

    if normalized_domain is not None:
        domain_claimed_elsewhere = session.execute(
            select(Business.id).where(
                Business.normalized_domain == normalized_domain,
                Business.source_id != business.source_id,
            )
        ).first()
        if domain_claimed_elsewhere is not None:
            normalized_domain = None

    row = session.execute(
        select(Business).where(
            Business.source == business.source, Business.source_id == business.source_id
        )
    ).scalar_one_or_none()

    if row is None:
        row = Business(source=business.source, source_id=business.source_id)
        session.add(row)

    row.name = business.name
    row.business_type = business_type
    row.normalized_domain = normalized_domain
    row.website_url = business.website_url
    row.phone = business.phone
    row.address = business.address
    row.lat = business.lat
    row.lng = business.lng
    row.source_raw = business.source_raw

    session.flush()
    return row


def upsert_contacts(
    session: Session,
    business_id,
    contacts: list[ContactCandidate],
    fetched_at: datetime,
) -> None:
    for contact in contacts:
        row = session.execute(
            select(Contact).where(Contact.business_id == business_id, Contact.email == contact.email)
        ).scalar_one_or_none()
        if row is None:
            session.add(
                Contact(
                    business_id=business_id,
                    email=contact.email,
                    is_generic=contact.is_generic,
                    source="website",
                    fetched_at=fetched_at,
                    legal_basis=CONTACT_LEGAL_BASIS_PLACEHOLDER,
                )
            )
        else:
            row.is_generic = contact.is_generic
            row.fetched_at = fetched_at


def upsert_signals(
    session: Session,
    business_id,
    signals: dict[str, object],
    fetched_at: datetime,
) -> None:
    """One row per (business, key); re-crawling updates the value in
    place rather than appending history, per EnrichmentSignal's own
    docstring. Values are wrapped ({"value": ...}) because the column is
    typed Mapped[dict | None] -- a raw bool/int/str would work at the
    Postgres JSONB level but not match the declared Python type."""
    for key, value in signals.items():
        row = session.execute(
            select(EnrichmentSignal).where(
                EnrichmentSignal.business_id == business_id, EnrichmentSignal.key == key
            )
        ).scalar_one_or_none()
        if row is None:
            session.add(
                EnrichmentSignal(
                    business_id=business_id,
                    key=key,
                    value={"value": value},
                    fetched_at=fetched_at,
                )
            )
        else:
            row.value = {"value": value}
            row.fetched_at = fetched_at


def create_target_run(session: Session, target_name: str, profile_hash: str, profile_yaml: str) -> TargetRun:
    run = TargetRun(target_name=target_name, profile_hash=profile_hash, profile_yaml=profile_yaml)
    session.add(run)
    session.flush()
    return run


def finish_target_run(
    session: Session,
    run_id,
    *,
    status: str,
    businesses_found: int | None = None,
    error: str | None = None,
) -> None:
    run = session.get(TargetRun, run_id)
    run.status = status
    run.finished_at = datetime.now(timezone.utc)
    run.businesses_found = businesses_found
    run.error = error
