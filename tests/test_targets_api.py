from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from leadgen.api.review import app
import leadgen.api.targets as targets_mod


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test gets its own targets/ + business_types.yaml + offers/,
    so tests can't collide with (or pollute) the real repo's config."""
    targets_dir = tmp_path / "targets"
    targets_dir.mkdir()
    monkeypatch.setattr(targets_mod, "TARGETS_DIR", targets_dir)

    business_types_path = tmp_path / "business_types.yaml"
    business_types_path.write_text(
        "dentist:\n  osm: [amenity=dentist]\n  places_types: [dentist]\n  keywords: [dentist]\n"
        "gym:\n  osm: [leisure=fitness_centre]\n  places_types: [gym]\n  keywords: [gym]\n"
    )
    monkeypatch.setattr(targets_mod, "BUSINESS_TYPES_PATH", business_types_path)

    offers_dir = tmp_path / "offers"
    offers_dir.mkdir()
    (offers_dir / "appointment-automation.yaml").write_text(
        "id: appointment-automation\n"
        "subject_templates: ['Quick idea for {{business_name}}']\n"
        "body_template: 'Hi'\n"
        "cta: 'Reply if interested'\n"
    )
    monkeypatch.setattr(targets_mod, "OFFERS_DIR", offers_dir)


def _write_target(name: str, **overrides) -> Path:
    data = {
        "name": name,
        "business_type": "dentist",
        "location": {"mode": "radius", "center": "Austin, Texas, USA", "radius_km": 15},
        "source": {"primary": "overpass"},
        "filters": {"exclude_domains": ["touchto.io"]},
        "outreach": {"offer_id": "appointment-automation", "sender_pool": ["sales1"]},
    }
    data.update(overrides)
    path = targets_mod.TARGETS_DIR / f"{name}.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


VALID_FORM = {
    "name": "gyms-denver-co",
    "enabled": "on",
    "business_type": "gym",
    "location_mode": "radius",
    "center": "Denver, Colorado, USA",
    "radius_km": "10",
    "source_primary": "overpass",
    "max_results": "500",
    "min_name_length": "0",
    "crawl_pages": "/, /contact",
    "max_pages": "8",
    "require_email": "on",
    "min_signal_count": "0",
    "offer_id": "appointment-automation",
    "sender_pool": "sales1",
    "daily_cap_per_mailbox": "40",
}


def test_list_targets_shows_existing_profiles(client: TestClient) -> None:
    _write_target("dentists-austin-tx")

    resp = client.get("/targets")

    assert resp.status_code == 200
    assert "dentists-austin-tx" in resp.text
    assert "radius: Austin, Texas, USA (15.0 km)" in resp.text


def test_list_targets_shows_invalid_profile_without_crashing(client: TestClient) -> None:
    _write_target("broken", business_type="not-a-real-type")

    resp = client.get("/targets")

    assert resp.status_code == 200
    assert "Invalid" in resp.text
    assert "broken" in resp.text


def test_new_target_form_renders_defaults(client: TestClient) -> None:
    resp = client.get("/targets/new")

    assert resp.status_code == 200
    assert 'name="name"' in resp.text
    assert "dentist" in resp.text  # from business_types dropdown
    assert "appointment-automation" in resp.text  # from offers dropdown


def test_new_target_form_prefills_from_existing_via_duplicate(client: TestClient) -> None:
    _write_target("dentists-austin-tx")

    resp = client.get("/targets/new", params={"from": "dentists-austin-tx"})

    assert resp.status_code == 200
    assert 'value="dentists-austin-tx-copy"' in resp.text
    assert "touchto.io" in resp.text  # carried over from filters.exclude_domains


def test_create_target_writes_valid_yaml_and_redirects(client: TestClient) -> None:
    resp = client.post("/targets", data=VALID_FORM, follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"] == "/targets/gyms-denver-co?created=1"

    written = targets_mod.TARGETS_DIR / "gyms-denver-co.yaml"
    assert written.is_file()
    data = yaml.safe_load(written.read_text())
    assert data["name"] == "gyms-denver-co"
    assert data["business_type"] == "gym"
    assert data["location"] == {"mode": "radius", "center": "Denver, Colorado, USA", "radius_km": 10.0}
    assert data["outreach"]["sender_pool"] == ["sales1"]


def test_create_target_rejects_duplicate_name(client: TestClient) -> None:
    _write_target("gyms-denver-co")

    resp = client.post("/targets", data=VALID_FORM)

    assert resp.status_code == 200
    assert "already exists" in resp.text
    # the pre-existing file must be untouched, not silently overwritten
    data = yaml.safe_load((targets_mod.TARGETS_DIR / "gyms-denver-co.yaml").read_text())
    assert data["business_type"] == "dentist"


@pytest.mark.parametrize("bad_name", ["../evil", "UPPERCASE", "has spaces", "", "-leading-hyphen"])
def test_create_target_rejects_unsafe_or_invalid_names(client: TestClient, bad_name: str) -> None:
    form = dict(VALID_FORM, name=bad_name)

    resp = client.post("/targets", data=form)

    assert resp.status_code == 200
    assert "lowercase letters, digits, and hyphens" in resp.text
    # nothing was written outside (or inside) the targets dir
    assert list(targets_mod.TARGETS_DIR.glob("*.yaml")) == []


def test_create_target_rejects_unknown_business_type(client: TestClient) -> None:
    form = dict(VALID_FORM, business_type="spaceship-repair")

    resp = client.post("/targets", data=form)

    assert resp.status_code == 200
    assert "Unknown business_type" in resp.text


def test_create_target_rejects_bad_radius(client: TestClient) -> None:
    form = dict(VALID_FORM, radius_km="0")

    resp = client.post("/targets", data=form)

    assert resp.status_code == 200
    assert "greater than 0" in resp.text


def test_show_target_displays_raw_yaml(client: TestClient) -> None:
    _write_target("dentists-austin-tx")

    resp = client.get("/targets/dentists-austin-tx")

    assert resp.status_code == 200
    assert "dentists-austin-tx" in resp.text
    assert "touchto.io" in resp.text
    assert "run_pipeline.py targets/dentists-austin-tx.yaml" in resp.text


def test_show_target_404_for_missing(client: TestClient) -> None:
    resp = client.get("/targets/does-not-exist")
    assert resp.status_code == 404


def test_show_target_404_for_path_traversal_attempt(client: TestClient) -> None:
    resp = client.get("/targets/..%2F..%2Fetc%2Fpasswd")
    assert resp.status_code == 404
