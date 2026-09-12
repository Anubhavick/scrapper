from leadgen.config.models import Filters
from leadgen.discover.filters import apply_filters
from leadgen.discover.overpass import DiscoveredBusiness


def _business(**overrides) -> DiscoveredBusiness:
    defaults = dict(
        name="Smile Dental Clinic",
        source_id="node/1",
        website_url="https://smiledental.example",
        phone="+91 124 000 0000",
    )
    defaults.update(overrides)
    return DiscoveredBusiness(**defaults)


def test_must_have_website_drops_businesses_without_one() -> None:
    businesses = [_business(website_url=None), _business()]
    result = apply_filters(businesses, Filters(must_have_website=True))
    assert len(result) == 1
    assert result[0].website_url is not None


def test_must_have_phone_drops_businesses_without_one() -> None:
    businesses = [_business(phone=None), _business()]
    result = apply_filters(businesses, Filters(must_have_phone=True))
    assert len(result) == 1
    assert result[0].phone is not None


def test_min_name_length_drops_short_names() -> None:
    businesses = [_business(name="AB"), _business(name="Full Clinic Name")]
    result = apply_filters(businesses, Filters(min_name_length=5))
    assert len(result) == 1
    assert result[0].name == "Full Clinic Name"


def test_exclude_domains_matches_normalised_domain() -> None:
    businesses = [
        _business(website_url="https://www.Practo.com/clinic"),
        _business(website_url="https://smiledental.example"),
    ]
    result = apply_filters(businesses, Filters(exclude_domains=["practo.com"]))
    assert len(result) == 1
    assert result[0].website_url == "https://smiledental.example"


def test_exclude_name_patterns_filters_regex_matches() -> None:
    businesses = [
        _business(name="Apollo Dental Care"),
        _business(name="Smile Dental Clinic"),
    ]
    result = apply_filters(
        businesses, Filters(exclude_name_patterns=["(?i)apollo"])
    )
    assert len(result) == 1
    assert result[0].name == "Smile Dental Clinic"


def test_no_filters_keeps_everything() -> None:
    businesses = [_business(), _business(website_url=None, phone=None)]
    result = apply_filters(businesses, Filters())
    assert len(result) == 2


def test_business_with_unparseable_website_not_excluded_by_domain_filter() -> None:
    businesses = [_business(website_url="not a url!!")]
    result = apply_filters(businesses, Filters(exclude_domains=["practo.com"]))
    assert len(result) == 1
