"""Pydantic schemas for the config-as-YAML surface described in
PROJECT.md: target profiles, the business-type registry, and offers.

`extra="forbid"` everywhere it's set is deliberate: a typo'd YAML key
(e.g. `radius_kms`) should fail loudly at load time, not silently be
ignored while the intended field falls back to its default.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

# The enrichment signals the crawler knows how to compute (PROJECT.md's
# example list). Anything referenced outside this set in a profile's
# qualification rules or an offer's relevant_signals is a typo, not a
# new feature — catch it at config-load time rather than at runtime
# when the signal silently never matches.
KNOWN_SIGNALS = frozenset(
    {
        "no_https",
        "no_mobile_viewport",
        "no_contact_form",
        "no_online_booking",
        "site_platform",
        "last_content_year",
        "page_weight_mb",
        "has_whatsapp_link",
    }
)

KNOWN_SOURCES = ("overpass", "places", "csv")


class ConfigError(Exception):
    """Raised for any invalid config file: bad YAML, a schema violation,
    or a valid-on-its-own file that references something (a business
    type, an offer, a signal) that doesn't exist."""


def _validate_known_signals(names: list[str]) -> list[str]:
    unknown = sorted(set(names) - KNOWN_SIGNALS)
    if unknown:
        raise ValueError(
            f"unknown signal(s) {unknown}; known signals: {sorted(KNOWN_SIGNALS)}"
        )
    return names


class RadiusLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["radius"]
    center: str
    radius_km: float = Field(gt=0)


class BboxLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["bbox"]
    # [south, west, north, east]
    bbox: tuple[float, float, float, float]

    @field_validator("bbox")
    @classmethod
    def validate_bbox(
        cls, v: tuple[float, float, float, float]
    ) -> tuple[float, float, float, float]:
        south, west, north, east = v
        if not (-90 <= south < north <= 90):
            raise ValueError(f"bbox south/north out of range or inverted: {v}")
        if not (-180 <= west < east <= 180):
            raise ValueError(f"bbox west/east out of range or inverted: {v}")
        return v


class CityLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["city"]
    center: str


class AdminAreaLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["admin_area"]
    center: str


Location = Annotated[
    Union[RadiusLocation, BboxLocation, CityLocation, AdminAreaLocation],
    Field(discriminator="mode"),
]


class SourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary: Literal["overpass", "places", "csv"]
    fallback: Literal["overpass", "places", "csv"] | None = None
    max_results: int = Field(gt=0, default=500)


class Filters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    must_have_website: bool = False
    must_have_phone: bool = False
    exclude_domains: list[str] = Field(default_factory=list)
    exclude_name_patterns: list[str] = Field(default_factory=list)
    min_name_length: int = Field(ge=0, default=0)

    @field_validator("exclude_name_patterns")
    @classmethod
    def validate_regex(cls, patterns: list[str]) -> list[str]:
        for pattern in patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"invalid regex {pattern!r}: {exc}") from exc
        return patterns


class Enrichment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    crawl_pages: list[str] = Field(default_factory=lambda: ["/"])
    max_pages: int = Field(gt=0, default=8)
    signals: list[str] = Field(default_factory=list)

    @field_validator("signals")
    @classmethod
    def validate_signals(cls, v: list[str]) -> list[str]:
        return _validate_known_signals(v)


class Qualification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    require_email: bool = True
    require_any_signal: list[str] = Field(default_factory=list)
    min_signal_count: int = Field(ge=0, default=0)

    @field_validator("require_any_signal")
    @classmethod
    def validate_signals(cls, v: list[str]) -> list[str]:
        return _validate_known_signals(v)


class Outreach(BaseModel):
    model_config = ConfigDict(extra="forbid")

    offer_id: str
    sender_pool: list[str] = Field(min_length=1)
    daily_cap_per_mailbox: int = Field(gt=0, default=40)


class TargetProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    enabled: bool = True
    business_type: str
    location: Location
    source: SourceConfig
    filters: Filters = Field(default_factory=Filters)
    enrichment: Enrichment = Field(default_factory=Enrichment)
    qualification: Qualification = Field(default_factory=Qualification)
    outreach: Outreach


class BusinessTypeDef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    osm: list[str] = Field(default_factory=list)
    places_types: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)

    @field_validator("osm")
    @classmethod
    def validate_osm_tags(cls, tags: list[str]) -> list[str]:
        for tag in tags:
            if "=" not in tag or tag.startswith("=") or tag.endswith("="):
                raise ValueError(f"OSM tag {tag!r} must be in key=value form")
        return tags


class Offer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    subject_templates: list[str] = Field(min_length=1)
    body_template: str
    relevant_signals: list[str] = Field(default_factory=list)
    cta: str

    @field_validator("relevant_signals")
    @classmethod
    def validate_signals(cls, v: list[str]) -> list[str]:
        return _validate_known_signals(v)
