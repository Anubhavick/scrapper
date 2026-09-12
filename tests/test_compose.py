from pathlib import Path

import pytest

from leadgen.compose.render import ComposeError, pick_generated_line, pick_subject, render_message
from leadgen.config.models import Offer

OFFER = Offer(
    id="appointment-automation",
    subject_templates=[
        "{business_name} — missed calls after hours?",
        "quick question about {business_name}'s bookings",
    ],
    body_template="templates/appointment_automation.txt",
    relevant_signals=["no_online_booking", "no_contact_form"],
    cta="Worth a 10-minute call this week?",
)


def test_pick_subject_is_deterministic() -> None:
    first = pick_subject(OFFER.subject_templates, "Smile Dental")
    second = pick_subject(OFFER.subject_templates, "Smile Dental")
    assert first == second
    assert first in OFFER.subject_templates


def test_pick_generated_line_uses_first_matching_signal() -> None:
    line = pick_generated_line(OFFER.relevant_signals, {"no_online_booking": True, "no_contact_form": True})
    assert "booking" in line


def test_pick_generated_line_returns_none_when_nothing_truthy() -> None:
    assert pick_generated_line(OFFER.relevant_signals, {"no_online_booking": False}) is None


def test_render_message_fills_template(tmp_path: Path) -> None:
    templates_root = tmp_path
    (templates_root / "templates").mkdir()
    (templates_root / "templates" / "appointment_automation.txt").write_text(
        "Hi, {generated_line} for {business_name}. {cta}"
    )

    subject, body = render_message(
        business_name="Smile Dental",
        offer=OFFER,
        signals={"no_online_booking": True},
        templates_root=templates_root,
    )
    assert "Smile Dental" in subject
    assert "Smile Dental" in body
    assert "booking" in body
    assert "Worth a 10-minute call this week?" in body


def test_render_message_raises_when_no_signal_matches(tmp_path: Path) -> None:
    with pytest.raises(ComposeError):
        render_message(
            business_name="Smile Dental",
            offer=OFFER,
            signals={"no_online_booking": False, "no_contact_form": False},
            templates_root=tmp_path,
        )


def test_render_message_raises_on_missing_template_file(tmp_path: Path) -> None:
    with pytest.raises(ComposeError):
        render_message(
            business_name="Smile Dental",
            offer=OFFER,
            signals={"no_online_booking": True},
            templates_root=tmp_path,
        )
