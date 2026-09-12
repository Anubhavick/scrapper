"""Send-queue decision logic: suppression + daily cap + randomised gap,
enforced in code per PROJECT.md's hard rules, not left to configuration.

This module takes already-fetched data (sent-today count, suppression
sets) rather than a DB session, so the decision path is unit-testable
without a real Postgres — see `db/repository.py` for the actual queries
and `send/gmail.py` for the actual API call. Nothing here decides *that*
a message should be approved; approval is a human clicking approve
upstream of this (PROJECT.md's hard rule), never inferred here.
"""

from __future__ import annotations

import random
import time

from leadgen.send.caps import can_send
from leadgen.send.suppression import is_suppressed

MIN_DELAY_SECONDS = 90
MAX_DELAY_SECONDS = 600

__all__ = ["SendBlocked", "check_sendable", "random_delay_seconds", "wait_before_next_send"]


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
