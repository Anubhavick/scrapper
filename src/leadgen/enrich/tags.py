"""Auto-generated tags: a human-scannable summary of a business's signals
and basic state, so the CSV can be filtered/sorted in a spreadsheet (e.g.
every `no-website` row, or every `no-booking` + `no-contact-form` row)
without opening the raw `signals` JSON column.

Purely derived from data already crawled -- no new API, no manual input.
Deliberately descriptive, not diagnostic: this only restates the boolean
signals qualify.py already treats as unambiguous, plus the raw value of
the non-boolean ones. It does not decide whether a value is "bad" (e.g.
how old is too old for last_content_year) -- see qualify.py's module
docstring for why that line hasn't been drawn.
"""

from __future__ import annotations

__all__ = ["compute_tags"]

BOOLEAN_SIGNAL_TAGS: dict[str, str] = {
    "no_https": "no-https",
    "no_mobile_viewport": "no-mobile",
    "no_contact_form": "no-contact-form",
    "no_online_booking": "no-booking",
    "has_whatsapp_link": "has-whatsapp",
}


def compute_tags(*, has_website: bool, signals: dict[str, object]) -> list[str]:
    tags: list[str] = []
    if not has_website:
        tags.append("no-website")

    for key, tag in BOOLEAN_SIGNAL_TAGS.items():
        if signals.get(key):
            tags.append(tag)

    platform = signals.get("site_platform")
    if platform:
        tags.append(f"platform-{str(platform).strip().lower().replace(' ', '-')}")

    content_year = signals.get("last_content_year")
    if content_year:
        tags.append(f"content-year-{content_year}")

    page_weight = signals.get("page_weight_mb")
    if page_weight:
        tags.append(f"page-weight-{page_weight}mb")

    return tags
