"""Pure-function tests for api/mailboxes.py's rendering helpers -- the
route itself needs a real Postgres session (same JSONB/UUID-vs-SQLite
reason as the other DB-touching api/ modules, CLAUDE.md/MANUAL.md §7)
and makes a real call to Google's token endpoint, so it's verified
manually instead (docs/18)."""

from datetime import datetime, timezone

from leadgen.api.mailboxes import _cap_badge, _last_error_html, _token_health_badge
from leadgen.db.models import Mailbox
from leadgen.send.oauth import TokenHealth


def _mailbox(**overrides) -> Mailbox:
    defaults = dict(
        name="sales1",
        email_address="sales1@example.com",
        oauth_refresh_token_encrypted="encrypted",
        daily_cap=50,
        is_active=True,
        last_send_error=None,
        last_send_error_at=None,
    )
    defaults.update(overrides)
    return Mailbox(**defaults)


# ---- _token_health_badge ----


def test_token_health_badge_unknown_when_no_check_ran() -> None:
    assert "unknown" in _token_health_badge(None)


def test_token_health_badge_healthy() -> None:
    html = _token_health_badge(TokenHealth(healthy=True))
    assert "token ok" in html


def test_token_health_badge_unhealthy_shows_reason() -> None:
    html = _token_health_badge(TokenHealth(healthy=False, reason="invalid_grant"))
    assert "token dead" in html
    assert "invalid_grant" in html


# ---- _cap_badge ----


def test_cap_badge_well_under_cap() -> None:
    html = _cap_badge(5, 50)
    assert "5/50" in html


def test_cap_badge_near_cap_flagged() -> None:
    html = _cap_badge(45, 50)  # >= 80%
    assert "45/50" in html


def test_cap_badge_at_cap_flagged_distinctly() -> None:
    html = _cap_badge(50, 50)
    assert "at cap" in html


# ---- _last_error_html ----


def test_last_error_html_none_when_no_error() -> None:
    mailbox = _mailbox()
    assert "none since last success" in _last_error_html(mailbox)


def test_last_error_html_shows_error_and_timestamp() -> None:
    mailbox = _mailbox(
        last_send_error="Google token endpoint returned 400: invalid_grant",
        last_send_error_at=datetime(2026, 9, 13, 10, 30, tzinfo=timezone.utc),
    )
    html = _last_error_html(mailbox)
    assert "invalid_grant" in html
    assert "2026-09-13 10:30" in html


def test_last_error_html_escapes_error_text() -> None:
    mailbox = _mailbox(last_send_error="<script>alert(1)</script>")
    html = _last_error_html(mailbox)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
