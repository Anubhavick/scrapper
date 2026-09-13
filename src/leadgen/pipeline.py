"""Wires discover -> filter -> crawl -> qualify -> CSV export together
for one target profile.

Build order step 5 added the CSV half. Persistence (a `session` passed
in) was deferred until docs/08 -- see that doc for why now: both the
lead-review UI's "history" and the campaign/orchestration work need real
`businesses`/`contacts`/`enrichment_signals`/`target_runs` rows, not a
CSV a human reads once and never queries again.
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from leadgen.config.models import BusinessTypeDef, TargetProfile
from leadgen.db.persist import (
    create_target_run,
    finish_target_run,
    upsert_business,
    upsert_contacts,
    upsert_signals,
)
from leadgen.discover.filters import apply_filters
from leadgen.discover.geocode import NominatimClient
from leadgen.discover.overpass import DiscoveredBusiness, discover
from leadgen.enrich.crawler import crawl_business
from leadgen.enrich.qualify import is_qualified
from leadgen.enrich.robots import RobotsChecker
from leadgen.enrich.signals import ContactCandidate
from leadgen.enrich.tags import compute_tags

CSV_FIELDS = [
    "name",
    "website_url",
    "phone",
    "address",
    "emails",
    "qualified",
    "crawl_status",
    "tags",
    "signals",
]


@dataclass(frozen=True)
class LeadRow:
    name: str
    website_url: str | None
    phone: str | None
    address: str | None
    emails: str
    qualified: bool
    crawl_status: str
    tags: str
    signals: str

    def as_csv_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "website_url": self.website_url or "",
            "phone": self.phone or "",
            "address": self.address or "",
            "emails": self.emails,
            "qualified": "yes" if self.qualified else "no",
            "crawl_status": self.crawl_status,
            "tags": self.tags,
            "signals": self.signals,
        }


def run_target_profile(
    profile: TargetProfile,
    business_type: BusinessTypeDef,
    overpass_client: httpx.Client,
    crawl_client: httpx.Client,
    user_agent: str,
    geocoder: NominatimClient | None = None,
    session: Session | None = None,
    target_name: str | None = None,
    profile_yaml_text: str | None = None,
) -> list[LeadRow]:
    """Discover businesses for `profile`, drop the ones its `filters`
    exclude, crawl the rest, and return one LeadRow per surviving
    business (crawled or not, if it had no website to crawl).

    Pass `session` (plus `target_name` and the profile file's raw
    `profile_yaml_text`) to also persist a `target_runs` row and upsert
    each business/contact/signal to Postgres as the run progresses.
    Without a session, this behaves exactly as before -- in-memory rows,
    CSV export only, no database dependency at all."""
    if session is not None and (target_name is None or profile_yaml_text is None):
        raise ValueError("target_name and profile_yaml_text are required when session is given")

    target_run = None
    if session is not None:
        profile_hash = hashlib.sha256(profile_yaml_text.encode("utf-8")).hexdigest()
        target_run = create_target_run(session, target_name, profile_hash, profile_yaml_text)

    try:
        discovered = discover(profile, business_type, overpass_client, user_agent, geocoder)
        filtered = apply_filters(discovered, profile.filters)

        robots = RobotsChecker(crawl_client, user_agent)
        rows: list[LeadRow] = []
        fetched_at = datetime.now(timezone.utc)

        for business in filtered:
            if not business.website_url:
                if session is not None:
                    upsert_business(session, business, business_type=profile.business_type)
                rows.append(_lead_row(business, contacts=[], signals={}, profile=profile))
                continue

            result = crawl_business(
                website_url=business.website_url,
                crawl_pages=profile.enrichment.crawl_pages,
                max_pages=profile.enrichment.max_pages,
                client=crawl_client,
                robots=robots,
                user_agent=user_agent,
            )
            if session is not None:
                db_business = upsert_business(session, business, business_type=profile.business_type)
                upsert_contacts(session, db_business.id, result.contacts, fetched_at)
                upsert_signals(session, db_business.id, result.signals, fetched_at)
            rows.append(
                _lead_row(
                    business,
                    result.contacts,
                    result.signals,
                    profile,
                    errors=result.errors,
                    pages_fetched=len(result.pages),
                )
            )

        if target_run is not None:
            finish_target_run(session, target_run.id, status="completed", businesses_found=len(rows))
        return rows
    except Exception as exc:
        if target_run is not None:
            finish_target_run(session, target_run.id, status="failed", error=str(exc))
        raise


def _crawl_status(has_website: bool, errors: list[str], pages_fetched: int) -> str:
    """One word a human filtering the CSV can act on directly, instead of
    parsing the tags string for 'error-' substrings. 'unreachable' means
    every crawl attempt failed, so `signals`/`tags` for that row reflect
    nothing about the real site — qualification correctly can't use them,
    and a reviewer shouldn't read absence-of-signals as a good sign."""
    if not has_website:
        return "no_website"
    if errors and pages_fetched == 0:
        return "unreachable"
    if errors:
        return "partial"
    return "ok"


def _lead_row(
    business: DiscoveredBusiness,
    contacts: list[ContactCandidate],
    signals: dict[str, object],
    profile: TargetProfile,
    errors: list[str] | None = None,
    pages_fetched: int = 0,
) -> LeadRow:
    errors = errors or []
    tags = list(
        dict.fromkeys(compute_tags(has_website=business.website_url is not None, signals=signals) + errors)
    )
    return LeadRow(
        name=business.name,
        website_url=business.website_url,
        phone=business.phone,
        address=business.address,
        emails=";".join(c.email for c in contacts),
        qualified=is_qualified(profile, contacts, signals),
        crawl_status=_crawl_status(business.website_url is not None, errors, pages_fetched),
        tags=";".join(tags),
        signals=json.dumps(signals, sort_keys=True),
    )


def export_csv(rows: list[LeadRow], path: Path) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.as_csv_dict())
