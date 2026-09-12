from pathlib import Path

import yaml

from leadgen.config.loader import load_target_profile
from leadgen.config.models import BusinessTypeDef
from leadgen.enrich.qualify import is_qualified
from leadgen.enrich.signals import ContactCandidate

DENTIST = BusinessTypeDef(osm=["amenity=dentist"])


def _profile(tmp_path: Path, **qualification_overrides):
    data = {
        "name": "t",
        "business_type": "dentist",
        "location": {"mode": "radius", "center": "X", "radius_km": 10},
        "source": {"primary": "overpass"},
        "outreach": {"offer_id": "o", "sender_pool": ["s1"]},
        "qualification": {
            "require_email": True,
            "require_any_signal": ["no_online_booking", "no_contact_form"],
            "min_signal_count": 1,
            **qualification_overrides,
        },
    }
    path = tmp_path / "p.yaml"
    path.write_text(yaml.safe_dump(data))
    return load_target_profile(path, {"dentist": DENTIST})


CONTACT = [ContactCandidate(email="info@clinic.com", is_generic=True)]


def test_fails_when_email_required_but_missing(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    assert is_qualified(profile, contacts=[], signals={"no_online_booking": True}) is False


def test_passes_when_email_present_and_signal_matches(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    assert is_qualified(profile, CONTACT, {"no_online_booking": True}) is True


def test_fails_when_no_required_signal_present(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    signals = {"no_online_booking": False, "no_contact_form": False}
    assert is_qualified(profile, CONTACT, signals) is False


def test_min_signal_count_requires_multiple_matches(tmp_path: Path) -> None:
    profile = _profile(tmp_path, min_signal_count=2)
    only_one = {"no_online_booking": True, "no_contact_form": False}
    both = {"no_online_booking": True, "no_contact_form": True}
    assert is_qualified(profile, CONTACT, only_one) is False
    assert is_qualified(profile, CONTACT, both) is True


def test_no_required_signals_means_only_email_matters(tmp_path: Path) -> None:
    profile = _profile(tmp_path, require_any_signal=[], min_signal_count=0)
    assert is_qualified(profile, CONTACT, signals={}) is True


def test_require_email_false_allows_no_contacts(tmp_path: Path) -> None:
    profile = _profile(tmp_path, require_email=False)
    assert is_qualified(profile, [], {"no_online_booking": True}) is True


def test_numeric_signal_counts_when_truthy_not_by_magnitude(tmp_path: Path) -> None:
    # No threshold configured: falls back to the pre-threshold behaviour
    # (see qualify.py's module docstring) -- any non-None value counts,
    # regardless of how old/heavy.
    profile = _profile(
        tmp_path, require_any_signal=["last_content_year"], min_signal_count=1
    )
    assert is_qualified(profile, CONTACT, {"last_content_year": 2024}) is True
    assert is_qualified(profile, CONTACT, {"last_content_year": None}) is False


def test_stale_content_threshold_only_matches_below_cutoff(tmp_path: Path) -> None:
    profile = _profile(
        tmp_path,
        require_any_signal=["last_content_year"],
        min_signal_count=1,
        stale_content_before_year=2023,
    )
    assert is_qualified(profile, CONTACT, {"last_content_year": 2020}) is True
    assert is_qualified(profile, CONTACT, {"last_content_year": 2024}) is False
    assert is_qualified(profile, CONTACT, {"last_content_year": None}) is False


def test_page_weight_threshold_only_matches_above_cutoff(tmp_path: Path) -> None:
    profile = _profile(
        tmp_path,
        require_any_signal=["page_weight_mb"],
        min_signal_count=1,
        max_page_weight_mb=4.5,
    )
    assert is_qualified(profile, CONTACT, {"page_weight_mb": 6.2}) is True
    assert is_qualified(profile, CONTACT, {"page_weight_mb": 1.1}) is False
