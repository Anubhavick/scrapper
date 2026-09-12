"""Shared defensive HTTP timeout config for every outbound request
`enrich/` makes to a business's own site (crawling it, or fetching its
robots.txt). One place to tune the connect/read budget instead of
crawler.py and robots.py drifting out of sync with each other.
"""

from __future__ import annotations

import httpx

# Split connect vs. read because they're different failure modes: a host
# that never completes the TCP/TLS handshake (firewalled, dead IP) is a
# different problem from one that accepts the connection and then never
# sends a body (slow CMS, stalled proxy) — and we want to fail fast on
# both rather than let either hang the pipeline.
CRAWL_REQUEST_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0)

__all__ = ["CRAWL_REQUEST_TIMEOUT"]
