"""Gmail API message construction and sending.

Only `users.messages.send` is used — this system never reads a mailbox
(stage 7's bounce/reply handling is unbuilt; when it exists it should use
`gmail.readonly`/`gmail.modify`, which are *restricted* scopes needing a
CASA review, deliberately not requested yet).
"""

from __future__ import annotations

import base64
from email.header import Header
from email.mime.text import MIMEText

import httpx

GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"

__all__ = ["GmailSendError", "build_raw_message", "send_message"]


class GmailSendError(Exception):
    pass


def build_raw_message(*, from_addr: str, to_addr: str, subject: str, body: str) -> str:
    """Build a base64url-encoded RFC 2822 message, as the Gmail API's
    `raw` field requires.

    `MIMEText(body, "plain", "utf-8")` already encodes the body correctly,
    but a plain `mime["Subject"] = subject` does not: this module's
    `Message` uses the legacy `compat32` policy, which serialises headers
    assuming ASCII and raises `UnicodeEncodeError` at `.as_bytes()` time
    the moment a business name (routine outside English-only markets)
    contains a non-ASCII character. Wrapping in `email.header.Header`
    applies RFC 2047 encoded-word encoding only when actually needed,
    so plain-ASCII subjects are untouched."""
    mime = MIMEText(body, "plain", "utf-8")
    mime["From"] = from_addr
    mime["To"] = to_addr
    try:
        subject.encode("ascii")
        mime["Subject"] = subject
    except UnicodeEncodeError:
        mime["Subject"] = Header(subject, "utf-8")
    return base64.urlsafe_b64encode(mime.as_bytes()).decode("ascii")


def send_message(client: httpx.Client, *, access_token: str, raw_message: str) -> dict:
    """Returns the Gmail API response: {id, threadId, labelIds}."""
    response = client.post(
        GMAIL_SEND_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        json={"raw": raw_message},
    )
    if response.status_code >= 400:
        raise GmailSendError(f"Gmail send failed with {response.status_code}: {response.text}")
    return response.json()
