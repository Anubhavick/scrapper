import httpx
import pytest

from leadgen.send.oauth import (
    OAuthError,
    build_authorization_url,
    exchange_code_for_tokens,
    refresh_access_token,
)


def test_build_authorization_url_contains_required_params() -> None:
    url = build_authorization_url(
        client_id="client-123",
        redirect_uri="https://example.com/callback",
        state="xyz",
    )
    assert "client_id=client-123" in url
    assert "redirect_uri=https%3A%2F%2Fexample.com%2Fcallback" in url
    assert "scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fgmail.send" in url
    assert "access_type=offline" in url
    assert "prompt=consent" in url
    assert "state=xyz" in url


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_exchange_code_for_tokens_returns_parsed_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/token")
        return httpx.Response(200, json={"access_token": "a", "refresh_token": "r", "expires_in": 3600})

    result = exchange_code_for_tokens(
        _client(handler),
        client_id="c",
        client_secret="s",
        redirect_uri="https://example.com/callback",
        code="auth-code",
    )
    assert result == {"access_token": "a", "refresh_token": "r", "expires_in": 3600}


def test_exchange_code_raises_on_error_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    with pytest.raises(OAuthError):
        exchange_code_for_tokens(
            _client(handler),
            client_id="c",
            client_secret="s",
            redirect_uri="https://example.com/callback",
            code="bad-code",
        )


def test_refresh_access_token_returns_new_access_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "new-a", "expires_in": 3600})

    result = refresh_access_token(
        _client(handler), client_id="c", client_secret="s", refresh_token="r"
    )
    assert result["access_token"] == "new-a"
