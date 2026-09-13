import base64
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from leadgen.api.review import _business_row_dict, add_excluded_domain, app
from leadgen.db.models import Business, TargetRunBusiness

CSV_HEADER = "name,website_url,phone,address,emails,qualified,crawl_status,tags,signals\n"


def _basic_auth_header(username: str, password: str) -> str:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {token}"


def _write_csv(path: Path) -> None:
    path.write_text(
        CSV_HEADER
        + 'Good Dental,https://good-dental.example/,+1-555-0100,"1 Main St",hi@good-dental.example,yes,ok,no-booking,{}\n'
        + "Bad Match,https://not-a-dentist.example/,+1-555-0200,,,,no,ok,,{}\n"
        + "Dead Site,https://dead.example/,,,,no,unreachable,error-connection,{}\n"
    )


def _write_profile(path: Path) -> None:
    path.write_text(
        "name: t\n"
        "filters:\n"
        "  must_have_website: true\n"
        "  min_name_length: 3\n"
    )


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("WEB_UI_USERNAME", "test-user")
    monkeypatch.setenv("WEB_UI_PASSWORD", "test-pass")
    return TestClient(app, headers={"Authorization": _basic_auth_header("test-user", "test-pass")})


def test_index_renders_rows(tmp_path: Path, client: TestClient) -> None:
    csv_path = tmp_path / "leads.csv"
    _write_csv(csv_path)
    profile_path = tmp_path / "profile.yaml"
    _write_profile(profile_path)

    resp = client.get("/", params={"csv": str(csv_path), "profile": str(profile_path)})

    assert resp.status_code == 200
    assert "Good Dental" in resp.text
    assert "Bad Match" in resp.text
    assert "3 of 3 rows shown" in resp.text


def test_index_missing_csv_returns_404(tmp_path: Path, client: TestClient) -> None:
    resp = client.get("/", params={"csv": str(tmp_path / "nope.csv")})
    assert resp.status_code == 404


def test_filter_by_qualified(tmp_path: Path, client: TestClient) -> None:
    csv_path = tmp_path / "leads.csv"
    _write_csv(csv_path)
    profile_path = tmp_path / "profile.yaml"
    _write_profile(profile_path)

    resp = client.get(
        "/", params={"csv": str(csv_path), "profile": str(profile_path), "qualified": "yes"}
    )

    assert "Good Dental" in resp.text
    assert "Bad Match" not in resp.text
    assert "1 of 3 rows shown" in resp.text


def test_filter_by_status(tmp_path: Path, client: TestClient) -> None:
    csv_path = tmp_path / "leads.csv"
    _write_csv(csv_path)
    profile_path = tmp_path / "profile.yaml"
    _write_profile(profile_path)

    resp = client.get(
        "/", params={"csv": str(csv_path), "profile": str(profile_path), "status": "unreachable"}
    )

    assert "Dead Site" in resp.text
    assert "Good Dental" not in resp.text


def test_reject_adds_domain_and_redirects(tmp_path: Path, client: TestClient) -> None:
    csv_path = tmp_path / "leads.csv"
    _write_csv(csv_path)
    profile_path = tmp_path / "profile.yaml"
    _write_profile(profile_path)

    resp = client.post(
        "/reject",
        data={
            "name": "Bad Match",
            "domain": "not-a-dentist.example",
            "csv": str(csv_path),
            "profile": str(profile_path),
            "filter_qs": f"csv={csv_path}&profile={profile_path}",
        },
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert "rejected=not-a-dentist.example" in resp.headers["location"]
    assert "exclude_domains: [not-a-dentist.example]" in profile_path.read_text()


def test_reject_works_with_empty_csv_field(tmp_path: Path, client: TestClient) -> None:
    """Regression test: the /runs/{id} (DB-backed) page has no CSV to point
    at, so it renders the reject form's hidden `csv` field as an empty
    string. A live browser/curl check caught that FastAPI's `Form(...)`
    treats an empty-string form value as *missing*, not empty -- 422,
    not a render bug -- so `csv` must stay optional (Form("")) even
    though the handler never reads it."""
    profile_path = tmp_path / "profile.yaml"
    _write_profile(profile_path)

    resp = client.post(
        "/reject",
        data={
            "name": "Some Business",
            "domain": "example.com",
            "csv": "",
            "profile": str(profile_path),
            "filter_qs": "qualified=all",
            "return_to": "/runs/some-run-id",
        },
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/runs/some-run-id?")


def test_business_row_dict_matches_csv_row_shape() -> None:
    """The DB-backed /runs/{id} page reuses `_row_html`/`_filtered`
    unchanged from the CSV-backed `/` page -- this only works if
    `_business_row_dict` produces the same key/value shape a CSV
    DictReader row would."""
    business = Business(
        name="River Rock Dental",
        business_type="dentist",
        website_url="https://riverrockdentalfamily.com/locations/mueller/",
        phone="+1-512-669-5147",
        address="1801 East 51st Street, Austin, 78723",
        source="overpass",
        source_id="node/1",
    )
    run_business = TargetRunBusiness(
        qualified=True,
        crawl_status="ok",
        tags=["platform-wordpress", "content-year-2024"],
    )

    row = _business_row_dict(business, ["front@riverrockdentalfamily.com"], run_business)

    assert row == {
        "name": "River Rock Dental",
        "address": "1801 East 51st Street, Austin, 78723",
        "website_url": "https://riverrockdentalfamily.com/locations/mueller/",
        "phone": "+1-512-669-5147",
        "emails": "front@riverrockdentalfamily.com",
        "qualified": "yes",
        "crawl_status": "ok",
        "tags": "platform-wordpress;content-year-2024",
    }


def test_add_excluded_domain_is_idempotent(tmp_path: Path) -> None:
    profile_path = tmp_path / "profile.yaml"
    profile_path.write_text(
        "filters:\n  exclude_domains: [existing.example]\n  min_name_length: 3\n"
    )

    added_first = add_excluded_domain(profile_path, "new.example")
    added_second = add_excluded_domain(profile_path, "new.example")

    assert added_first is True
    assert added_second is False
    assert "exclude_domains: [existing.example, new.example]" in profile_path.read_text()


def test_add_excluded_domain_preserves_comments(tmp_path: Path) -> None:
    profile_path = tmp_path / "profile.yaml"
    profile_path.write_text(
        "filters:\n  # a hand-written note\n  exclude_domains: [existing.example]\n"
    )

    add_excluded_domain(profile_path, "new.example")

    text = profile_path.read_text()
    assert "# a hand-written note" in text
    assert "exclude_domains: [existing.example, new.example]" in text
