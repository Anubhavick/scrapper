"""Pure-logic tests for send/orchestrator.py's run_orchestration_loop --
everything here is plain callables/mocks, no DB, mirroring test_queue.py's
approach. The DB-touching glue (db/orchestration.py) needs a real
Postgres, verified manually instead -- see docs/12."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from rq.timeouts import JobTimeoutException

from leadgen.send.orchestrator import SendJob, run_orchestration_loop
from leadgen.send.queue import SendBlocked


@dataclass(frozen=True)
class _FakeTokenHealth:
    healthy: bool
    reason: str | None = None


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("leadgen.send.queue.wait_before_next_send", lambda: None)


def _job(message_id: str, mailbox_id: str = "mb1", email: str = "lead@clinic.example", daily_cap: int = 50) -> SendJob:
    return SendJob(message_id=message_id, mailbox_id=mailbox_id, email=email, daily_cap=daily_cap)


def test_sends_every_job_when_nothing_blocks():
    jobs = [_job("m1"), _job("m2")]
    sent = []

    results = run_orchestration_loop(
        jobs,
        suppressed_emails_fn=set,
        suppressed_domains_fn=set,
        reserve_fn=lambda job: 0,
        send_fn=lambda job: {"id": f"gmail-{job.message_id}"},
        on_sent=lambda job, resp: sent.append((job.message_id, resp)),
        on_blocked=lambda job, exc: pytest.fail("should not block"),
    )

    assert [r.outcome for r in results] == ["sent", "sent"]
    assert sent == [("m1", {"id": "gmail-m1"}), ("m2", {"id": "gmail-m2"})]


def test_cap_block_stops_rest_of_same_mailbox_but_not_other_mailboxes():
    jobs = [_job("m1", mailbox_id="mb1"), _job("m2", mailbox_id="mb1"), _job("m3", mailbox_id="mb2")]
    sent = []
    blocked = []

    def reserve(job):
        # mb1 is already at cap; mb2 has room.
        return 50 if job.mailbox_id == "mb1" else 0

    results = run_orchestration_loop(
        jobs,
        suppressed_emails_fn=set,
        suppressed_domains_fn=set,
        reserve_fn=reserve,
        send_fn=lambda job: {"id": f"gmail-{job.message_id}"},
        on_sent=lambda job, resp: sent.append(job.message_id),
        on_blocked=lambda job, exc: blocked.append((job.message_id, exc.reason)),
    )

    # m2 never attempted: mb1 stopped after m1's cap block.
    assert [r.job.message_id for r in results] == ["m1", "m3"]
    assert blocked == [("m1", "cap")]
    assert sent == ["m3"]


def test_suppression_block_skips_only_that_job_and_continues_mailbox():
    jobs = [_job("m1", email="bad@clinic.example"), _job("m2", email="good@clinic.example")]
    sent = []
    blocked = []

    results = run_orchestration_loop(
        jobs,
        suppressed_emails_fn=lambda: {"bad@clinic.example"},
        suppressed_domains_fn=set,
        reserve_fn=lambda job: 0,
        send_fn=lambda job: {"id": f"gmail-{job.message_id}"},
        on_sent=lambda job, resp: sent.append(job.message_id),
        on_blocked=lambda job, exc: blocked.append((job.message_id, exc.reason)),
    )

    assert [r.job.message_id for r in results] == ["m1", "m2"]
    assert blocked == [("m1", "suppressed")]
    assert sent == ["m2"]


def test_send_fn_error_reported_and_does_not_abort_run():
    jobs = [_job("m1", mailbox_id="mb1"), _job("m2", mailbox_id="mb2")]
    errors = []

    def send_fn(job):
        if job.message_id == "m1":
            raise RuntimeError("gmail API down")
        return {"id": "gmail-m2"}

    results = run_orchestration_loop(
        jobs,
        suppressed_emails_fn=set,
        suppressed_domains_fn=set,
        reserve_fn=lambda job: 0,
        send_fn=send_fn,
        on_sent=lambda job, resp: None,
        on_blocked=lambda job, exc: pytest.fail("should not be a SendBlocked"),
        on_error=lambda job, exc: errors.append((job.message_id, str(exc))),
    )

    assert [(r.job.message_id, r.outcome) for r in results] == [("m1", "error"), ("m2", "sent")]
    assert errors == [("m1", "gmail API down")]


def test_job_timeout_exception_propagates_instead_of_being_treated_as_a_send_error():
    # Caught for real in docs/13: an rq worker's own job_timeout signal is
    # an Exception subclass, so a naive `except Exception` swallows it --
    # letting the loop keep sleeping/sending well past when the worker
    # process was supposed to be killed. Must propagate, not be recorded
    # as a per-message "error" outcome.
    jobs = [_job("m1", mailbox_id="mb1"), _job("m2", mailbox_id="mb1")]

    def send_fn(job):
        raise JobTimeoutException("Task exceeded maximum timeout value (180 seconds)")

    with pytest.raises(JobTimeoutException):
        run_orchestration_loop(
            jobs,
            suppressed_emails_fn=set,
            suppressed_domains_fn=set,
            reserve_fn=lambda job: 0,
            send_fn=send_fn,
            on_sent=lambda job, resp: None,
            on_blocked=lambda job, exc: pytest.fail("should not be a SendBlocked"),
            on_error=lambda job, exc: pytest.fail("must not be reported as an ordinary per-message error"),
        )


def test_unhealthy_token_blocks_every_job_for_that_mailbox_without_attempting_any():
    jobs = [_job("m1", mailbox_id="mb1"), _job("m2", mailbox_id="mb1"), _job("m3", mailbox_id="mb2")]
    blocked = []
    sent = []

    def token_health(mailbox_id):
        return _FakeTokenHealth(healthy=False, reason="invalid_grant") if mailbox_id == "mb1" else _FakeTokenHealth(healthy=True)

    def reserve_fn(job):
        if job.mailbox_id == "mb1":
            pytest.fail("mb1 should never reach reserve_fn")
        return 0

    def send_fn(job):
        if job.mailbox_id == "mb1":
            pytest.fail("mb1 should never send")
        return {"id": f"gmail-{job.message_id}"}

    results = run_orchestration_loop(
        jobs,
        suppressed_emails_fn=set,
        suppressed_domains_fn=set,
        reserve_fn=reserve_fn,
        send_fn=send_fn,
        on_sent=lambda job, resp: sent.append(job.message_id),
        on_blocked=lambda job, exc: blocked.append((job.message_id, exc.reason)),
        token_health_fn=token_health,
    )

    assert blocked == [("m1", "token"), ("m2", "token")]
    assert sent == ["m3"]
    assert [r.outcome for r in results] == ["blocked", "blocked", "sent"]


def test_healthy_token_does_not_block_and_checks_once_per_mailbox():
    jobs = [_job("m1", mailbox_id="mb1"), _job("m2", mailbox_id="mb1")]
    checked = []

    def token_health(mailbox_id):
        checked.append(mailbox_id)
        return _FakeTokenHealth(healthy=True)

    results = run_orchestration_loop(
        jobs,
        suppressed_emails_fn=set,
        suppressed_domains_fn=set,
        reserve_fn=lambda job: 0,
        send_fn=lambda job: {"id": f"gmail-{job.message_id}"},
        on_sent=lambda job, resp: None,
        on_blocked=lambda job, exc: pytest.fail("should not block"),
        token_health_fn=token_health,
    )

    assert [r.outcome for r in results] == ["sent", "sent"]
    assert checked == ["mb1"]  # one check for the mailbox, not per job


def test_no_token_health_fn_means_no_preflight_check_at_all():
    # Default (token_health_fn=None) must behave exactly as before this
    # feature existed -- callers that don't opt in are unaffected.
    results = run_orchestration_loop(
        [_job("m1")],
        suppressed_emails_fn=set,
        suppressed_domains_fn=set,
        reserve_fn=lambda job: 0,
        send_fn=lambda job: {"id": "gmail-m1"},
        on_sent=lambda job, resp: None,
        on_blocked=lambda job, exc: pytest.fail("should not block"),
    )
    assert [r.outcome for r in results] == ["sent"]


def test_inactive_mailbox_blocks_every_job_for_that_mailbox_without_attempting_any():
    jobs = [_job("m1", mailbox_id="mb1"), _job("m2", mailbox_id="mb1"), _job("m3", mailbox_id="mb2")]
    blocked = []
    sent = []

    def mailbox_active(mailbox_id):
        return mailbox_id != "mb1"

    def reserve_fn(job):
        if job.mailbox_id == "mb1":
            pytest.fail("mb1 should never reach reserve_fn")
        return 0

    def send_fn(job):
        if job.mailbox_id == "mb1":
            pytest.fail("mb1 should never send")
        return {"id": f"gmail-{job.message_id}"}

    results = run_orchestration_loop(
        jobs,
        suppressed_emails_fn=set,
        suppressed_domains_fn=set,
        reserve_fn=reserve_fn,
        send_fn=send_fn,
        on_sent=lambda job, resp: sent.append(job.message_id),
        on_blocked=lambda job, exc: blocked.append((job.message_id, exc.reason)),
        mailbox_active_fn=mailbox_active,
    )

    assert blocked == [("m1", "mailbox_inactive"), ("m2", "mailbox_inactive")]
    assert sent == ["m3"]
    assert [r.outcome for r in results] == ["blocked", "blocked", "sent"]


def test_mailbox_active_checked_before_token_health_so_a_dead_mailbox_skips_the_token_call_too():
    jobs = [_job("m1", mailbox_id="mb1")]
    token_health_calls = []

    def mailbox_active(mailbox_id):
        return False

    def token_health(mailbox_id):
        token_health_calls.append(mailbox_id)
        return _FakeTokenHealth(healthy=True)

    results = run_orchestration_loop(
        jobs,
        suppressed_emails_fn=set,
        suppressed_domains_fn=set,
        reserve_fn=lambda job: 0,
        send_fn=lambda job: pytest.fail("should not send"),
        on_sent=lambda job, resp: None,
        on_blocked=lambda job, exc: None,
        token_health_fn=token_health,
        mailbox_active_fn=mailbox_active,
    )

    assert token_health_calls == []  # never reached -- inactive mailbox short-circuits first
    assert [r.outcome for r in results] == ["blocked"]


def test_no_mailbox_active_fn_means_no_preflight_check_at_all():
    # Default (mailbox_active_fn=None) must behave exactly as before this
    # feature existed -- callers that don't opt in are unaffected.
    results = run_orchestration_loop(
        [_job("m1")],
        suppressed_emails_fn=set,
        suppressed_domains_fn=set,
        reserve_fn=lambda job: 0,
        send_fn=lambda job: {"id": "gmail-m1"},
        on_sent=lambda job, resp: None,
        on_blocked=lambda job, exc: pytest.fail("should not block"),
    )
    assert [r.outcome for r in results] == ["sent"]


def test_reserve_fn_is_called_after_suppression_checks_so_it_never_reserves_a_suppressed_send():
    call_order = []

    def suppressed_emails():
        call_order.append("suppression")
        return {"lead@clinic.example"}

    def reserve(job):
        call_order.append("reserve")
        return 0

    run_orchestration_loop(
        [_job("m1")],
        suppressed_emails_fn=suppressed_emails,
        suppressed_domains_fn=set,
        reserve_fn=reserve,
        send_fn=lambda job: pytest.fail("should not send a suppressed message"),
        on_sent=lambda job, resp: None,
        on_blocked=lambda job, exc: None,
    )

    assert call_order == ["suppression"]  # reserve_fn never reached
