import pytest

from leadgen.send.queue import SendBlocked, check_sendable, send_next


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


def test_send_next_waits_before_reading_state(monkeypatch) -> None:
    call_order: list[str] = []
    monkeypatch.setattr(
        "leadgen.send.queue.wait_before_next_send", lambda: call_order.append("waited")
    )

    def fetch_emails() -> set[str]:
        call_order.append("read-emails")
        return set()

    def fetch_domains() -> set[str]:
        call_order.append("read-domains")
        return set()

    def fetch_sent_today() -> int:
        call_order.append("read-sent-today")
        return 0

    def do_send() -> dict:
        call_order.append("sent")
        return {"id": "msg1"}

    result = send_next(
        email="lead@clinic.example",
        suppressed_emails_fn=fetch_emails,
        suppressed_domains_fn=fetch_domains,
        sent_today_fn=fetch_sent_today,
        daily_cap=50,
        send_fn=do_send,
    )

    assert result == {"id": "msg1"}
    assert call_order[0] == "waited"
    assert call_order[-1] == "sent"
    assert set(call_order[1:-1]) == {"read-emails", "read-domains", "read-sent-today"}


def test_send_next_blocks_without_sending_when_state_went_stale_during_wait(monkeypatch) -> None:
    # Simulates a suppression landing (or the cap filling from another
    # worker) while this send was sleeping out its randomised gap.
    monkeypatch.setattr("leadgen.send.queue.wait_before_next_send", lambda: None)

    sent = {"called": False}

    def do_send() -> dict:
        sent["called"] = True
        return {"id": "msg1"}

    with pytest.raises(SendBlocked):
        send_next(
            email="lead@clinic.example",
            suppressed_emails_fn=lambda: {"lead@clinic.example"},
            suppressed_domains_fn=set,
            sent_today_fn=lambda: 0,
            daily_cap=50,
            send_fn=do_send,
        )

    assert sent["called"] is False
