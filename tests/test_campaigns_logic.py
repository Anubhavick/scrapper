"""Pure-logic tests for db/campaigns.py -- the DB-touching functions
(create_campaign, generate_campaign_messages) need a real Postgres, same
reasoning as db/persist.py (see docs/11's Verification section for that
manual run), but `_select_best_contact` takes a plain list and is worth
covering directly."""

from leadgen.db.campaigns import _select_best_contact
from leadgen.db.models import Contact


def _contact(email: str, is_generic: bool) -> Contact:
    return Contact(email=email, is_generic=is_generic, source="website")


def test_select_best_contact_prefers_named_over_generic() -> None:
    generic = _contact("info@example.com", is_generic=True)
    named = _contact("jane@example.com", is_generic=False)

    assert _select_best_contact([generic, named]) is named


def test_select_best_contact_falls_back_to_first_when_all_generic() -> None:
    first = _contact("info@example.com", is_generic=True)
    second = _contact("contact@example.com", is_generic=True)

    assert _select_best_contact([first, second]) is first


def test_select_best_contact_returns_none_for_empty_list() -> None:
    assert _select_best_contact([]) is None
