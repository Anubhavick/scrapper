"""Send-queue decision logic: suppression + daily cap + randomised gap,
enforced in code per PROJECT.md's hard rules, not left to configuration.

This module takes already-fetched data (sent-today count, suppression
sets) rather than a DB session, so the decision path is unit-testable
without a real Postgres — see `db/repository.py` for the actual queries
and `send/gmail.py` for the actual API call. Nothing here decides *that*
a message should be approved; approval is a human clicking approve
upstream of this (PROJECT.md's hard rule), never inferred here.

`send_next()` is the composition a future orchestration loop should call
instead of hand-rolling the order of operations: it sleeps out the
randomised 90-600s gap *first*, then re-fetches suppression/cap state
and calls `check_sendable()` immediately before invoking the send
callback. The state fetchers are plain callables specifically so they
are evaluated *after* the sleep, not before it — a message that was
fine to send when it was queued can go stale during a wait that long
(a suppression can land, another worker can fill the cap), and checking
once up front then sleeping would send on that stale state. `caps.py`
and `db/repository.py` deliberately have no mutable counter to
increment (see CLAUDE.md's schema-decisions note); the equivalent
atomic operation for a real caller is `db.repository.reserve_send_slot`,
which takes a Postgres transaction-level advisory lock on the mailbox
so two concurrent workers racing the same mailbox can't both observe
"under cap" before either commits. Wrap the `sent_today_fn` passed to
`send_next` around that, inside one transaction, rather than calling
`count_sent_today` standalone, once an orchestrator actually exists.
"""

from __future__ import annotations

import random
import time
from typing import Callable

from leadgen.send.caps import can_send
from leadgen.send.suppression import is_suppressed

MIN_DELAY_SECONDS = 90
MAX_DELAY_SECONDS = 600

__all__ = [
    "SendBlocked",
    "check_sendable",
    "random_delay_seconds",
    "wait_before_next_send",
    "send_next",
]


class SendBlocked(Exception):
    """Raised instead of silently skipping a message, so a caller has to
    look at (and log) why a send didn't happen."""


def check_sendable(
    *,
    email: str,
    suppressed_emails: set[str],
    suppressed_domains: set[str],
    sent_today: int,
    daily_cap: int,
) -> None:
    if is_suppressed(email=email, suppressed_emails=suppressed_emails, suppressed_domains=suppressed_domains):
        raise SendBlocked(f"{email} is suppressed")
    if not can_send(sent_today, daily_cap):
        raise SendBlocked(f"mailbox already sent {sent_today}/{daily_cap} today")


def random_delay_seconds() -> float:
    return random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS)


def wait_before_next_send() -> None:
    time.sleep(random_delay_seconds())


def send_next(
    *,
    email: str,
    suppressed_emails_fn: Callable[[], set[str]],
    suppressed_domains_fn: Callable[[], set[str]],
    sent_today_fn: Callable[[], int],
    daily_cap: int,
    send_fn: Callable[[], dict],
) -> dict:
    """Wait, THEN validate against fresh state, THEN send — in that
    order, always. The fetchers are only called after `wait_before_next_send`
    returns, so a suppression or cap change that happens *during* the
    90-600s gap is seen before `send_fn` runs, not missed because the
    state was read before the wait started. Raises SendBlocked (from
    `check_sendable`) instead of sending if the fresh state says no."""
    wait_before_next_send()
    check_sendable(
        email=email,
        suppressed_emails=suppressed_emails_fn(),
        suppressed_domains=suppressed_domains_fn(),
        sent_today=sent_today_fn(),
        daily_cap=daily_cap,
    )
    return send_fn()
