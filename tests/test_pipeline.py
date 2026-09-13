from pathlib import Path

import httpx
import pytest
import yaml

from leadgen.config.loader import load_target_profile
from leadgen.config.models import BusinessTypeDef
from leadgen.pipeline import export_csv, run_target_profile

DENTIST = BusinessTypeDef(osm=["amenity=dentist"])

OVERPASS_RESPONSE = {
    "elements": [
        {
            "type": "node",
            "id": 1,
            "lat": 28.46,
            "lon": 77.03,
            "tags": {
                "name": "Smile Dental",
                "website": "https://smiledental.example",
            },
        },
        {
            "type": "node",
            "id": 2,
            "lat": 28.47,
            "lon": 77.04,
            "tags": {"name": "Practo Listed Clinic", "website": "https://practo.com/x"},
        },
    ]
}

SMILEDENTAL_PAGES = {
    "/": "<html><body>Home. Contact info@smiledental.example.</body></html>",
    "/contact": "<html><body><form><input name='email'></form></body></html>",
}


def _profile(tmp_path: Path):
    data = {
        "name": "t",
        "business_type": "dentist",
        "location": {"mode": "bbox", "bbox": [28.4, 76.9, 28.5, 77.1]},
        "source": {"primary": "overpass"},
        "filters": {"exclude_domains": ["practo.com"]},
        "enrichment": {"crawl_pages": ["/", "/contact"], "max_pages": 8},
        "qualification": {
            "require_email": True,
            "require_any_signal": ["no_online_booking"],
            "min_signal_count": 1,
        },
        "outreach": {"offer_id": "o", "sender_pool": ["s1"]},
        "legal_region": "india",
    }
    path = tmp_path / "p.yaml"
    path.write_text(yaml.safe_dump(data))
    return load_target_profile(path, {"dentist": DENTIST})


def _overpass_client() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=OVERPASS_RESPONSE)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _crawl_client() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        html = SMILEDENTAL_PAGES.get(request.url.path)
        if html is None:
            return httpx.Response(404)
        return httpx.Response(200, text=html)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_run_target_profile_end_to_end(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("time.sleep", lambda s: None)
    profile = _profile(tmp_path)

    rows = run_target_profile(
        profile,
        DENTIST,
        overpass_client=_overpass_client(),
        crawl_client=_crawl_client(),
        user_agent="test-bot/1.0",
    )

    # The Practo-listed clinic is filtered out before ever being crawled.
    assert len(rows) == 1
    row = rows[0]
    assert row.name == "Smile Dental"
    assert "info@smiledental.example" in row.emails
    assert row.qualified is True  # has email + no booking mention anywhere -> no_online_booking=True
    assert "no-booking" in row.tags.split(";")


def test_export_csv_writes_expected_rows(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("time.sleep", lambda s: None)
    profile = _profile(tmp_path)
    rows = run_target_profile(
        profile,
        DENTIST,
        overpass_client=_overpass_client(),
        crawl_client=_crawl_client(),
        user_agent="test-bot/1.0",
    )

    csv_path = tmp_path / "leads.csv"
    export_csv(rows, csv_path)

    content = csv_path.read_text()
    assert "Smile Dental" in content
    assert "info@smiledental.example" in content
    assert "qualified" in content.splitlines()[0]  # header row
    assert "tags" in content.splitlines()[0]
    assert "no-booking" in content
