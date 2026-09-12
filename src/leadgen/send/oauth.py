"""Google OAuth 2.0 for Gmail send access — one mailbox's authorization-code
flow plus refresh.

Scope is `gmail.send` only (not `gmail.readonly`/`gmail.modify`, which are
*restricted* scopes requiring a CASA security assessment before Google
allows them in production). `gmail.send` is merely *sensitive*: it just
needs the OAuth consent screen out of Testing mode before refresh tokens
stop expiring after 7 days. See docs/06 before registering real mailboxes.
"""

from __future__ import annotations

from urllib.parse import urlencode

import httpx

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"

__all__ = ["OAuthError", "build_authorization_url", "exchange_code_for_tokens", "refresh_access_token"]


class OAuthError(Exception):
    pass


def build_authorization_url(*, client_id: str, redirect_uri: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": GMAIL_SEND_SCOPE,
        "access_type": "offline",  # required to get a refresh_token at all
        "prompt": "consent",  # required every time, or a re-auth returns no refresh_token
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def exchange_code_for_tokens(
    client: httpx.Client,
    *,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    code: str,
) -> dict:
    """Returns Google's token response: access_token, refresh_token,
    expires_in, scope, token_type. refresh_token is only present on the
    *first* consent for a given account — callers must persist it then."""
    response = client.post(
        GOOGLE_TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "code": code,
            "grant_type": "authorization_code",
        },
    )
    return _parse_token_response(response)


def refresh_access_token(
    client: httpx.Client,
    *,
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> dict:
    """Returns a fresh access_token (no new refresh_token — Google doesn't
    reissue one on refresh)."""
    response = client.post(
        GOOGLE_TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
    )
    return _parse_token_response(response)


def _parse_token_response(response: httpx.Response) -> dict:
    if response.status_code >= 400:
        raise OAuthError(
            f"Google token endpoint returned {response.status_code}: {response.text}"
        )
    return response.json()
