"""Apply a target profile's discovery-time filters to a batch of
DiscoveredBusiness rows, before any of them get crawled.

Filtering here rather than after enrichment is what keeps the crawl
budget (1 req/sec, capped pages per business) from being spent on
businesses the profile explicitly doesn't want.
"""

from __future__ import annotations

import re

from leadgen.config.models import Filters
from leadgen.discover.overpass import DiscoveredBusiness
from leadgen.util.domains import normalise_domain


def apply_filters(
    businesses: list[DiscoveredBusiness], filters: Filters
) -> list[DiscoveredBusiness]:
    excluded_domains = {normalise_domain(d) for d in filters.exclude_domains}
    name_patterns = [re.compile(p) for p in filters.exclude_name_patterns]

    return [
        business
        for business in businesses
        if _passes(business, filters, excluded_domains, name_patterns)
    ]


def _passes(
    business: DiscoveredBusiness,
    filters: Filters,
    excluded_domains: set[str],
    name_patterns: list[re.Pattern],
) -> bool:
    if filters.must_have_website and not business.website_url:
        return False
    if filters.must_have_phone and not business.phone:
        return False
    if len(business.name.strip()) < filters.min_name_length:
        return False

    if business.website_url:
        try:
            domain = normalise_domain(business.website_url)
        except ValueError:
            domain = None
        if domain in excluded_domains:
            return False

    if any(pattern.search(business.name) for pattern in name_patterns):
        return False

    return True
