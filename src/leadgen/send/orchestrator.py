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
from typing import TYPE_CHECKING, Callable

from leadgen.send.queue import SendBlocked, send_next

if TYPE_CHECKING:
    from leadgen.send.oauth import TokenHealth

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
    token_health_fn: Callable[[object], "TokenHealth"] | None = None,
    mailbox_active_fn: Callable[[object], bool] | None = None,
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
    enforce. See the module docstring for how this was actually caught.

    `token_health_fn(mailbox_id)`, when given, is called once per mailbox
    *before* that mailbox's first job is attempted -- a preflight, not a
    per-message check. A dead refresh token (revoked consent, the 7-day
    Testing-mode expiry, an account security event) is a per-*mailbox*
    fact, not a per-message one: without this, a dead token used to
    surface as `send_fn` raising once per remaining message in that
    mailbox's queue, each one only discovered after `send_next()` had
    already slept out its own 90-600s gap first. When the check reports
    unhealthy, every job in that mailbox is reported blocked
    (`reason="token"`, via `on_blocked`/the returned `SendResult`s) with
    no sleep, no reservation, and no send attempt for any of them --
    mirroring how a `"cap"` block already stops the rest of that
    mailbox's queue, just decided up front instead of after the first
    job fails. Other mailboxes are unaffected either way.

    `mailbox_active_fn(mailbox_id)`, when given, is checked at the same
    point as `token_health_fn` -- once per mailbox, before its queue
    starts. `jobs` is normally built from a single up-front snapshot of
    which mailboxes were active (docs/12); for a long-running loop over
    many mailboxes, one could be deactivated (an incident, a mistake
    caught mid-run) after that snapshot was taken but before this
    function reaches its turn. Checking again right here -- not just
    once for the whole run -- catches that without needing a separate
    poller. An inactive mailbox blocks all its jobs the same way an
    unhealthy token does (`reason="mailbox_inactive"`)."""
    by_mailbox: dict[object, list[SendJob]] = {}
    for job in jobs:
        by_mailbox.setdefault(job.mailbox_id, []).append(job)

    results: list[SendResult] = []
    for mailbox_id, mailbox_jobs in by_mailbox.items():
        if mailbox_active_fn is not None and not mailbox_active_fn(mailbox_id):
            exc = SendBlocked(f"mailbox {mailbox_id} is no longer active", reason="mailbox_inactive")
            for job in mailbox_jobs:
                on_blocked(job, exc)
                results.append(SendResult(job, "blocked", detail=str(exc)))
            continue
        if token_health_fn is not None:
            health = token_health_fn(mailbox_id)
            if not health.healthy:
                exc = SendBlocked(
                    f"mailbox {mailbox_id} refresh token unhealthy: {health.reason}",
                    reason="token",
                )
                for job in mailbox_jobs:
                    on_blocked(job, exc)
                    results.append(SendResult(job, "blocked", detail=str(exc)))
                continue
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
