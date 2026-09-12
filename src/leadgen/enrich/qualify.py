"""Decide whether a crawled business is a sendable lead, per the
profile's `qualification` rules.

Boolean signals (no_https, no_mobile_viewport, no_contact_form,
no_online_booking, has_whatsapp_link) count toward
`require_any_signal`/`min_signal_count` exactly when True — unambiguous.

For the two numeric signals (`last_content_year`, `page_weight_mb`),
PROJECT.md's own example profile lists `last_content_year` in
`require_any_signal` without ever defining what value makes an old site
"count": is 2022 stale? 2018? There was no threshold anywhere in the
spec, and inventing one directly in this function (e.g. "stale if more
than 2 years old") would have silently decided which real businesses get
emailed for every profile at once — a product call, not a plumbing
detail, and not one to make unilaterally.

Resolved per docs/05's own suggested fix: the threshold is now a
per-profile config decision (`qualification.stale_content_before_year`,
`qualification.max_page_weight_mb`), not a constant in this file. A
profile that doesn't set either keeps the exact old behaviour — a
non-None value counts as "matched" purely because the crawler found
*some* value, not because it indicates an actual problem — so no
existing profile's qualification set changes unless someone deliberately
opts in. Whoever writes the target profile YAML for a given business
type/market is the one who should say what "stale" or "too heavy" means
for it; still revisit against the "read 200 rows by hand" step (5)
before leaning on this for a live campaign.
"""

from __future__ import annotations

from leadgen.config.models import Qualification, TargetProfile
from leadgen.enrich.signals import ContactCandidate


def _signal_matched(key: str, value: object, qualification: Qualification) -> bool:
    if key == "last_content_year" and qualification.stale_content_before_year is not None:
        return isinstance(value, int) and value < qualification.stale_content_before_year
    if key == "page_weight_mb" and qualification.max_page_weight_mb is not None:
        return isinstance(value, (int, float)) and value > qualification.max_page_weight_mb
    return bool(value)


def is_qualified(
    profile: TargetProfile,
    contacts: list[ContactCandidate],
    signals: dict[str, object],
) -> bool:
    qualification = profile.qualification

    if qualification.require_email and not contacts:
        return False

    if qualification.require_any_signal:
        matched = sum(
            1
            for key in qualification.require_any_signal
            if _signal_matched(key, signals.get(key), qualification)
        )
        if matched < qualification.min_signal_count:
            return False

    return True
