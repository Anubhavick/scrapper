from leadgen.enrich.tags import compute_tags


def test_no_website_tag() -> None:
    assert "no-website" in compute_tags(has_website=False, signals={})


def test_no_tag_when_website_present_and_no_signals() -> None:
    assert compute_tags(has_website=True, signals={}) == []


def test_boolean_signals_produce_expected_tags() -> None:
    tags = compute_tags(
        has_website=True,
        signals={
            "no_https": True,
            "no_mobile_viewport": True,
            "no_contact_form": True,
            "no_online_booking": True,
            "has_whatsapp_link": True,
        },
    )
    assert tags == ["no-https", "no-mobile", "no-contact-form", "no-booking", "has-whatsapp"]


def test_false_boolean_signals_produce_no_tag() -> None:
    tags = compute_tags(has_website=True, signals={"no_https": False, "has_whatsapp_link": False})
    assert tags == []


def test_site_platform_becomes_slugged_tag() -> None:
    tags = compute_tags(has_website=True, signals={"site_platform": "Word Press"})
    assert tags == ["platform-word-press"]


def test_last_content_year_and_page_weight_are_surfaced_not_judged() -> None:
    tags = compute_tags(
        has_website=True, signals={"last_content_year": 2019, "page_weight_mb": 3.2}
    )
    assert tags == ["content-year-2019", "page-weight-3.2mb"]


def test_none_values_produce_no_tag() -> None:
    tags = compute_tags(
        has_website=True,
        signals={"site_platform": None, "last_content_year": None, "page_weight_mb": None},
    )
    assert tags == []
