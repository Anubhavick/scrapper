"""One-off smoke test: send a real email through a stored mailbox, end to
end (refresh token -> access token -> Gmail API send).

This is NOT the production send path -- it deliberately bypasses
send/queue.py's suppression + daily-cap checks, because sending one test
message to yourself isn't a real campaign send and there's no `messages`
row to attach it to yet. Never reuse this pattern for actual leads --
always go through send.queue.check_sendable() first for that.

Usage:
    uv run python scripts/send_test_email.py <mailbox-name> <to-address>
"""

from __future__ import annotations

import os
import sys

import httpx
from dotenv import load_dotenv
from sqlalchemy import select

from leadgen.db.models import Mailbox
from leadgen.db.session import session_scope
from leadgen.send.crypto import TokenCipher
from leadgen.send.gmail import build_raw_message, send_message
from leadgen.send.oauth import refresh_access_token


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: uv run python scripts/send_test_email.py <mailbox-name> <to-address>")
    mailbox_name, to_address = sys.argv[1], sys.argv[2]

    load_dotenv()
    client_id = os.environ["GOOGLE_OAUTH_CLIENT_ID"]
    client_secret = os.environ["GOOGLE_OAUTH_CLIENT_SECRET"]
    encryption_key = os.environ["TOKEN_ENCRYPTION_KEY"]

    with session_scope() as session:
        mailbox = session.execute(
            select(Mailbox).where(Mailbox.name == mailbox_name)
        ).scalar_one_or_none()
        if mailbox is None:
            raise SystemExit(
                f"no mailbox named {mailbox_name!r} -- run scripts/authorize_mailbox.py first"
            )
        refresh_token = TokenCipher(encryption_key).decrypt(mailbox.oauth_refresh_token_encrypted)
        from_address = mailbox.email_address

    with httpx.Client(timeout=30.0) as client:
        tokens = refresh_access_token(
            client, client_id=client_id, client_secret=client_secret, refresh_token=refresh_token
        )
        raw = build_raw_message(
            from_addr=from_address,
            to_addr=to_address,
            subject="leadgen: test send",
            body=(
                "This confirms the OAuth -> Gmail API send path works end to end.\n\n"
                f"Sent from mailbox: {from_address}"
            ),
        )
        result = send_message(client, access_token=tokens["access_token"], raw_message=raw)

    print(f"Sent. Gmail message id={result['id']} thread id={result['threadId']}")


if __name__ == "__main__":
    main()
