"""Turns a `target_run`'s qualified leads into a `campaigns` row plus one
`queued` `messages` row per lead -- the piece CLAUDE.md's "next concrete
step" describes. Deliberately not covered by the mocked/pure test suite,
like `db/persist.py`/`db/repository.py` -- `Message`/`Campaign` use the
same Postgres-specific JSONB/UUID columns SQLite can't stand in for. See
docs/11 for the real verification run.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from leadgen.compose.render import ComposeError, render_message
from leadgen.config.models import Offer
from leadgen.db.models import Business, Campaign, CampaignMailbox, Contact, EnrichmentSignal, Mailbox, Message, TargetRunBusiness

__all__ = ["CampaignError", "create_campaign", "generate_campaign_messages"]


class CampaignError(Exception):
    """Raised for anything that would leave a campaign half-built --
    an unknown mailbox name, a target_run with no qualified leads --
    rather than silently creating an empty or partial campaign."""


def create_campaign(
    session: Session,
    *,
    target_run_id,
    offer_id: str,
    sender_pool: list[str],
    name: str | None = None,
) -> Campaign:
    """Creates the `campaigns` row and its `campaign_mailboxes` join rows.
    `sender_pool` names must already exist as `mailboxes` rows (created by
    `scripts/authorize_mailbox.py`, HOWTO.md) -- a campaign can't invent a
    sending identity it doesn't have real OAuth credentials for."""
    if not sender_pool:
        raise CampaignError("sender_pool must have at least one mailbox name")

    mailboxes = session.execute(select(Mailbox).where(Mailbox.name.in_(sender_pool))).scalars().all()
    found_names = {m.name for m in mailboxes}
    missing = [n for n in sender_pool if n not in found_names]
    if missing:
        raise CampaignError(
            f"unknown mailbox name(s) {missing} -- run scripts/authorize_mailbox.py first"
        )

    campaign = Campaign(target_run_id=target_run_id, offer_id=offer_id, name=name)
    session.add(campaign)
    session.flush()
    for mailbox in mailboxes:
        session.add(CampaignMailbox(campaign_id=campaign.id, mailbox_id=mailbox.id))
    return campaign


def _select_best_contact(contacts: list[Contact]) -> Contact | None:
    """One message per business, not one per contact -- a business with
    nine published mailboxes (a real case, docs/09's BLVD Dentistry) gets
    one outreach message, not nine. Prefers a named address over a
    generic one (info@, contact@, ...) when both exist."""
    named = [c for c in contacts if not c.is_generic]
    if named:
        return named[0]
    return contacts[0] if contacts else None


def _flatten_signals(session: Session, business_id) -> dict:
    """`enrichment_signals.value` is stored as `{"value": ...}` (see
    `db/persist.py`'s docstring on why) -- unwrap it back to the plain
    `{key: value}` shape `render_message()`/qualify.py expect."""
    rows = session.execute(
        select(EnrichmentSignal).where(EnrichmentSignal.business_id == business_id)
    ).scalars()
    return {row.key: (row.value or {}).get("value") for row in rows}


def generate_campaign_messages(
    session: Session,
    campaign: Campaign,
    *,
    offer: Offer,
    templates_root: Path = Path("."),
) -> tuple[list[Message], list[str]]:
    """Renders and queues one Message per qualified business in the
    campaign's target_run. Idempotent: re-running against the same
    campaign skips businesses that already have a message in it (the
    `(campaign_id, contact_id)` unique constraint would reject a
    duplicate anyway; this checks first so a partial re-run doesn't spam
    warnings for leads it already handled).

    Returns (created_messages, skip_reasons) -- one bad lead (missing
    contact, or a signal set that no longer justifies the offer's
    `relevant_signals`) is skipped and reported, not allowed to abort
    the whole campaign."""
    qualified = session.execute(
        select(TargetRunBusiness, Business)
        .join(Business, TargetRunBusiness.business_id == Business.id)
        .where(
            TargetRunBusiness.target_run_id == campaign.target_run_id,
            TargetRunBusiness.qualified.is_(True),
        )
        .order_by(Business.name)
    ).all()

    if not qualified:
        raise CampaignError("this target_run has no qualified businesses to message")

    campaign_mailboxes = (
        session.execute(
            select(CampaignMailbox.mailbox_id).where(CampaignMailbox.campaign_id == campaign.id)
        )
        .scalars()
        .all()
    )

    created: list[Message] = []
    skipped: list[str] = []

    for index, (run_business, business) in enumerate(qualified):
        contacts = session.execute(
            select(Contact).where(Contact.business_id == business.id)
        ).scalars().all()
        contact = _select_best_contact(contacts)
        if contact is None:
            skipped.append(f"{business.name}: no contact email on file")
            continue

        existing = session.execute(
            select(Message).where(Message.campaign_id == campaign.id, Message.contact_id == contact.id)
        ).scalar_one_or_none()
        if existing is not None:
            continue

        signals = _flatten_signals(session, business.id)
        try:
            subject, body = render_message(
                business_name=business.name,
                offer=offer,
                signals=signals,
                templates_root=templates_root,
            )
        except ComposeError as exc:
            skipped.append(f"{business.name}: {exc}")
            continue

        mailbox_id = campaign_mailboxes[index % len(campaign_mailboxes)] if campaign_mailboxes else None
        message = Message(
            campaign_id=campaign.id,
            contact_id=contact.id,
            mailbox_id=mailbox_id,
            status="queued",
            subject=subject,
            body=body,
        )
        session.add(message)
        created.append(message)

    return created, skipped
