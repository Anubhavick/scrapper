import pytest

from leadgen.util.domains import normalise_domain


@pytest.mark.parametrize(
    "value,expected",
    [
        # www stripping
        ("www.example.com", "example.com"),
        ("http://www.example.com", "example.com"),
        # uppercase
        ("EXAMPLE.com", "example.com"),
        ("WWW.EXAMPLE.COM", "example.com"),
        # trailing dot
        ("example.com.", "example.com"),
        ("www.example.com.", "example.com"),
        # paths
        ("example.com/contact", "example.com"),
        ("https://example.com/contact/us", "example.com"),
        ("www.example.com/", "example.com"),
        # query strings
        ("example.com?utm_source=x", "example.com"),
        ("https://example.com/page?a=1&b=2", "example.com"),
        # port / userinfo, bonus robustness
        ("example.com:8080/path", "example.com"),
        # subdomains that are NOT www must be preserved
        ("shop.example.com", "shop.example.com"),
        ("https://blog.news.example.com/post", "blog.news.example.com"),
        ("api.example.co.uk", "api.example.co.uk"),
    ],
)
def test_normalise_domain_valid_cases(value: str, expected: str) -> None:
    assert normalise_domain(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        ("café.fr", "xn--caf-dma.fr"),
        ("www.café.fr", "xn--caf-dma.fr"),
        ("münchen.de", "xn--mnchen-3ya.de"),
        ("xn--caf-dma.fr", "xn--caf-dma.fr"),  # already-encoded input is idempotent
    ],
)
def test_normalise_domain_idn(value: str, expected: str) -> None:
    assert normalise_domain(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "not a domain at all!!",
        "http://",
        "://",
        "localhost",
        "just-one-label",
        "http:///no-host-here",
        "..",
        "www.",
    ],
)
def test_normalise_domain_invalid_input_raises(value: str) -> None:
    with pytest.raises(ValueError):
        normalise_domain(value)


def test_normalise_domain_rejects_non_string() -> None:
    with pytest.raises(ValueError):
        normalise_domain(None)  # type: ignore[arg-type]


def test_normalise_domain_is_idempotent() -> None:
    once = normalise_domain("https://www.Example.COM/contact?ref=1")
    twice = normalise_domain(once)
    assert once == twice == "example.com"
