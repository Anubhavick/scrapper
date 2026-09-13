from datetime import datetime, timezone

from leadgen.enrich.crawler import PageFetch
from leadgen.enrich.signals import compute_signals, extract_emails


def _page(url: str, html: str) -> PageFetch:
    return PageFetch(
        url=url,
        status_code=200,
        html=html,
        content_length_bytes=len(html.encode()),
        fetched_at=datetime.now(timezone.utc),
    )


# ---- extract_emails ----


def test_extract_emails_from_text_and_mailto() -> None:
    html = """
    <body>
      <p>Reach us at Info@Example-Dental.com or call us.</p>
      <a href="mailto:appointments@example-dental.com?subject=Hi">Book</a>
    </body>
    """
    candidates = extract_emails(html)
    emails = {c.email for c in candidates}
    assert emails == {"info@example-dental.com", "appointments@example-dental.com"}


def test_extract_emails_flags_generic_local_parts() -> None:
    html = "<p>info@clinic.com and dr.sharma@clinic.com</p>"
    candidates = {c.email: c.is_generic for c in extract_emails(html)}
    assert candidates["info@clinic.com"] is True
    assert candidates["dr.sharma@clinic.com"] is False


def test_extract_emails_filters_placeholder_domains() -> None:
    html = "<p>Contact yourname@example.com or real@clinic.com</p>"
    emails = {c.email for c in extract_emails(html)}
    assert emails == {"real@clinic.com"}


def test_extract_emails_no_emails_present() -> None:
    assert extract_emails("<p>No contact info here.</p>") == []


def test_extract_emails_does_not_guess_from_domain() -> None:
    # A page that never mentions an address should never produce one,
    # even though a plausible guess (info@ + the site's own domain)
    # would be trivial to construct.
    html = "<html><head><title>Clinic</title></head><body>Welcome!</body></html>"
    assert extract_emails(html) == []


# ---- compute_signals ----


def test_signals_https_and_viewport() -> None:
    homepage = _page(
        "https://clinic.example/",
        '<html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body></body></html>',
    )
    signals = compute_signals([homepage])
    assert signals["no_https"] is False
    assert signals["no_mobile_viewport"] is False


def test_signals_http_and_missing_viewport() -> None:
    homepage = _page("http://clinic.example/", "<html><body>No meta here</body></html>")
    signals = compute_signals([homepage])
    assert signals["no_https"] is True
    assert signals["no_mobile_viewport"] is True


def test_signals_contact_form_detected_on_any_page() -> None:
    homepage = _page("https://clinic.example/", "<html><body>Home</body></html>")
    contact_page = _page(
        "https://clinic.example/contact",
        '<html><body><form><input name="email"></form></body></html>',
    )
    signals = compute_signals([homepage, contact_page])
    assert signals["no_contact_form"] is False


def test_signals_no_contact_form_when_none_present() -> None:
    homepage = _page("https://clinic.example/", "<html><body>Home</body></html>")
    signals = compute_signals([homepage])
    assert signals["no_contact_form"] is True


def test_signals_online_booking_detected() -> None:
    homepage = _page(
        "https://clinic.example/",
        "<html><body><a href='/book'>Book an appointment online</a></body></html>",
    )
    signals = compute_signals([homepage])
    assert signals["no_online_booking"] is False


def test_signals_platform_detection() -> None:
    homepage = _page(
        "https://clinic.example/",
        '<html><body><link rel="stylesheet" href="/wp-content/themes/x/style.css"></body></html>',
    )
    signals = compute_signals([homepage])
    assert signals["site_platform"] == "wordpress"


def test_signals_platform_none_when_undetected() -> None:
    homepage = _page("https://clinic.example/", "<html><body>plain site</body></html>")
    signals = compute_signals([homepage])
    assert signals["site_platform"] is None


def test_signals_last_content_year_takes_max_across_pages() -> None:
    homepage = _page("https://clinic.example/", "<html><body>Est. 2015</body></html>")
    other_page = _page(
        "https://clinic.example/about",
        "<html><body>Voted best clinic in 2019.</body></html>",
    )
    signals = compute_signals([homepage, other_page])
    assert signals["last_content_year"] == 2019


def test_signals_last_content_year_ignores_copyright_footer_year() -> None:
    # A stale site whose only year anywhere is an auto-generated copyright
    # footer must not be reported as "fresh" just because the footer year
    # is current -- docs/07's original bug report.
    homepage = _page(
        "https://clinic.example/",
        "<html><body>Welcome to our clinic. &copy; 2026 Clinic. All rights reserved.</body></html>",
    )
    signals = compute_signals([homepage])
    assert signals["last_content_year"] is None


def test_signals_last_content_year_prefers_real_content_over_copyright_footer() -> None:
    homepage = _page(
        "https://clinic.example/",
        "<html><body>Latest news from 2019.</body></html>",
    )
    footer_page = _page(
        "https://clinic.example/about",
        "<html><body>&copy; 2026 Clinic. All rights reserved.</body></html>",
    )
    signals = compute_signals([homepage, footer_page])
    assert signals["last_content_year"] == 2019


def test_signals_last_content_year_ignores_copyright_year_range() -> None:
    homepage = _page(
        "https://clinic.example/",
        "<html><body>(c) 2015-2026 Clinic. All rights reserved.</body></html>",
    )
    signals = compute_signals([homepage])
    assert signals["last_content_year"] is None


def test_signals_last_content_year_none_when_no_year_mentioned() -> None:
    homepage = _page("https://clinic.example/", "<html><body>Welcome!</body></html>")
    signals = compute_signals([homepage])
    assert signals["last_content_year"] is None


def test_signals_page_weight_sums_bytes() -> None:
    homepage = _page("https://clinic.example/", "a" * 1000)
    other = _page("https://clinic.example/about", "b" * 2000)
    signals = compute_signals([homepage, other])
    assert signals["page_weight_mb"] == round(3000 / (1024 * 1024), 3)


def test_signals_whatsapp_link_detected() -> None:
    homepage = _page(
        "https://clinic.example/",
        '<html><body><a href="https://wa.me/911234567890">Chat</a></body></html>',
    )
    signals = compute_signals([homepage])
    assert signals["has_whatsapp_link"] is True


def test_signals_empty_pages_returns_empty_dict() -> None:
    assert compute_signals([]) == {}
