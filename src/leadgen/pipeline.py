"""Wires discover -> filter -> crawl -> qualify -> CSV export together
for one target profile.

Build order step 5. Nothing here writes to the database — a lead list
produced by this module is a CSV to read by hand, not yet a persisted
`businesses`/`contacts`/`enrichment_signals` row. That persistence
layer doesn't exist yet; see docs/05 for why this step deliberately
doesn't build it.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

import httpx

from leadgen.config.models import BusinessTypeDef, TargetProfile
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
) -> list[LeadRow]:
    """Discover businesses for `profile`, drop the ones its `filters`
    exclude, crawl the rest, and return one LeadRow per surviving
    business (crawled or not, if it had no website to crawl)."""
    discovered = discover(profile, business_type, overpass_client, user_agent, geocoder)
    filtered = apply_filters(discovered, profile.filters)

    robots = RobotsChecker(crawl_client, user_agent)
    rows: list[LeadRow] = []

    for business in filtered:
        if not business.website_url:
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

    return rows


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
