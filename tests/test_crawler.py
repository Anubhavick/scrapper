import socket
import ssl

import httpx
import pytest

from leadgen.enrich.crawler import crawl_business, fetch_page
from leadgen.enrich.robots import RobotsChecker

USER_AGENT = "test-bot/1.0"

PAGES = {
    "/": '<html><head><meta name="viewport" content="width=device-width"></head>'
    '<body>Home. Contact info@clinic.example</body></html>',
    "/contact": '<html><body><form><input name="email"></form>'
    "<a href='mailto:appointments@clinic.example'>Email us</a></body></html>",
    "/about": "<html><body>Established 2019. &copy; 2022 Clinic</body></html>",
}


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path in PAGES:
        return httpx.Response(200, text=PAGES[path])
    if path == "/robots.txt":
        return httpx.Response(404)
    return httpx.Response(404)


def _client() -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(_handler))


def test_fetch_page_returns_none_on_404() -> None:
    result = fetch_page("https://clinic.example/missing", _client(), USER_AGENT)
    assert result is None


def test_fetch_page_returns_page_on_success() -> None:
    result = fetch_page("https://clinic.example/", _client(), USER_AGENT)
    assert result is not None
    assert result.status_code == 200
    assert "info@clinic.example" in result.html


def test_crawl_business_aggregates_contacts_and_signals(monkeypatch) -> None:
    monkeypatch.setattr("time.sleep", lambda s: None)  # keep the test instant

    client = _client()
    robots = RobotsChecker(client, USER_AGENT)
    result = crawl_business(
        website_url="https://clinic.example/",
        crawl_pages=["/", "/contact", "/about"],
        max_pages=8,
        client=client,
        robots=robots,
        user_agent=USER_AGENT,
    )

    assert len(result.pages) == 3
    emails = {c.email for c in result.contacts}
    assert emails == {"info@clinic.example", "appointments@clinic.example"}
    assert result.signals["no_contact_form"] is False
    assert result.signals["last_content_year"] == 2019


def test_crawl_business_respects_max_pages(monkeypatch) -> None:
    monkeypatch.setattr("time.sleep", lambda s: None)

    client = _client()
    robots = RobotsChecker(client, USER_AGENT)
    result = crawl_business(
        website_url="https://clinic.example/",
        crawl_pages=["/", "/contact", "/about"],
        max_pages=1,
        client=client,
        robots=robots,
        user_agent=USER_AGENT,
    )
    assert len(result.pages) == 1
    assert result.pages[0].url == "https://clinic.example/"


def test_crawl_business_skips_disallowed_paths(monkeypatch) -> None:
    monkeypatch.setattr("time.sleep", lambda s: None)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /contact\n")
        return _handler(request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    robots = RobotsChecker(client, USER_AGENT)
    result = crawl_business(
        website_url="https://clinic.example/",
        crawl_pages=["/", "/contact"],
        max_pages=8,
        client=client,
        robots=robots,
        user_agent=USER_AGENT,
    )
    fetched_urls = {p.url for p in result.pages}
    assert fetched_urls == {"https://clinic.example/"}


def test_crawl_business_rate_limits_between_pages(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))

    # 5 monotonic() calls total for a 2-page crawl: the initial deadline
    # anchor, then per page one "now" read (reused for both the deadline
    # check and the rate-limit math) plus one to record last_request_at.
    clock = iter([0.0, 0.0, 0.0, 0.1, 0.1])
    monkeypatch.setattr("time.monotonic", lambda: next(clock))

    client = _client()
    robots = RobotsChecker(client, USER_AGENT)
    crawl_business(
        website_url="https://clinic.example/",
        crawl_pages=["/", "/contact"],
        max_pages=8,
        client=client,
        robots=robots,
        user_agent=USER_AGENT,
    )
    assert len(sleeps) == 1
    assert sleeps[0] == 0.9


def test_crawl_business_stops_at_host_deadline(monkeypatch) -> None:
    monkeypatch.setattr("time.sleep", lambda s: None)

    client = _client()
    robots = RobotsChecker(client, USER_AGENT)
    result = crawl_business(
        website_url="https://clinic.example/",
        crawl_pages=["/", "/contact", "/about"],
        max_pages=8,
        client=client,
        robots=robots,
        user_agent=USER_AGENT,
        host_deadline_seconds=0.0,  # already expired before the first page
    )
    assert result.pages == []
    assert result.errors == ["error-timeout"]


@pytest.mark.parametrize(
    ("exc", "expected_tag"),
    [
        (httpx.ConnectTimeout("timed out"), "error-timeout"),
        (httpx.ReadTimeout("timed out"), "error-timeout"),
        (httpx.ConnectError("dns", request=None), "error-dns"),
        (httpx.ConnectError("ssl", request=None), "error-ssl"),
        (httpx.ConnectError("refused", request=None), "error-connection"),
        (httpx.RemoteProtocolError("dropped"), "error-connection"),
    ],
)
def test_fetch_page_classifies_network_failures(monkeypatch, exc, expected_tag) -> None:
    if expected_tag == "error-dns":
        exc.__cause__ = socket.gaierror("Name or service not known")
    elif expected_tag == "error-ssl":
        exc.__cause__ = ssl.SSLError("certificate verify failed")

    def raising_get(*args, **kwargs):
        raise exc

    client = _client()
    monkeypatch.setattr(client, "get", raising_get)

    tags: list[str] = []
    result = fetch_page("https://clinic.example/", client, USER_AGENT, on_error=tags.append)
    assert result is None
    assert tags == [expected_tag]
