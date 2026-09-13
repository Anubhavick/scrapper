import base64
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from leadgen.api.review import app
import leadgen.api.targets as targets_mod


def _basic_auth_header(username: str, password: str) -> str:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {token}"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("WEB_UI_USERNAME", "test-user")
    monkeypatch.setenv("WEB_UI_PASSWORD", "test-pass")
    return TestClient(app, headers={"Authorization": _basic_auth_header("test-user", "test-pass")})


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
        "legal_region": "us",
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
    "legal_region": "us",
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


def test_create_target_rejects_missing_legal_region(client: TestClient) -> None:
    form = dict(VALID_FORM)
    del form["legal_region"]

    resp = client.post("/targets", data=form)

    assert resp.status_code == 200
    assert "legal_region" in resp.text
    assert list(targets_mod.TARGETS_DIR.glob("*.yaml")) == []


def test_create_target_rejects_eu_uk_without_opt_in(client: TestClient) -> None:
    form = dict(VALID_FORM, legal_region="eu_uk")

    resp = client.post("/targets", data=form)

    assert resp.status_code == 200
    assert "requires_opt_in" in resp.text
    assert list(targets_mod.TARGETS_DIR.glob("*.yaml")) == []


def test_create_target_accepts_eu_uk_with_opt_in(client: TestClient) -> None:
    form = dict(VALID_FORM, legal_region="eu_uk", requires_opt_in="on")

    resp = client.post("/targets", data=form, follow_redirects=False)

    assert resp.status_code == 303
    data = yaml.safe_load((targets_mod.TARGETS_DIR / "gyms-denver-co.yaml").read_text())
    assert data["legal_region"] == "eu_uk"
    assert data["requires_opt_in"] is True


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


def test_edit_target_form_prefills_from_existing_file(client: TestClient) -> None:
    _write_target("dentists-austin-tx")

    resp = client.get("/targets/dentists-austin-tx/edit")

    assert resp.status_code == 200
    assert "touchto.io" in resp.text
    assert "Renaming isn't supported" in resp.text
    assert 'action="/targets/dentists-austin-tx/edit"' in resp.text


def test_edit_target_form_404_for_missing(client: TestClient) -> None:
    resp = client.get("/targets/does-not-exist/edit")
    assert resp.status_code == 404


def test_edit_target_saves_changes_and_redirects(client: TestClient) -> None:
    _write_target("dentists-austin-tx")
    form = dict(VALID_FORM, name="dentists-austin-tx", business_type="dentist", radius_km="25")

    resp = client.post("/targets/dentists-austin-tx/edit", data=form, follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"] == "/targets/dentists-austin-tx?updated=1"
    data = yaml.safe_load((targets_mod.TARGETS_DIR / "dentists-austin-tx.yaml").read_text())
    assert data["location"]["radius_km"] == 25.0


def test_edit_target_cannot_rename_via_submitted_name_field(client: TestClient) -> None:
    _write_target("dentists-austin-tx")
    form = dict(VALID_FORM, name="totally-different-name", business_type="dentist")

    resp = client.post("/targets/dentists-austin-tx/edit", data=form, follow_redirects=False)

    assert resp.status_code == 303
    # still saved under the URL's name, not the form's submitted name
    assert resp.headers["location"] == "/targets/dentists-austin-tx?updated=1"
    assert not (targets_mod.TARGETS_DIR / "totally-different-name.yaml").exists()
    data = yaml.safe_load((targets_mod.TARGETS_DIR / "dentists-austin-tx.yaml").read_text())
    assert data["name"] == "dentists-austin-tx"


def test_edit_target_does_not_check_for_duplicate_name(client: TestClient) -> None:
    # Overwriting the same file it's editing must not trip the
    # create-only "already exists" rejection.
    _write_target("dentists-austin-tx")
    form = dict(VALID_FORM, name="dentists-austin-tx", business_type="dentist")

    resp = client.post("/targets/dentists-austin-tx/edit", data=form, follow_redirects=False)

    assert resp.status_code == 303


def test_edit_target_rejects_invalid_submission_without_writing(client: TestClient) -> None:
    _write_target("dentists-austin-tx")
    original = (targets_mod.TARGETS_DIR / "dentists-austin-tx.yaml").read_text()
    form = dict(VALID_FORM, name="dentists-austin-tx", business_type="dentist", radius_km="0")

    resp = client.post("/targets/dentists-austin-tx/edit", data=form)

    assert resp.status_code == 200
    assert "greater than 0" in resp.text
    assert (targets_mod.TARGETS_DIR / "dentists-austin-tx.yaml").read_text() == original


def test_edit_target_404_for_missing_on_post(client: TestClient) -> None:
    resp = client.post("/targets/does-not-exist/edit", data=VALID_FORM)
    assert resp.status_code == 404


def test_edit_target_form_shows_error_for_unparseable_yaml(client: TestClient) -> None:
    bad_path = targets_mod.TARGETS_DIR / "broken-yaml.yaml"
    bad_path.write_text("name: [unterminated\n")

    resp = client.get("/targets/broken-yaml/edit")

    assert resp.status_code == 400
    assert "doesn't parse as YAML" in resp.text


def test_show_target_displays_updated_notice(client: TestClient) -> None:
    _write_target("dentists-austin-tx")

    resp = client.get("/targets/dentists-austin-tx", params={"updated": "1"})

    assert resp.status_code == 200
    assert "Saved changes" in resp.text


def test_list_targets_shows_edit_link(client: TestClient) -> None:
    _write_target("dentists-austin-tx")

    resp = client.get("/targets")

    assert "/targets/dentists-austin-tx/edit" in resp.text


def test_list_targets_no_pager_when_under_one_page(client: TestClient) -> None:
    for i in range(3):
        _write_target(f"target-{i}")

    resp = client.get("/targets")

    assert resp.status_code == 200
    assert "Page 1 of" not in resp.text


def test_list_targets_paginates_when_over_page_size(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import leadgen.api.nav as nav_mod

    monkeypatch.setattr(nav_mod, "PAGE_SIZE", 2)
    monkeypatch.setattr(targets_mod, "PAGE_SIZE", 2)
    for i in range(5):
        _write_target(f"target-{i}")

    resp_page1 = client.get("/targets")
    assert "Page 1 of 3" in resp_page1.text
    assert "5 profile(s)" in resp_page1.text
    # only the first 2 (alphabetically) should be listed on page 1
    assert "target-0" in resp_page1.text
    assert "target-1" in resp_page1.text
    assert "target-2" not in resp_page1.text

    resp_page2 = client.get("/targets", params={"page": 2})
    assert "target-2" in resp_page2.text
    assert "target-3" in resp_page2.text
    assert "target-0" not in resp_page2.text
