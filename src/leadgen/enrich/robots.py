"""robots.txt compliance, checked once per host and cached for the life
of the client.

PROJECT.md's crawling hard rule is unconditional — every fetch goes
through this before a request is made, no exceptions for pages that
"probably" allow crawling.
"""

from __future__ import annotations

from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from leadgen.enrich.http_defaults import CRAWL_REQUEST_TIMEOUT


class RobotsChecker:
    def __init__(self, client: httpx.Client, user_agent: str) -> None:
        self._client = client
        self._user_agent = user_agent
        self._parsers: dict[str, RobotFileParser] = {}

    def can_fetch(self, url: str) -> bool:
        origin = _origin(url)
        parser = self._parsers.get(origin)
        if parser is None:
            parser = self._fetch_parser(origin)
            self._parsers[origin] = parser
        return parser.can_fetch(self._user_agent, url)

    def _fetch_parser(self, origin: str) -> RobotFileParser:
        parser = RobotFileParser()
        try:
            response = self._client.get(
                f"{origin}/robots.txt",
                headers={"User-Agent": self._user_agent},
                timeout=CRAWL_REQUEST_TIMEOUT,
            )
        except httpx.HTTPError:
            # Unreachable robots.txt: fail open, matching stdlib
            # RobotFileParser.read()'s own convention.
            parser.allow_all = True
            return parser

        if response.status_code in (401, 403):
            parser.disallow_all = True
        elif response.status_code >= 400:
            parser.allow_all = True
        else:
            parser.parse(response.text.splitlines())
        return parser


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"
