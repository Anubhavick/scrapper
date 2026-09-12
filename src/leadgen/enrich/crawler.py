"""Fetch a business's own pages (never a third party) at a bounded
rate, cap total pages per the profile's enrichment config, and hand the
results to signals.py for extraction.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx

from leadgen.enrich.robots import RobotsChecker
from leadgen.enrich.signals import ContactCandidate, compute_signals, extract_emails

MIN_REQUEST_INTERVAL_SECONDS = 1.0


@dataclass(frozen=True)
class PageFetch:
    url: str
    status_code: int
    html: str
    content_length_bytes: int
    fetched_at: datetime


@dataclass(frozen=True)
class CrawlResult:
    pages: list[PageFetch]
    contacts: list[ContactCandidate]
    signals: dict[str, object]


def fetch_page(url: str, client: httpx.Client, user_agent: str) -> PageFetch | None:
    """Fetch one page. Returns None on any failure (network error, 4xx/5xx)
    — a single broken page shouldn't abort the whole crawl."""
    try:
        response = client.get(
            url, headers={"User-Agent": user_agent}, follow_redirects=True
        )
    except httpx.HTTPError:
        return None
    if response.status_code >= 400:
        return None
    return PageFetch(
        url=str(response.url),
        status_code=response.status_code,
        html=response.text,
        content_length_bytes=len(response.content),
        fetched_at=datetime.now(timezone.utc),
    )


def crawl_business(
    website_url: str,
    crawl_pages: list[str],
    max_pages: int,
    client: httpx.Client,
    robots: RobotsChecker,
    user_agent: str,
    min_request_interval: float = MIN_REQUEST_INTERVAL_SECONDS,
) -> CrawlResult:
    """Crawl up to max_pages of one business's own site, at 1 request/
    second, skipping anything robots.txt disallows for our User-Agent."""
    pages: list[PageFetch] = []
    last_request_at: float | None = None

    for path in crawl_pages[:max_pages]:
        url = urljoin(website_url, path)

        if not robots.can_fetch(url):
            continue

        if last_request_at is not None:
            remaining = min_request_interval - (time.monotonic() - last_request_at)
            if remaining > 0:
                time.sleep(remaining)

        page = fetch_page(url, client, user_agent)
        last_request_at = time.monotonic()
        if page is not None:
            pages.append(page)

    emails: dict[str, ContactCandidate] = {}
    for page in pages:
        for candidate in extract_emails(page.html):
            emails.setdefault(candidate.email, candidate)

    return CrawlResult(
        pages=pages,
        contacts=sorted(emails.values(), key=lambda c: c.email),
        signals=compute_signals(pages),
    )
