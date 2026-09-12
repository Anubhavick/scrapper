from leadgen.send.suppression import is_suppressed


def test_suppressed_by_exact_email() -> None:
    assert is_suppressed(
        email="Lead@Clinic.example",
        suppressed_emails={"lead@clinic.example"},
        suppressed_domains=set(),
    ) is True


def test_suppressed_by_domain_even_if_email_not_listed() -> None:
    assert is_suppressed(
        email="other@clinic.example",
        suppressed_emails=set(),
        suppressed_domains={"clinic.example"},
    ) is True


def test_not_suppressed() -> None:
    assert is_suppressed(
        email="lead@other.example",
        suppressed_emails={"lead@clinic.example"},
        suppressed_domains={"clinic.example"},
    ) is False


def test_www_domain_suppression_matches_normalised_form() -> None:
    assert is_suppressed(
        email="lead@www.clinic.example",
        suppressed_emails=set(),
        suppressed_domains={"clinic.example"},
    ) is True
