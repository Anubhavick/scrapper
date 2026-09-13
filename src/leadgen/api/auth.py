"""HTTP Basic Auth gate for the whole web UI (ROADMAP.md item 5: "no auth
on the web UI at all... a real problem the moment it's reachable by
anyone else").

A single shared username/password, not per-user accounts -- this is a
small team on one internal tool, and per-user identity already has a
place in this system (typing a name to approve a message, docs/11;
`reason` on a suppression) that has nothing to do with who's logged in.
Adding a users table/login form/session cookies for that would be new
surface area solving a problem nobody has yet. HTTP Basic is enough to
stop an unauthenticated visitor from reaching any page -- the browser's
native login prompt, no session/cookie code to write or get wrong.

Credentials come from `WEB_UI_USERNAME`/`WEB_UI_PASSWORD` -- plaintext in
`.env`, same as `GOOGLE_OAUTH_CLIENT_SECRET`/`TOKEN_ENCRYPTION_KEY`
already are (never committed, per the Hard rules). Read lazily on every
request (matching `queue.py`'s `get_queue()` style) rather than once at
import time, so a value change doesn't need a process restart to take
effect and so tests can set/change them per test via monkeypatch.
"""

from __future__ import annotations

import os
import secrets

from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

__all__ = ["require_auth"]

_security = HTTPBasic()


def _configured_credentials() -> tuple[str, str]:
    load_dotenv()  # matches db/session.py's get_engine() -- lazy, not at import time
    username = os.environ.get("WEB_UI_USERNAME")
    password = os.environ.get("WEB_UI_PASSWORD")
    if not username or not password:
        # Deliberately not swallowed into a "just let anyone in" fallback
        # -- the whole point of this module is that the app must not
        # serve a page without configured credentials.
        raise RuntimeError(
            "WEB_UI_USERNAME and WEB_UI_PASSWORD must both be set (see "
            ".env.example) before this app will serve any page."
        )
    return username, password


def require_auth(credentials: HTTPBasicCredentials = Depends(_security)) -> str:
    """FastAPI dependency, wired in once at the `FastAPI(dependencies=...)`
    level (api/review.py) so it runs before every route on the shared
    app -- including ones added later via `include_router`, and the
    auto-generated `/docs`/`/openapi.json`.

    `secrets.compare_digest` (not `==`) for both fields, per FastAPI's
    own HTTP Basic Auth documentation: a plain string comparison returns
    as soon as the first differing byte is found, which leaks how many
    leading characters of a guess were correct through response timing.
    """
    username, password = _configured_credentials()
    valid_username = secrets.compare_digest(credentials.username, username)
    valid_password = secrets.compare_digest(credentials.password, password)
    if not (valid_username and valid_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username
