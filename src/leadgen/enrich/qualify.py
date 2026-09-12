"""Decide whether a crawled business is a sendable lead, per the
profile's `qualification` rules.

Boolean signals (no_https, no_mobile_viewport, no_contact_form,
no_online_booking, has_whatsapp_link) count toward
`require_any_signal`/`min_signal_count` exactly when True — unambiguous.

For the two numeric signals (`last_content_year`, `page_weight_mb`) and
`site_platform` (a string), PROJECT.md's own example profile lists
`last_content_year` in `require_any_signal` without ever defining what
value makes an old site "count": is 2022 stale? 2018? There is no
threshold anywhere in the spec, and inventing one here (e.g. "stale if
more than 2 years old") would silently decide which real businesses get
emailed — that's a product call, not a plumbing detail, and it isn't
mine to make unilaterally. Until there's an explicit threshold in
config, a non-boolean signal counts toward `require_any_signal` when
its value is truthy (the crawler found *some* value at all) — not when
that value indicates a problem. Revisit this once the build order's
"read 200 rows by hand" step (5) shows whether magnitude actually
matters for these two signals.
"""

from __future__ import annotations

from leadgen.config.models import TargetProfile
from leadgen.enrich.signals import ContactCandidate


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
            1 for key in qualification.require_any_signal if signals.get(key)
        )
        if matched < qualification.min_signal_count:
            return False

    return True
