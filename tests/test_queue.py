import pytest

from leadgen.send.queue import SendBlocked, check_sendable


def test_allows_send_when_not_suppressed_and_under_cap() -> None:
    check_sendable(
        email="lead@clinic.example",
        suppressed_emails=set(),
        suppressed_domains=set(),
        sent_today=10,
        daily_cap=50,
    )  # no raise


def test_blocks_suppressed_email() -> None:
    with pytest.raises(SendBlocked):
        check_sendable(
            email="lead@clinic.example",
            suppressed_emails={"lead@clinic.example"},
            suppressed_domains=set(),
            sent_today=0,
            daily_cap=50,
        )


def test_blocks_suppressed_domain() -> None:
    with pytest.raises(SendBlocked):
        check_sendable(
            email="lead@clinic.example",
            suppressed_emails=set(),
            suppressed_domains={"clinic.example"},
            sent_today=0,
            daily_cap=50,
        )


def test_blocks_when_over_daily_cap() -> None:
    with pytest.raises(SendBlocked):
        check_sendable(
            email="lead@clinic.example",
            suppressed_emails=set(),
            suppressed_domains=set(),
            sent_today=50,
            daily_cap=50,
        )


def test_suppression_checked_even_when_under_cap_and_vice_versa() -> None:
    # Suppression wins even with room left in the cap.
    with pytest.raises(SendBlocked, match="suppressed"):
        check_sendable(
            email="lead@clinic.example",
            suppressed_emails={"lead@clinic.example"},
            suppressed_domains=set(),
            sent_today=0,
            daily_cap=50,
        )
