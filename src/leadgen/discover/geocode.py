"""Nominatim geocoding: resolve a place name to coordinates + OSM id.

Nominatim's usage policy caps requests at 1/sec and requires a real,
identifying User-Agent — both enforced here, not left to the caller.
Results are cached to disk keyed by the place name, so the same city is
never geocoded twice (PROJECT.md § Location resolution).
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
MIN_REQUEST_INTERVAL_SECONDS = 1.0


class GeocodeError(Exception):
    """Raised when a place name can't be resolved to a location."""


@dataclass(frozen=True)
class GeocodeResult:
    lat: float
    lon: float
    osm_type: str  # "node" | "way" | "relation"
    osm_id: int
    display_name: str


class NominatimClient:
    def __init__(
        self,
        user_agent: str,
        cache_dir: Path,
        client: httpx.Client | None = None,
        min_interval: float = MIN_REQUEST_INTERVAL_SECONDS,
    ) -> None:
        if not user_agent or not user_agent.strip():
            raise ValueError(
                "Nominatim's usage policy requires a real, identifying "
                "User-Agent (see CRAWLER_USER_AGENT in .env.example)"
            )
        self._user_agent = user_agent
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._client = client or httpx.Client(timeout=30.0)
        self._min_interval = min_interval
        self._last_request_at: float | None = None

    def geocode(self, place_name: str) -> GeocodeResult:
        cached = self._read_cache(place_name)
        if cached is not None:
            return cached

        self._wait_for_rate_limit()
        response = self._client.get(
            NOMINATIM_URL,
            params={"q": place_name, "format": "json", "limit": 1},
            headers={"User-Agent": self._user_agent},
        )
        self._last_request_at = time.monotonic()
        response.raise_for_status()
        results = response.json()
        if not results:
            raise GeocodeError(f"Nominatim found no results for {place_name!r}")

        top = results[0]
        result = GeocodeResult(
            lat=float(top["lat"]),
            lon=float(top["lon"]),
            osm_type=top["osm_type"],
            osm_id=int(top["osm_id"]),
            display_name=top["display_name"],
        )
        self._write_cache(place_name, result)
        return result

    def _wait_for_rate_limit(self) -> None:
        if self._last_request_at is None:
            return
        remaining = self._min_interval - (time.monotonic() - self._last_request_at)
        if remaining > 0:
            time.sleep(remaining)

    def _cache_path(self, place_name: str) -> Path:
        key = hashlib.sha256(place_name.strip().lower().encode()).hexdigest()
        return self._cache_dir / f"{key}.json"

    def _read_cache(self, place_name: str) -> GeocodeResult | None:
        path = self._cache_path(place_name)
        if not path.is_file():
            return None
        return GeocodeResult(**json.loads(path.read_text()))

    def _write_cache(self, place_name: str, result: GeocodeResult) -> None:
        self._cache_path(place_name).write_text(json.dumps(asdict(result)))
