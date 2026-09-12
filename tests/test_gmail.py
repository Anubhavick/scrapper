import base64
from email import message_from_bytes

import httpx
import pytest

from leadgen.send.gmail import GmailSendError, build_raw_message, send_message


def test_build_raw_message_is_base64url_mime() -> None:
    raw = build_raw_message(
        from_addr="sales@example.com",
        to_addr="lead@clinic.example",
        subject="hello",
        body="body text",
    )
    mime = message_from_bytes(base64.urlsafe_b64decode(raw.encode("ascii")))
    assert mime["From"] == "sales@example.com"
    assert mime["To"] == "lead@clinic.example"
    assert mime["Subject"] == "hello"
    assert mime.get_payload(decode=True).decode("utf-8") == "body text"


def test_build_raw_message_encodes_non_ascii_subject() -> None:
    # Regression test: a plain `mime["Subject"] = subject` raises
    # UnicodeEncodeError at .as_bytes() time under the default compat32
    # policy the moment the subject has a non-ASCII character.
    from email.header import decode_header

    original_subject = "Café Dentaire — missed calls?"
    raw = build_raw_message(
        from_addr="sales@example.com",
        to_addr="lead@clinic.example",
        subject=original_subject,
        body="body text",
    )
    mime = message_from_bytes(base64.urlsafe_b64decode(raw.encode("ascii")))
    decoded_parts = decode_header(mime["Subject"])
    decoded_subject = "".join(
        chunk.decode(charset or "ascii") if isinstance(chunk, bytes) else chunk
        for chunk, charset in decoded_parts
    )
    assert decoded_subject == original_subject


def test_send_message_returns_response_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer token-123"
        assert request.url.path.endswith("/messages/send")
        return httpx.Response(200, json={"id": "msg1", "threadId": "thread1"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = send_message(client, access_token="token-123", raw_message="rawdata")
    assert result == {"id": "msg1", "threadId": "thread1"}


def test_send_message_raises_on_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "insufficientPermissions"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(GmailSendError):
        send_message(client, access_token="token-123", raw_message="rawdata")
