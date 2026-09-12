import httpx

from leadgen.enrich.robots import RobotsChecker

USER_AGENT = "test-bot/1.0"


def _checker(handler) -> RobotsChecker:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return RobotsChecker(client, USER_AGENT)


def test_disallowed_path_is_blocked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="User-agent: *\nDisallow: /admin\n")

    checker = _checker(handler)
    assert checker.can_fetch("https://clinic.example/admin") is False
    assert checker.can_fetch("https://clinic.example/contact") is True


def test_missing_robots_txt_allows_everything() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    checker = _checker(handler)
    assert checker.can_fetch("https://clinic.example/anything") is True


def test_forbidden_robots_txt_disallows_everything() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    checker = _checker(handler)
    assert checker.can_fetch("https://clinic.example/") is False


def test_unreachable_robots_txt_fails_open() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    checker = _checker(handler)
    assert checker.can_fetch("https://clinic.example/") is True


def test_robots_txt_fetched_once_per_origin() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url)
        return httpx.Response(200, text="User-agent: *\nAllow: /\n")

    checker = _checker(handler)
    checker.can_fetch("https://clinic.example/a")
    checker.can_fetch("https://clinic.example/b")
    checker.can_fetch("https://clinic.example/c")
    assert len(calls) == 1
