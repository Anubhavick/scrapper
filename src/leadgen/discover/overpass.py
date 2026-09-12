"""Overpass API: build a query from a target profile's location + a
business type's OSM tags, run it, and parse the results.

No API key needed (PROJECT.md build order step 3) — this is the
cheapest-to-verify part of the whole pipeline, which is why it's built
first.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from leadgen.config.models import (
    AdminAreaLocation,
    BboxLocation,
    BusinessTypeDef,
    CityLocation,
    RadiusLocation,
    TargetProfile,
)
from leadgen.discover.geocode import GeocodeResult, NominatimClient

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
QUERY_TIMEOUT_SECONDS = 60


class OverpassError(Exception):
    """Raised when a query can't be built, or the Overpass API fails."""


@dataclass(frozen=True)
class DiscoveredBusiness:
    name: str
    source_id: str
    source: str = "overpass"
    website_url: str | None = None
    phone: str | None = None
    address: str | None = None
    lat: float | None = None
    lng: float | None = None
    source_raw: dict | None = None


def _area_id_for(geocoded: GeocodeResult) -> int:
    # Overpass's area-id convention: relation id + 3_600_000_000, or
    # way id + 2_400_000_000. A bare node has no boundary to build an
    # area from at all.
    if geocoded.osm_type == "relation":
        return 3_600_000_000 + geocoded.osm_id
    if geocoded.osm_type == "way":
        return 2_400_000_000 + geocoded.osm_id
    raise OverpassError(
        f"{geocoded.display_name!r} resolved to a single point, not an "
        "area with boundaries — city/admin_area mode needs a place "
        "Nominatim recognises as a way or relation; use radius or bbox "
        "mode for a point location instead"
    )


def build_query(
    profile: TargetProfile,
    business_type: BusinessTypeDef,
    geocoder: NominatimClient | None = None,
) -> str:
    """Build the Overpass QL query for a profile's location + business
    type. `geocoder` is required for radius/city/admin_area modes; bbox
    mode needs no geocoding since its coordinates are already explicit.
    """
    if not business_type.osm:
        raise OverpassError(
            f"business_type {profile.business_type!r} has no `osm` tags "
            "in business_types.yaml — nothing to query Overpass for"
        )

    location = profile.location
    area_clause = ""

    if isinstance(location, BboxLocation):
        south, west, north, east = location.bbox
        element_filter = f"({south},{west},{north},{east})"
    elif isinstance(location, RadiusLocation):
        if geocoder is None:
            raise OverpassError("radius mode requires a geocoder")
        geocoded = geocoder.geocode(location.center)
        radius_m = int(location.radius_km * 1000)
        element_filter = f"(around:{radius_m},{geocoded.lat},{geocoded.lon})"
    elif isinstance(location, (CityLocation, AdminAreaLocation)):
        if geocoder is None:
            raise OverpassError(f"{location.mode} mode requires a geocoder")
        geocoded = geocoder.geocode(location.center)
        area_clause = f"area({_area_id_for(geocoded)})->.searchArea;\n"
        element_filter = "(area.searchArea)"
    else:  # pragma: no cover - exhaustive per the Location union
        raise OverpassError(f"unsupported location mode: {location.mode!r}")

    tag_clauses = []
    for tag in business_type.osm:
        key, _, value = tag.partition("=")
        for element in ("node", "way"):
            tag_clauses.append(f'  {element}["{key}"="{value}"]{element_filter};')

    return (
        f"[out:json][timeout:{QUERY_TIMEOUT_SECONDS}];\n"
        f"{area_clause}"
        "(\n" + "\n".join(tag_clauses) + "\n"
        ");\n"
        "out center tags;"
    )


def run_query(query: str, client: httpx.Client) -> list[dict]:
    """Execute an Overpass QL query. Caller owns the client's lifecycle
    so tests can inject an httpx.MockTransport-backed client."""
    try:
        response = client.post(OVERPASS_URL, data={"data": query})
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise OverpassError(f"Overpass request failed: {exc}") from exc
    return response.json().get("elements", [])


def parse_elements(
    elements: list[dict], max_results: int
) -> list[DiscoveredBusiness]:
    """Turn Overpass `elements` into DiscoveredBusiness rows. Elements
    with no name or no resolvable coordinate are skipped rather than
    raising — one malformed element shouldn't fail the whole run."""
    results: list[DiscoveredBusiness] = []
    for element in elements:
        if len(results) >= max_results:
            break

        tags = element.get("tags", {})
        name = tags.get("name")
        if not name:
            continue

        if element.get("type") == "node":
            lat, lon = element.get("lat"), element.get("lon")
        else:
            center = element.get("center") or {}
            lat, lon = center.get("lat"), center.get("lon")
        if lat is None or lon is None:
            continue

        results.append(
            DiscoveredBusiness(
                name=name,
                source_id=f"{element['type']}/{element['id']}",
                website_url=tags.get("website") or tags.get("contact:website"),
                phone=tags.get("phone") or tags.get("contact:phone"),
                address=_format_address(tags),
                lat=lat,
                lng=lon,
                source_raw=tags,
            )
        )
    return results


def _format_address(tags: dict) -> str | None:
    parts = [
        tags.get(f"addr:{part}")
        for part in ("housenumber", "street", "city", "postcode")
    ]
    parts = [p for p in parts if p]
    return ", ".join(parts) if parts else None


def discover(
    profile: TargetProfile,
    business_type: BusinessTypeDef,
    client: httpx.Client,
    geocoder: NominatimClient | None = None,
) -> list[DiscoveredBusiness]:
    """End-to-end: build the query, run it, parse the results, capped
    at the profile's source.max_results."""
    query = build_query(profile, business_type, geocoder)
    elements = run_query(query, client)
    return parse_elements(elements, profile.source.max_results)
