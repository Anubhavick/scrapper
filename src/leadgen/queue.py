"""RQ queue setup, mirroring db/session.py's pattern for DATABASE_URL:
reads REDIS_URL from the environment rather than a hardcoded default, so
the same code points at local or prod Redis by env alone.

Exists because the orchestration loop (send/orchestrator.py,
db/orchestration.py) can take hours to finish -- 90-600s between each of
up to 50 messages per mailbox -- so it can never run inside a web
request/response cycle (same reasoning as the scan-builder page not
triggering a scan synchronously, docs/10). `api/campaigns.py`'s "Start
sending" button enqueues `jobs.send_campaign_messages_job` here instead
of calling `db/orchestration.py` directly; a separate `uv run rq worker`
process actually executes it. See docs/13.
"""

from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv
from redis import Redis
from rq import Queue

__all__ = ["QUEUE_NAME", "get_queue"]

QUEUE_NAME = "leadgen"


@lru_cache(maxsize=1)
def get_queue() -> Queue:
    load_dotenv()
    redis_url = os.environ["REDIS_URL"]
    return Queue(QUEUE_NAME, connection=Redis.from_url(redis_url))
