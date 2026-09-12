"""One-time interactive flow to authorize a Gmail mailbox for sending.

Run once per mailbox that should be able to send:

    uv run python scripts/authorize_mailbox.py <short-name> <email-address>

Opens your browser to Google's consent screen, catches the redirect on a
local loopback port (no need to register a redirect URI for a "Desktop
app" OAuth client -- Google allows any localhost port for that client
type), exchanges the code for tokens, encrypts the refresh token, and
inserts one row into `mailboxes`. The email address is passed in rather
than fetched from Gmail's profile endpoint -- `gmail.send` alone doesn't
grant access to `users.getProfile`, and requesting a broader scope just
to look up an address you already know isn't worth it.

Requires `docker compose up -d` + `alembic upgrade head` already run, and
GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET / TOKEN_ENCRYPTION_KEY
already set in .env.
"""

from __future__ import annotations

import os
import secrets
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import httpx
from dotenv import load_dotenv

from leadgen.db.models import Mailbox
from leadgen.db.session import session_scope
from leadgen.send.crypto import TokenCipher
from leadgen.send.oauth import build_authorization_url, exchange_code_for_tokens

REDIRECT_PORT = 8765
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/oauth2callback"


class _CallbackHandler(BaseHTTPRequestHandler):
    code: str | None = None
    state: str | None = None

    def do_GET(self) -> None:
        params = parse_qs(urlparse(self.path).query)
        _CallbackHandler.code = params.get("code", [None])[0]
        _CallbackHandler.state = params.get("state", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<html><body>Authorized -- you can close this tab.</body></html>")

    def log_message(self, format: str, *args: object) -> None:
        pass  # keep the terminal quiet


def _wait_for_callback() -> tuple[str, str | None]:
    server = HTTPServer(("localhost", REDIRECT_PORT), _CallbackHandler)
    server.handle_request()
    server.server_close()
    if not _CallbackHandler.code:
        raise SystemExit("No authorization code received -- did you deny access?")
    return _CallbackHandler.code, _CallbackHandler.state


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(
            "usage: uv run python scripts/authorize_mailbox.py <short-name> <email-address>"
        )
    mailbox_name, email_address = sys.argv[1], sys.argv[2]

    load_dotenv()
    client_id = os.environ["GOOGLE_OAUTH_CLIENT_ID"]
    client_secret = os.environ["GOOGLE_OAUTH_CLIENT_SECRET"]
    encryption_key = os.environ["TOKEN_ENCRYPTION_KEY"]

    state = secrets.token_urlsafe(16)
    url = build_authorization_url(client_id=client_id, redirect_uri=REDIRECT_URI, state=state)

    print(f"\nOpening your browser to authorize {mailbox_name!r}.")
    print("If it doesn't open automatically, visit this URL:\n")
    print(url)
    print("\nWaiting for you to approve access...")
    webbrowser.open(url)

    code, returned_state = _wait_for_callback()
    if returned_state != state:
        raise SystemExit("state mismatch on the redirect -- aborting, do not retry blindly")

    with httpx.Client(timeout=30.0) as client:
        tokens = exchange_code_for_tokens(
            client,
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=REDIRECT_URI,
            code=code,
        )
        refresh_token = tokens.get("refresh_token")
        if not refresh_token:
            raise SystemExit(
                "Google didn't return a refresh_token -- this Google account was "
                "likely already authorized for this OAuth client before, and Google "
                "only issues a refresh_token on first consent. Revoke this app's "
                "access at https://myaccount.google.com/permissions and run this "
                "script again."
            )

    encrypted = TokenCipher(encryption_key).encrypt(refresh_token)

    with session_scope() as session:
        session.add(
            Mailbox(
                name=mailbox_name,
                email_address=email_address,
                oauth_refresh_token_encrypted=encrypted,
            )
        )

    print(f"\nStored mailbox {mailbox_name!r} ({email_address}) with an encrypted refresh token.")


if __name__ == "__main__":
    main()
