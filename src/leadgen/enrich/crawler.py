"""Fetch a business's own pages (never a third party) at a bounded
rate, cap total pages per the profile's enrichment config, and hand the
results to signals.py for extraction.
"""

from __future__ import annotations

import socket
import ssl
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import urljoin

import httpx

from leadgen.enrich.http_defaults import CRAWL_REQUEST_TIMEOUT
from leadgen.enrich.robots import RobotsChecker
from leadgen.enrich.signals import ContactCandidate, compute_signals, extract_emails

MIN_REQUEST_INTERVAL_SECONDS = 1.0

# Total wall-clock budget for crawling one business's site. Bounds the
# pathological case a per-request timeout alone doesn't catch: a host
# that responds *just* fast enough on every request to never trip
# CRAWL_REQUEST_TIMEOUT, but still burns minutes across `max_pages`
# requests. One slow host must not stall the rest of a pipeline run.
DEFAULT_HOST_DEADLINE_SECONDS = 45.0


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
    errors: list[str] = field(default_factory=list)


def _classify_request_error(exc: httpx.RequestError) -> str:
    """Coarse tag for a failed request, so 'DNS doesn't resolve' and
    'this host is a black hole' are distinguishable downstream (CSV tags,
    ops triage) without reading a stack trace. httpx doesn't expose a
    dedicated DNS/SSL exception type — both surface as ConnectError with
    the underlying stdlib error attached as __cause__ — so we inspect
    that instead of guessing from the exception's class alone.
    """
    if isinstance(
        exc, (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout)
    ):
        return "error-timeout"

    cause = exc.__cause__
    if isinstance(cause, ssl.SSLError):
        return "error-ssl"
    if isinstance(cause, socket.gaierror):
        return "error-dns"
    if isinstance(exc, (httpx.ConnectError, httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError)):
        return "error-connection"
    return "error-network"


def fetch_page(
    url: str,
    client: httpx.Client,
    user_agent: str,
    *,
    timeout: httpx.Timeout | float = CRAWL_REQUEST_TIMEOUT,
    on_error: Callable[[str], None] | None = None,
) -> PageFetch | None:
    """Fetch one page. Returns None on any failure (network error, 4xx/5xx)
    — a single broken page shouldn't abort the whole crawl. When given,
    `on_error` receives a coarse failure tag (see `_classify_request_error`)
    so a caller can surface *why*, without this function ever raising for
    a reachability problem."""
    try:
        response = client.get(
            url,
            headers={"User-Agent": user_agent},
            follow_redirects=True,
            timeout=timeout,
        )
    except httpx.RequestError as exc:
        if on_error is not None:
            on_error(_classify_request_error(exc))
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
    host_deadline_seconds: float = DEFAULT_HOST_DEADLINE_SECONDS,
) -> CrawlResult:
    """Crawl up to max_pages of one business's own site, at 1 request/
    second, skipping anything robots.txt disallows for our User-Agent.
    Stops early (without raising) once `host_deadline_seconds` of
    wall-clock time has passed, so one slow business can cost this
    pipeline run at most that much time."""
    pages: list[PageFetch] = []
    errors: list[str] = []
    last_request_at: float | None = None
    deadline = time.monotonic() + host_deadline_seconds

    for path in crawl_pages[:max_pages]:
        now = time.monotonic()
        if now >= deadline:
            errors.append("error-timeout")
            break

        url = urljoin(website_url, path)

        if not robots.can_fetch(url):
            continue

        if last_request_at is not None:
            remaining = min_request_interval - (now - last_request_at)
            if remaining > 0:
                time.sleep(remaining)

        page = fetch_page(url, client, user_agent, on_error=errors.append)
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
        errors=errors,
    )
