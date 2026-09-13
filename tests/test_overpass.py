from pathlib import Path

import httpx
import pytest

from leadgen.config.loader import load_target_profile
from leadgen.config.models import BusinessTypeDef
from leadgen.discover.geocode import NominatimClient
from leadgen.discover.overpass import (
    OverpassError,
    build_query,
    discover,
    parse_elements,
    run_query,
)

DENTIST = BusinessTypeDef(osm=["amenity=dentist", "healthcare=dentist"])

NOMINATIM_RELATION_RESPONSE = [
    {
        "lat": "28.4595",
        "lon": "77.0266",
        "osm_type": "relation",
        "osm_id": "1234567",
        "display_name": "Gurugram, Haryana, India",
    }
]

NOMINATIM_NODE_RESPONSE = [
    {
        "lat": "28.4595",
        "lon": "77.0266",
        "osm_type": "node",
        "osm_id": "999",
        "display_name": "A single point",
    }
]

OVERPASS_ELEMENTS = {
    "elements": [
        {
            "type": "node",
            "id": 111,
            "lat": 28.46,
            "lon": 77.03,
            "tags": {
                "name": "Smile Dental Clinic",
                "website": "https://smiledental.example",
                "phone": "+91 124 000 0000",
                "addr:housenumber": "12",
                "addr:street": "MG Road",
                "addr:city": "Gurugram",
            },
        },
        {
            "type": "way",
            "id": 222,
            "center": {"lat": 28.47, "lon": 77.04},
            "tags": {"name": "Bright Smiles"},
        },
        {"type": "node", "id": 333, "lat": 28.48, "lon": 77.05, "tags": {}},  # no name
        {"type": "way", "id": 444, "tags": {"name": "No center given"}},  # no center
    ]
}


def _profile(tmp_path: Path, **overrides):
    import uuid

    import yaml

    data = {
        "name": "t",
        "business_type": "dentist",
        "location": {"mode": "radius", "center": "Gurugram", "radius_km": 15},
        "source": {"primary": "overpass", "max_results": 500},
        "outreach": {"offer_id": "o", "sender_pool": ["s1"]},
        "legal_region": "india",
    }
    data.update(overrides)
    path = tmp_path / f"{uuid.uuid4()}.yaml"
    path.write_text(yaml.safe_dump(data))
    return load_target_profile(path, {"dentist": DENTIST})


def _geocoder(tmp_path: Path, response=NOMINATIM_RELATION_RESPONSE) -> NominatimClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response)

    return NominatimClient(
        user_agent="test-bot/1.0",
        cache_dir=tmp_path,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        min_interval=0,
    )


# ---- build_query ----


def test_build_query_radius_mode(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    query = build_query(profile, DENTIST, _geocoder(tmp_path))
    assert "around:15000,28.4595,77.0266" in query
    assert 'node["amenity"="dentist"]' in query
    assert 'way["healthcare"="dentist"]' in query


def test_build_query_bbox_mode_needs_no_geocoder(tmp_path: Path) -> None:
    profile = _profile(
        tmp_path, location={"mode": "bbox", "bbox": [28.4, 76.9, 28.5, 77.1]}
    )
    query = build_query(profile, DENTIST, geocoder=None)
    assert "(28.4,76.9,28.5,77.1)" in query


def test_build_query_city_mode_uses_area(tmp_path: Path) -> None:
    profile = _profile(tmp_path, location={"mode": "city", "center": "Gurugram"})
    query = build_query(profile, DENTIST, _geocoder(tmp_path))
    assert "area(3601234567)->.searchArea" in query
    assert "(area.searchArea)" in query


def test_build_query_city_mode_point_location_raises(tmp_path: Path) -> None:
    profile = _profile(tmp_path, location={"mode": "city", "center": "A Point"})
    geocoder = _geocoder(tmp_path, response=NOMINATIM_NODE_RESPONSE)
    with pytest.raises(OverpassError, match="not an area"):
        build_query(profile, DENTIST, geocoder)


def test_build_query_radius_without_geocoder_raises(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    with pytest.raises(OverpassError, match="requires a geocoder"):
        build_query(profile, DENTIST, geocoder=None)


def test_build_query_business_type_with_no_osm_tags_raises(tmp_path: Path) -> None:
    profile = _profile(
        tmp_path, location={"mode": "bbox", "bbox": [28.4, 76.9, 28.5, 77.1]}
    )
    with pytest.raises(OverpassError, match="no `osm` tags"):
        build_query(profile, BusinessTypeDef(), geocoder=None)


# ---- parse_elements ----


def test_parse_elements_extracts_node_and_way() -> None:
    results = parse_elements(OVERPASS_ELEMENTS["elements"], max_results=100)
    assert len(results) == 2  # the two without a name/center are skipped

    node_result = results[0]
    assert node_result.name == "Smile Dental Clinic"
    assert node_result.source_id == "node/111"
    assert node_result.website_url == "https://smiledental.example"
    assert node_result.address == "12, MG Road, Gurugram"
    assert node_result.lat == 28.46

    way_result = results[1]
    assert way_result.name == "Bright Smiles"
    assert way_result.source_id == "way/222"
    assert way_result.lat == 28.47


def test_parse_elements_respects_max_results() -> None:
    results = parse_elements(OVERPASS_ELEMENTS["elements"], max_results=1)
    assert len(results) == 1


# ---- run_query / discover end-to-end ----


def test_run_query_returns_elements() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        return httpx.Response(200, json=OVERPASS_ELEMENTS)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    elements = run_query("fake query", client, user_agent="test-bot/1.0")
    assert len(elements) == 4


def test_run_query_sends_identifying_user_agent() -> None:
    # overpass-api.de returns 406 for requests without a real User-Agent
    # (a generic httpx default gets rejected) -- this was missed until a
    # live run against the real API surfaced it, since no mocked test
    # checked headers before.
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["User-Agent"] == "test-bot/1.0 (+contact: me@example.com)"
        return httpx.Response(200, json=OVERPASS_ELEMENTS)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    run_query("fake query", client, user_agent="test-bot/1.0 (+contact: me@example.com)")


def test_run_query_http_error_raises_overpass_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="Bad Request")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(OverpassError, match="Overpass request failed"):
        run_query("fake query", client, user_agent="test-bot/1.0")


def test_run_query_retries_transient_errors_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] < 3:
            return httpx.Response(504, text="Gateway Timeout")
        return httpx.Response(200, json=OVERPASS_ELEMENTS)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    elements = run_query("fake query", client, user_agent="test-bot/1.0")
    assert attempts["count"] == 3
    assert len(elements) == 4


def test_run_query_gives_up_after_max_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda seconds: None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(504, text="Gateway Timeout")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(OverpassError, match="Overpass request failed"):
        run_query("fake query", client, user_agent="test-bot/1.0", max_attempts=3)


def test_discover_end_to_end(tmp_path: Path) -> None:
    def overpass_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=OVERPASS_ELEMENTS)

    profile = _profile(tmp_path)
    overpass_client = httpx.Client(transport=httpx.MockTransport(overpass_handler))
    results = discover(profile, DENTIST, overpass_client, "test-bot/1.0", _geocoder(tmp_path))
    assert len(results) == 2
    assert results[0].name == "Smile Dental Clinic"
