"""The orchestration loop's decision logic: given a list of approved
sends grouped by mailbox, drive each one through `send/queue.py`'s
`send_next()` in order, using injected callables for every side effect
(state reads, the reservation, the actual send, recording the outcome).

Deliberately takes plain callables rather than SQLAlchemy sessions/ORM
models -- the same separation `send/queue.py`/`send/caps.py`/
`send/suppression.py` already use -- so this module's actual decisions
(which mailbox goes next, when a cap-block should stop a mailbox's queue
vs. a suppression-block only skipping one message) are covered by the
pure/mocked test suite. The DB-touching glue that builds `SendJob`s from
real `messages`/`mailboxes` rows and implements the callables against a
real Postgres lives in `db/orchestration.py` -- not covered by this
suite for the same JSONB/UUID-vs-SQLite reason as `db/repository.py`/
`db/campaigns.py`, verify that manually against a real Postgres instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from leadgen.send.queue import SendBlocked, send_next

try:
    # Soft dependency: this module has no hard import of rq (it's meant
    # to be usable/testable with zero external services, per the module
    # docstring), but when a caller does run this inside an `rq worker`
    # process (db/orchestration.py's job does, via jobs.py), the broad
    # `except Exception` below would otherwise silently swallow RQ's own
    # job-cancellation signal -- JobTimeoutException subclasses Exception,
    # not BaseException, specifically the kind of thing a bare `except
    # Exception` is written to catch. Caught for real during docs/13's
    # verification: a job hit its (default, too-short-for-this-loop)
    # timeout mid-sleep, the exception was swallowed as if it were "one
    # bad message," and the job reported success -- defeating the
    # timeout as a safety net entirely. See the docstring below.
    from rq.timeouts import JobTimeoutException
except ImportError:
    JobTimeoutException = ()  # isinstance(exc, ()) is always False

__all__ = ["SendJob", "SendResult", "run_orchestration_loop"]


@dataclass(frozen=True)
class SendJob:
    """One approved message queued to send. `message_id`/`mailbox_id` are
    opaque to this module (real UUIDs in production, plain strings in
    tests) -- it never inspects them, only groups/threads them through."""

    message_id: object
    mailbox_id: object
    email: str
    daily_cap: int


@dataclass(frozen=True)
class SendResult:
    job: SendJob
    outcome: str  # "sent" | "blocked"
    detail: str | None = None
    gmail_response: dict | None = None


def run_orchestration_loop(
    jobs: list[SendJob],
    *,
    suppressed_emails_fn: Callable[[], set[str]],
    suppressed_domains_fn: Callable[[], set[str]],
    reserve_fn: Callable[[SendJob], int],
    send_fn: Callable[[SendJob], dict],
    on_sent: Callable[[SendJob, dict], None],
    on_blocked: Callable[[SendJob, SendBlocked], None],
    on_error: Callable[[SendJob, Exception], None] | None = None,
) -> list[SendResult]:
    """Groups `jobs` by `mailbox_id` (stable order within each group) and
    works through each mailbox's queue in turn via `send_next()` -- which
    sleeps the randomised 90-600s gap *before* re-reading suppression/cap
    state, so a message that looked fine when queued is still checked
    against fresh state right before it actually sends.

    `reserve_fn(job)` is the `sent_today_fn` `send_next()` calls: it must
    take whatever atomic lock is needed (`db.repository.reserve_send_slot`
    in production) and, as a side effect while that lock is held, commit
    the reservation itself if there's room -- returning the *pre*-
    reservation sent-today count either way, so `send_next()`'s own
    `can_send(count, daily_cap)` recomputation agrees with what
    `reserve_fn` just decided. See `db/orchestration.py` for why the
    reservation has to be "mark the message row sent" rather than a
    separate counter, and why that happens before `send_fn`'s network
    call rather than after.

    A `SendBlocked` with `reason == "cap"` stops the *current* mailbox's
    remaining jobs (every one of them would fail the identical cap check)
    but still lets other mailboxes take their turn; `reason ==
    "suppressed"` only skips that one job.

    Any other exception from `reserve_fn`/`send_fn` (a Gmail API error, a
    dead refresh token) is caught, reported via `on_error`, and recorded
    as `"error"` rather than aborting the whole run -- one bad message or
    a transient Gmail failure shouldn't stop every other mailbox's queue.
    It does *not* stop the rest of the same mailbox's queue either: a
    one-off network error isn't reliably distinguishable here from a
    per-message problem, so the conservative default is to keep trying
    the mailbox's remaining jobs rather than assume the whole mailbox is
    broken.

    The one exception this re-raises instead of swallowing: an RQ
    `JobTimeoutException` (when `rq` is installed). That signal means the
    *process itself* is being cancelled -- treating it like an ordinary
    per-message error would let the loop keep going, sleeping and
    sending, indefinitely past whatever `job_timeout` was supposed to
    enforce. See the module docstring for how this was actually caught."""
    by_mailbox: dict[object, list[SendJob]] = {}
    for job in jobs:
        by_mailbox.setdefault(job.mailbox_id, []).append(job)

    results: list[SendResult] = []
    for mailbox_jobs in by_mailbox.values():
        for job in mailbox_jobs:
            try:
                gmail_response = send_next(
                    email=job.email,
                    suppressed_emails_fn=suppressed_emails_fn,
                    suppressed_domains_fn=suppressed_domains_fn,
                    sent_today_fn=lambda job=job: reserve_fn(job),
                    daily_cap=job.daily_cap,
                    send_fn=lambda job=job: send_fn(job),
                )
            except SendBlocked as exc:
                on_blocked(job, exc)
                results.append(SendResult(job, "blocked", detail=str(exc)))
                if exc.reason == "cap":
                    break
                continue
            except JobTimeoutException:
                raise  # the worker's own cancellation signal -- must propagate, never be treated as "one bad message"
            except Exception as exc:  # noqa: BLE001 -- see docstring: one bad send must not kill the run
                if on_error is not None:
                    on_error(job, exc)
                results.append(SendResult(job, "error", detail=str(exc)))
                continue
            else:
                on_sent(job, gmail_response)
                results.append(SendResult(job, "sent", gmail_response=gmail_response))

    return results
