"""Pure-logic tests for api/suppressions.py's _normalise_email -- the
routes themselves need a real Postgres (Suppression uses the same
JSONB/UUID-vs-SQLite-incompatible db/models.py as everything else in
db/), verified manually instead, same reasoning as api/campaigns.py."""

import pytest

from leadgen.api.suppressions import _normalise_email


def test_normalise_email_lowercases_and_strips():
    assert _normalise_email("  Lead@Example.COM  ") == "lead@example.com"


@pytest.mark.parametrize("bad", ["not-an-email", "@example.com", "lead@", "lead @example.com", ""])
def test_normalise_email_rejects_malformed_input(bad):
    with pytest.raises(ValueError):
        _normalise_email(bad)
