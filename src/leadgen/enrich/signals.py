"""Contact-email extraction and enrichment-signal computation from
already-fetched HTML.

No email guessing (PROJECT.md hard rule): every email returned here was
literally present in the page's visible text or a mailto: link — never
constructed from a name/domain pattern.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from selectolax.parser import HTMLParser

if TYPE_CHECKING:
    from leadgen.enrich.crawler import PageFetch

_EMAIL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._%+-]*@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# Local parts that indicate a role address rather than a named
# individual — relevant to the DPDP guidance in PROJECT.md's legal
# posture table (prefer generic role addresses for India).
_GENERIC_LOCAL_PARTS = frozenset(
    {
        "info",
        "contact",
        "hello",
        "admin",
        "sales",
        "support",
        "office",
        "enquiries",
        "enquiry",
        "mail",
        "contactus",
        "bookings",
        "reception",
    }
)

# Domains that show up constantly in template/boilerplate markup and
# are never a real business's contact address.
_PLACEHOLDER_DOMAINS = frozenset(
    {
        "example.com",
        "example.org",
        "example.net",
        "test.com",
        "yourdomain.com",
        "domain.com",
        "sentry.io",
        "wixpress.com",
        "godaddy.com",
    }
)

_BOOKING_KEYWORDS = (
    "book now",
    "book an appointment",
    "book online",
    "schedule an appointment",
    "calendly.com",
    "book appointment",
)

_PLATFORM_SIGNATURES = {
    "wordpress": ("wp-content", "wp-includes"),
    "wix": ("wix.com", "wixstatic.com"),
    "squarespace": ("squarespace.com", "static1.squarespace.com"),
    "shopify": ("cdn.shopify.com", "myshopify.com"),
    "webflow": ("webflow.com", "assets-global.website-files.com"),
}


@dataclass(frozen=True)
class ContactCandidate:
    email: str
    is_generic: bool


def extract_emails(html: str) -> list[ContactCandidate]:
    tree = HTMLParser(html)
    found: set[str] = set()

    for match in _EMAIL_RE.finditer(tree.text(separator=" ")):
        found.add(match.group(0).lower())

    for anchor in tree.css("a[href^='mailto:']"):
        href = anchor.attributes.get("href") or ""
        address = href[len("mailto:") :].split("?")[0].strip()
        if address:
            found.add(address.lower())

    candidates = []
    for email in sorted(found):
        if "@" not in email:
            continue
        local_part, _, domain = email.partition("@")
        if domain in _PLACEHOLDER_DOMAINS:
            continue
        candidates.append(
            ContactCandidate(
                email=email, is_generic=local_part in _GENERIC_LOCAL_PARTS
            )
        )
    return candidates


def _has_viewport_meta(tree: HTMLParser) -> bool:
    return any(
        "width=device-width" in (meta.attributes.get("content") or "")
        for meta in tree.css('meta[name="viewport"]')
    )


def _detect_platform(html_lower: str) -> str | None:
    for platform, signatures in _PLATFORM_SIGNATURES.items():
        if any(sig in html_lower for sig in signatures):
            return platform
    return None


def _has_booking_mention(text_lower: str, html_lower: str) -> bool:
    return any(kw in text_lower or kw in html_lower for kw in _BOOKING_KEYWORDS)


def _has_whatsapp_link(html_lower: str) -> bool:
    return "wa.me/" in html_lower or "api.whatsapp.com/send" in html_lower


_YEAR_RE = r"(?:19\d{2}|20\d{2})"

# A year mentioned right after a copyright marker (`©`, `(c)`, "copyright")
# is almost always auto-generated to today's year by a template and says
# nothing about when the page's actual content was last touched -- see
# docs/07/docs/15. Matches an optional trailing range (`© 2015-2026`) too,
# since both ends of a copyright range are equally uninformative.
_COPYRIGHT_YEAR_RE = re.compile(
    rf"(?:©|\(c\)|copyright)[^0-9]{{0,20}}{_YEAR_RE}(?:\s*[-–—]\s*{_YEAR_RE})?",
    re.IGNORECASE,
)


def _extract_years(text: str) -> list[int]:
    content_text = _COPYRIGHT_YEAR_RE.sub(" ", text)
    return [int(year) for year in re.findall(rf"\b({_YEAR_RE})\b", content_text)]


def compute_signals(pages: list[PageFetch]) -> dict[str, object]:
    """Aggregate signals across every page actually fetched.
    no_https/no_mobile_viewport are read from the homepage (pages[0])
    specifically; everything else is true if ANY crawled page shows it
    (a form on /contact still means "has a contact form", even if the
    homepage doesn't)."""
    if not pages:
        return {}

    homepage = pages[0]
    homepage_tree = HTMLParser(homepage.html)

    any_form = False
    any_booking = False
    any_whatsapp = False
    platform: str | None = None
    years: list[int] = []
    total_bytes = 0

    for page in pages:
        tree = HTMLParser(page.html)
        html_lower = page.html.lower()
        text_lower = tree.text(separator=" ").lower()

        any_form = any_form or bool(tree.css("form"))
        any_booking = any_booking or _has_booking_mention(text_lower, html_lower)
        any_whatsapp = any_whatsapp or _has_whatsapp_link(html_lower)
        if platform is None:
            platform = _detect_platform(html_lower)
        years.extend(_extract_years(text_lower))
        total_bytes += page.content_length_bytes

    return {
        "no_https": not homepage.url.startswith("https://"),
        "no_mobile_viewport": not _has_viewport_meta(homepage_tree),
        "no_contact_form": not any_form,
        "no_online_booking": not any_booking,
        "site_platform": platform,
        "last_content_year": max(years) if years else None,
        "page_weight_mb": round(total_bytes / (1024 * 1024), 3),
        "has_whatsapp_link": any_whatsapp,
    }
