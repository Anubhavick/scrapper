"""Daily send-cap decision logic.

`mailboxes` has no daily-counter column on purpose (see CLAUDE.md's schema
decisions) — the count is computed from `messages.sent_at` at query time.
This module only holds the pure decision (`can_send`); the actual COUNT(*)
query lives in `db/repository.py`, which needs a real Postgres to exercise —
unlike this module, it's not covered by the mocked/pure test suite.
"""

from __future__ import annotations

from datetime import datetime, timezone

__all__ = ["start_of_day_utc", "can_send"]


def start_of_day_utc(now: datetime) -> datetime:
    """"Today" means the UTC calendar day, not each mailbox's local day —
    PROJECT.md doesn't specify a timezone for the cap, and per-mailbox
    local-day tracking would need a timezone column that doesn't exist.
    Worth revisiting if mailboxes end up spread across very different
    timezones and the boundary starts mattering in practice."""
    return now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def can_send(sent_today: int, daily_cap: int) -> bool:
    return sent_today < daily_cap
