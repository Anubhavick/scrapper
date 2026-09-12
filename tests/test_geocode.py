from pathlib import Path

import httpx
import pytest

from leadgen.discover.geocode import GeocodeError, NominatimClient

NOMINATIM_RESPONSE = [
    {
        "lat": "28.4594965",
        "lon": "77.0266383",
        "osm_type": "relation",
        "osm_id": "1234567",
        "display_name": "Gurugram, Haryana, India",
    }
]


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_geocode_parses_result(tmp_path: Path) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert "User-Agent" in request.headers
        return httpx.Response(200, json=NOMINATIM_RESPONSE)

    nominatim = NominatimClient(
        user_agent="test-bot/1.0 (contact@example.com)",
        cache_dir=tmp_path,
        client=_client(handler),
        min_interval=0,
    )
    result = nominatim.geocode("Gurugram, Haryana, India")

    assert result.lat == pytest.approx(28.4594965)
    assert result.lon == pytest.approx(77.0266383)
    assert result.osm_type == "relation"
    assert result.osm_id == 1234567
    assert len(calls) == 1


def test_geocode_caches_to_disk(tmp_path: Path) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=NOMINATIM_RESPONSE)

    nominatim = NominatimClient(
        user_agent="test-bot/1.0",
        cache_dir=tmp_path,
        client=_client(handler),
        min_interval=0,
    )
    nominatim.geocode("Gurugram")
    nominatim.geocode("Gurugram")
    nominatim.geocode("Gurugram")

    assert len(calls) == 1  # second and third calls hit the disk cache


def test_geocode_no_results_raises(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    nominatim = NominatimClient(
        user_agent="test-bot/1.0",
        cache_dir=tmp_path,
        client=_client(handler),
        min_interval=0,
    )
    with pytest.raises(GeocodeError, match="no results"):
        nominatim.geocode("Nowhereville")


def test_geocode_requires_real_user_agent(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="User-Agent"):
        NominatimClient(user_agent="", cache_dir=tmp_path)


def test_geocode_rate_limits_between_requests(tmp_path: Path, monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))

    clock = iter([100.0, 100.2, 105.0])
    monkeypatch.setattr("time.monotonic", lambda: next(clock))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=NOMINATIM_RESPONSE)

    nominatim = NominatimClient(
        user_agent="test-bot/1.0",
        cache_dir=tmp_path,
        client=_client(handler),
        min_interval=1.0,
    )
    nominatim.geocode("A")  # consumes monotonic() call #1 (post-request timestamp)
    nominatim.geocode("B")  # elapsed 0.2s since last -> should sleep ~0.8s
    assert sleeps and sleeps[0] == pytest.approx(0.8, abs=0.01)
