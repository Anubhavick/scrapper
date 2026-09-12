"""Renders one lead's subject + body from an offer template.

Per PROJECT.md, a message is one generated line grounded in a real signal,
dropped into an otherwise-fixed template — not a free-form LLM draft. The
signal→sentence mapping below only covers boolean signals that are
unambiguous when true; `last_content_year` and `page_weight_mb` are left
out on purpose because qualify.py's documented gap (docs/05) means their
truthiness doesn't reliably indicate a real problem, and this module isn't
the place to invent a threshold either.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from leadgen.config.models import Offer

__all__ = ["ComposeError", "pick_subject", "pick_generated_line", "render_message"]


class ComposeError(Exception):
    pass


SIGNAL_LINES: dict[str, str] = {
    "no_https": "I noticed your site isn't on https yet",
    "no_mobile_viewport": "I noticed your site isn't set up for mobile screens yet",
    "no_contact_form": "I couldn't find a contact form on your site",
    "no_online_booking": "I didn't see an online booking option on your site",
    "has_whatsapp_link": "I saw you're already taking enquiries over WhatsApp",
}


def pick_subject(subject_templates: list[str], seed: str) -> str:
    """Deterministic per-business pick (so re-running compose on the same
    lead doesn't change the subject) that still varies across a lead list."""
    index = int(hashlib.sha256(seed.encode("utf-8")).hexdigest(), 16) % len(subject_templates)
    return subject_templates[index]


def pick_generated_line(relevant_signals: list[str], signals: dict) -> str | None:
    for key in relevant_signals:
        if key in SIGNAL_LINES and signals.get(key):
            return SIGNAL_LINES[key]
    return None


def render_message(
    *,
    business_name: str,
    offer: Offer,
    signals: dict,
    templates_root: Path,
) -> tuple[str, str]:
    """Returns (subject, body). Raises ComposeError if no relevant signal
    was actually truthy — a lead reaching compose without one means
    qualification should already have rejected it (see qualify.py)."""
    line = pick_generated_line(offer.relevant_signals, signals)
    if line is None:
        raise ComposeError(
            f"{business_name!r}: none of {offer.relevant_signals} were truthy in "
            f"{signals!r} — this lead shouldn't have passed qualification"
        )

    subject = pick_subject(offer.subject_templates, business_name).format(business_name=business_name)

    template_path = templates_root / offer.body_template
    try:
        body_template = template_path.read_text()
    except FileNotFoundError as exc:
        raise ComposeError(f"offer {offer.id!r} points at missing template {template_path}") from exc

    body = body_template.format(business_name=business_name, generated_line=line, cta=offer.cta)
    return subject, body
