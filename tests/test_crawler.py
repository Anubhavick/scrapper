import httpx

from leadgen.enrich.crawler import crawl_business, fetch_page
from leadgen.enrich.robots import RobotsChecker

USER_AGENT = "test-bot/1.0"

PAGES = {
    "/": '<html><head><meta name="viewport" content="width=device-width"></head>'
    '<body>Home. Contact info@clinic.example</body></html>',
    "/contact": '<html><body><form><input name="email"></form>'
    "<a href='mailto:appointments@clinic.example'>Email us</a></body></html>",
    "/about": "<html><body>&copy; 2022 Clinic</body></html>",
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
    assert result.signals["last_content_year"] == 2022


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

    # 3 monotonic() calls total for a 2-page crawl: one after page 1
    # (records last_request_at), two for page 2 (the remaining-time
    # check, then recording its own last_request_at).
    clock = iter([0.0, 0.1, 0.1])
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
