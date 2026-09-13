"""RQ background jobs. `send_campaign_messages_job` is what
`api/campaigns.py`'s "Start sending" button enqueues (via `queue.py`)
instead of calling `db/orchestration.py` directly -- see that module's
docstring for why this can't run inside a web request.

Takes only plain, picklable arguments (a campaign id as a string, a
bool) rather than a Session/httpx.Client -- those aren't safe to hand
across the process boundary to the `rq worker` process that actually
runs this. Opens its own session/client internally instead, the same
way `scripts/send_approved_messages.py` does.

Progress is deliberately *not* tracked here (no custom job-state, no
progress callback): `run_approved_messages()` already updates each
`messages` row's `status` as it goes, so `api/campaigns.py` reads
progress straight from Postgres by re-querying that campaign's message
status counts. This job's return value is a same-run summary for the
RQ dashboard/logs, not something anything polls.
"""

from __future__ import annotations

import logging
import os

import httpx
from dotenv import load_dotenv

from leadgen.db.orchestration import run_approved_messages
from leadgen.db.session import session_scope
from leadgen.send.crypto import TokenCipher

logger = logging.getLogger(__name__)

__all__ = ["CONFIRMATION_PHRASE", "send_campaign_messages_job"]

# Shared with scripts/send_approved_messages.py's --live gate, and with
# api/campaigns.py's "Start sending" form -- one phrase, one place, so a
# real send can never fire from a slipped default or a stray click.
CONFIRMATION_PHRASE = "send real email"


def send_campaign_messages_job(campaign_id: str, *, live: bool) -> dict[str, int]:
    load_dotenv()
    client_id = os.environ["GOOGLE_OAUTH_CLIENT_ID"]
    client_secret = os.environ["GOOGLE_OAUTH_CLIENT_SECRET"]
    cipher = TokenCipher(os.environ["TOKEN_ENCRYPTION_KEY"])

    with session_scope() as session, httpx.Client(timeout=30.0) as client:
        results = run_approved_messages(
            session,
            client=client,
            client_id=client_id,
            client_secret=client_secret,
            cipher=cipher,
            dry_run=not live,
            campaign_id=campaign_id,
        )

    by_outcome: dict[str, int] = {}
    for result in results:
        by_outcome[result.outcome] = by_outcome.get(result.outcome, 0) + 1
    logger.info("campaign %s send job finished: %s", campaign_id, by_outcome)
    return by_outcome
