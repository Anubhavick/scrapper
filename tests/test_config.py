from pathlib import Path

import pytest
import yaml

from leadgen.config.loader import (
    ConfigError,
    load_business_types,
    load_offer,
    load_offers,
    load_target_profile,
    load_target_profiles,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
BUSINESS_TYPES_PATH = REPO_ROOT / "config" / "business_types.yaml"
OFFERS_DIR = REPO_ROOT / "config" / "offers"
TARGETS_DIR = REPO_ROOT / "targets"


def write_yaml(path: Path, data: dict) -> Path:
    path.write_text(yaml.safe_dump(data))
    return path


# ---- real example files load cleanly ----


def test_load_business_types_real_file() -> None:
    types = load_business_types(BUSINESS_TYPES_PATH)
    assert "dentist" in types
    assert "amenity=dentist" in types["dentist"].osm
    assert types["dentist"].places_types == ["dentist"]


def test_load_offers_real_dir() -> None:
    offers = load_offers(OFFERS_DIR)
    assert "appointment-automation" in offers
    assert offers["appointment-automation"].cta


def test_load_target_profiles_real_dir() -> None:
    business_types = load_business_types(BUSINESS_TYPES_PATH)
    offers = load_offers(OFFERS_DIR)
    profiles = load_target_profiles(TARGETS_DIR, business_types, offers)
    # Derived from the directory rather than hardcoded: this test exists to
    # prove every profile the repo actually ships still loads, so adding a
    # real target profile shouldn't fail it.
    assert len(profiles) == len(list(TARGETS_DIR.glob("*.yaml")))
    profile = next(p for p in profiles if p.name == "dentists-gurugram")
    assert profile.location.mode == "radius"
    assert profile.location.radius_km == 15
    assert profile.outreach.offer_id == "appointment-automation"


# ---- location discriminated union ----


def _minimal_profile(**overrides) -> dict:
    base = {
        "name": "t",
        "business_type": "dentist",
        "location": {"mode": "radius", "center": "X", "radius_km": 10},
        "source": {"primary": "overpass"},
        "outreach": {"offer_id": "o", "sender_pool": ["s1"]},
        "legal_region": "us",
    }
    base.update(overrides)
    return base


def test_location_bbox_mode(tmp_path: Path) -> None:
    data = _minimal_profile(
        location={"mode": "bbox", "bbox": [28.4, 76.9, 28.5, 77.1]}
    )
    path = write_yaml(tmp_path / "p.yaml", data)
    profile = load_target_profile(path, {"dentist": _dummy_business_type()})
    assert profile.location.mode == "bbox"


def test_location_radius_missing_field_raises(tmp_path: Path) -> None:
    data = _minimal_profile(location={"mode": "radius", "center": "X"})
    path = write_yaml(tmp_path / "p.yaml", data)
    with pytest.raises(ConfigError, match="radius_km"):
        load_target_profile(path, {"dentist": _dummy_business_type()})


def test_location_bbox_inverted_raises(tmp_path: Path) -> None:
    data = _minimal_profile(
        location={"mode": "bbox", "bbox": [28.5, 76.9, 28.4, 77.1]}
    )
    path = write_yaml(tmp_path / "p.yaml", data)
    with pytest.raises(ConfigError):
        load_target_profile(path, {"dentist": _dummy_business_type()})


def test_location_unknown_mode_raises(tmp_path: Path) -> None:
    data = _minimal_profile(location={"mode": "planet", "center": "X"})
    path = write_yaml(tmp_path / "p.yaml", data)
    with pytest.raises(ConfigError):
        load_target_profile(path, {"dentist": _dummy_business_type()})


# ---- clear errors on bad YAML ----


def test_malformed_yaml_syntax_raises_config_error(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("name: [unterminated\n  bracket: true")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_target_profile(path, {})


def test_empty_file_raises_config_error(tmp_path: Path) -> None:
    path = tmp_path / "empty.yaml"
    path.write_text("")
    with pytest.raises(ConfigError, match="empty"):
        load_target_profile(path, {})


def test_missing_file_raises_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_target_profile(tmp_path / "missing.yaml", {})


def test_unknown_top_level_key_raises(tmp_path: Path) -> None:
    data = _minimal_profile()
    data["typo_field"] = "oops"
    path = write_yaml(tmp_path / "p.yaml", data)
    with pytest.raises(ConfigError):
        load_target_profile(path, {"dentist": _dummy_business_type()})


def test_unknown_business_type_raises(tmp_path: Path) -> None:
    path = write_yaml(tmp_path / "p.yaml", _minimal_profile())
    with pytest.raises(ConfigError, match="unknown business_type"):
        load_target_profile(path, {"gym": _dummy_business_type()})


def test_unknown_offer_id_raises(tmp_path: Path) -> None:
    path = write_yaml(tmp_path / "p.yaml", _minimal_profile())
    with pytest.raises(ConfigError, match="unknown offer_id"):
        load_target_profile(
            path,
            {"dentist": _dummy_business_type()},
            offers={},
        )


def test_invalid_regex_in_filters_raises(tmp_path: Path) -> None:
    data = _minimal_profile(filters={"exclude_name_patterns": ["(unclosed"]})
    path = write_yaml(tmp_path / "p.yaml", data)
    with pytest.raises(ConfigError, match="invalid regex"):
        load_target_profile(path, {"dentist": _dummy_business_type()})


def test_unknown_signal_in_qualification_raises(tmp_path: Path) -> None:
    data = _minimal_profile(qualification={"require_any_signal": ["has_a_typo"]})
    path = write_yaml(tmp_path / "p.yaml", data)
    with pytest.raises(ConfigError, match="unknown signal"):
        load_target_profile(path, {"dentist": _dummy_business_type()})


def test_empty_sender_pool_raises(tmp_path: Path) -> None:
    data = _minimal_profile(outreach={"offer_id": "o", "sender_pool": []})
    path = write_yaml(tmp_path / "p.yaml", data)
    with pytest.raises(ConfigError):
        load_target_profile(path, {"dentist": _dummy_business_type()})


# ---- legal_region / GDPR opt-in gate ----


def test_missing_legal_region_raises(tmp_path: Path) -> None:
    data = _minimal_profile()
    del data["legal_region"]
    path = write_yaml(tmp_path / "p.yaml", data)
    with pytest.raises(ConfigError, match="legal_region"):
        load_target_profile(path, {"dentist": _dummy_business_type()})


def test_eu_uk_without_requires_opt_in_raises(tmp_path: Path) -> None:
    data = _minimal_profile(legal_region="eu_uk")
    path = write_yaml(tmp_path / "p.yaml", data)
    with pytest.raises(ConfigError, match="requires_opt_in"):
        load_target_profile(path, {"dentist": _dummy_business_type()})


def test_eu_uk_with_requires_opt_in_loads(tmp_path: Path) -> None:
    data = _minimal_profile(legal_region="eu_uk", requires_opt_in=True)
    path = write_yaml(tmp_path / "p.yaml", data)
    profile = load_target_profile(path, {"dentist": _dummy_business_type()})
    assert profile.legal_region == "eu_uk"
    assert profile.requires_opt_in is True


def test_us_profile_defaults_requires_opt_in_false(tmp_path: Path) -> None:
    data = _minimal_profile(legal_region="us")
    path = write_yaml(tmp_path / "p.yaml", data)
    profile = load_target_profile(path, {"dentist": _dummy_business_type()})
    assert profile.requires_opt_in is False


def test_unknown_legal_region_raises(tmp_path: Path) -> None:
    data = _minimal_profile(legal_region="mars")
    path = write_yaml(tmp_path / "p.yaml", data)
    with pytest.raises(ConfigError):
        load_target_profile(path, {"dentist": _dummy_business_type()})


# ---- business types ----


def test_business_type_invalid_osm_tag_raises(tmp_path: Path) -> None:
    path = write_yaml(
        tmp_path / "business_types.yaml",
        {"dentist": {"osm": ["not-a-kv-pair"]}},
    )
    with pytest.raises(ConfigError, match="key=value"):
        load_business_types(path)


# ---- offers ----


def test_offer_id_must_match_filename(tmp_path: Path) -> None:
    path = write_yaml(
        tmp_path / "wrong-name.yaml",
        {
            "id": "appointment-automation",
            "subject_templates": ["s"],
            "body_template": "t.txt",
            "cta": "c",
        },
    )
    with pytest.raises(ConfigError, match="expected"):
        load_offer(path)


def test_offer_unknown_signal_raises(tmp_path: Path) -> None:
    path = write_yaml(
        tmp_path / "o.yaml",
        {
            "id": "o",
            "subject_templates": ["s"],
            "body_template": "t.txt",
            "cta": "c",
            "relevant_signals": ["not_a_real_signal"],
        },
    )
    with pytest.raises(ConfigError, match="unknown signal"):
        load_offer(path)


def _dummy_business_type():
    from leadgen.config.models import BusinessTypeDef

    return BusinessTypeDef()
