"""SQLAlchemy models for the leadgen data model (see PROJECT.md § Data model).

No query/repository logic here on purpose — this module only defines
structure. See the top-level schema-decisions discussion for why a few
columns and constraints differ from the literal PROJECT.md table.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class Business(Base):
    """One row per discovered company. Dedup key is normalized_domain when
    present, else (source, source_id) — see schema-decisions note on why
    these can't both be plain unique constraints."""

    __tablename__ = "businesses"

    id: Mapped[uuid.UUID] = _pk()

    name: Mapped[str]
    business_type: Mapped[str]

    normalized_domain: Mapped[str | None]
    website_url: Mapped[str | None]
    phone: Mapped[str | None]

    address: Mapped[str | None]
    city: Mapped[str | None]
    region: Mapped[str | None]
    country: Mapped[str | None]
    lat: Mapped[float | None] = mapped_column(Numeric(9, 6))
    lng: Mapped[float | None] = mapped_column(Numeric(9, 6))

    source: Mapped[str]
    source_id: Mapped[str]
    source_raw: Mapped[dict | None] = mapped_column(JSONB)
    # Only set for source='places' — Places content may not be persisted
    # beyond 30 days per Maps Platform ToS; only place_id is storable
    # long-term. A purge job (future work) queries against this.
    source_raw_expires_at: Mapped[datetime | None]

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_businesses_source_source_id"),
        CheckConstraint(
            "source IN ('overpass', 'places', 'csv')", name="ck_businesses_source"
        ),
        Index(
            "uq_businesses_normalized_domain",
            "normalized_domain",
            unique=True,
            postgresql_where=text("normalized_domain IS NOT NULL"),
        ),
    )


class Contact(Base):
    """Email addresses found for a business. Only ever populated from the
    business's own site or OSM tags — never guessed (see Hard rules)."""

    __tablename__ = "contacts"

    id: Mapped[uuid.UUID] = _pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE")
    )

    email: Mapped[str]
    is_generic: Mapped[bool] = mapped_column(default=False)

    # Provenance, required by Hard rules: "every contact row records
    # source, fetched_at, and legal basis."
    source: Mapped[str]
    fetched_at: Mapped[datetime]
    legal_basis: Mapped[str | None]

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        UniqueConstraint("business_id", "email", name="uq_contacts_business_email"),
        CheckConstraint("source IN ('website', 'osm')", name="ck_contacts_source"),
    )


class EnrichmentSignal(Base):
    """Key/value facts scraped from a business's site. One row per
    (business, key); re-crawling updates the value rather than appending
    history."""

    __tablename__ = "enrichment_signals"

    id: Mapped[uuid.UUID] = _pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE")
    )

    key: Mapped[str]
    value: Mapped[dict | None] = mapped_column(JSONB)
    fetched_at: Mapped[datetime]
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("business_id", "key", name="uq_signals_business_key"),
    )


class CrawlCache(Base):
    """Raw HTML per (business, url), so re-running discover/enrich during
    development doesn't re-crawl anything still fresh."""

    __tablename__ = "crawl_cache"

    id: Mapped[uuid.UUID] = _pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE")
    )

    url: Mapped[str]
    status_code: Mapped[int | None] = mapped_column(Integer)
    html: Mapped[str | None]
    fetched_at: Mapped[datetime]

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        UniqueConstraint("business_id", "url", name="uq_crawl_cache_business_url"),
    )


class TargetRun(Base):
    """One row per target-profile execution. Stores the full resolved
    profile YAML (not just a hash) so a run is actually reproducible even
    if business_types.yaml or an offer file drifts later."""

    __tablename__ = "target_runs"

    id: Mapped[uuid.UUID] = _pk()

    target_name: Mapped[str]
    profile_hash: Mapped[str]
    profile_yaml: Mapped[str]

    status: Mapped[str] = mapped_column(default="running")
    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    finished_at: Mapped[datetime | None]
    businesses_found: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None]

    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name="ck_target_runs_status",
        ),
    )


class Campaign(Base):
    """A target run's leads, paired with an offer and a sender pool."""

    __tablename__ = "campaigns"

    id: Mapped[uuid.UUID] = _pk()
    target_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("target_runs.id", ondelete="RESTRICT")
    )

    # References config/offers/<offer_id>.yaml — offers live in files, not
    # the DB, so intentionally no FK here.
    offer_id: Mapped[str]
    name: Mapped[str | None]

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class CampaignMailbox(Base):
    """Join table for a campaign's sender_pool (many-to-many)."""

    __tablename__ = "campaign_mailboxes"

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), primary_key=True
    )
    mailbox_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("mailboxes.id", ondelete="RESTRICT"), primary_key=True
    )


class Mailbox(Base):
    """A sending identity. Daily send counts are derived from
    messages.sent_at at query time — not stored here — so there is no
    counter that can drift out of sync with what was actually sent."""

    __tablename__ = "mailboxes"

    id: Mapped[uuid.UUID] = _pk()

    name: Mapped[str] = mapped_column(unique=True)
    email_address: Mapped[str] = mapped_column(unique=True)
    oauth_refresh_token_encrypted: Mapped[str]
    daily_cap: Mapped[int] = mapped_column(Integer, default=50)
    is_active: Mapped[bool] = mapped_column(default=True)

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )


class Message(Base):
    """One queued/sent email per (campaign, contact). Rendered subject/body
    are stored so an approved message can't drift if a template changes
    later; Gmail message/thread ids are stored for stage 7 (bounce/reply
    monitoring) to correlate back to this row."""

    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = _pk()
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaigns.id", ondelete="RESTRICT")
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("contacts.id", ondelete="RESTRICT")
    )
    mailbox_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("mailboxes.id", ondelete="RESTRICT")
    )

    status: Mapped[str] = mapped_column(default="queued")
    subject: Mapped[str | None]
    body: Mapped[str | None]

    approved_by: Mapped[str | None]
    approved_at: Mapped[datetime | None]
    sent_at: Mapped[datetime | None]
    bounced_at: Mapped[datetime | None]
    replied_at: Mapped[datetime | None]

    gmail_message_id: Mapped[str | None]
    gmail_thread_id: Mapped[str | None]

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        UniqueConstraint("campaign_id", "contact_id", name="uq_messages_campaign_contact"),
        CheckConstraint(
            "status IN ('queued', 'approved', 'sent', 'bounced', 'replied', 'rejected')",
            name="ck_messages_status",
        ),
    )


class Suppression(Base):
    """Permanent, global send-blockers. Checked before every send.
    scope='email' or scope='domain'; value is the lowercased email or
    normalized domain."""

    __tablename__ = "suppressions"

    id: Mapped[uuid.UUID] = _pk()

    scope: Mapped[str]
    value: Mapped[str]
    reason: Mapped[str]

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        UniqueConstraint("scope", "value", name="uq_suppressions_scope_value"),
        CheckConstraint("scope IN ('email', 'domain')", name="ck_suppressions_scope"),
    )
