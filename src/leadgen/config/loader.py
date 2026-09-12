"""Load and validate the YAML config surface: target profiles, the
business-type registry, and offers.

Every function here either returns a valid, fully-typed object or
raises ConfigError with the offending file path in the message — never
a bare pydantic.ValidationError or yaml.YAMLError, so a bad profile
fails with something a non-Python user can act on.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from leadgen.config.models import BusinessTypeDef, ConfigError, Offer, TargetProfile

__all__ = [
    "ConfigError",
    "load_business_types",
    "load_offer",
    "load_offers",
    "load_target_profile",
    "load_target_profiles",
]


def _read_yaml(path: Path) -> dict:
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    try:
        with path.open() as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    if data is None:
        raise ConfigError(f"{path} is empty")
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must be a YAML mapping, got {type(data).__name__}")
    return data


def load_business_types(path: Path) -> dict[str, BusinessTypeDef]:
    """Load config/business_types.yaml: a mapping of business_type name
    -> its OSM tags / Places types / keywords."""
    raw = _read_yaml(path)
    result: dict[str, BusinessTypeDef] = {}
    for name, definition in raw.items():
        try:
            result[name] = BusinessTypeDef.model_validate(definition)
        except ValidationError as exc:
            raise ConfigError(
                f"invalid business type {name!r} in {path}: {exc}"
            ) from exc
    return result


def load_offer(path: Path) -> Offer:
    """Load a single config/offers/<id>.yaml file."""
    raw = _read_yaml(path)
    try:
        offer = Offer.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"invalid offer file {path}: {exc}") from exc
    if offer.id != path.stem:
        raise ConfigError(
            f"offer file {path} has id {offer.id!r}, expected {path.stem!r} "
            "(id must match the filename)"
        )
    return offer


def load_offers(offers_dir: Path) -> dict[str, Offer]:
    """Load every *.yaml file in config/offers/, keyed by offer id."""
    if not offers_dir.is_dir():
        raise ConfigError(f"offers directory not found: {offers_dir}")
    return {
        path.stem: load_offer(path) for path in sorted(offers_dir.glob("*.yaml"))
    }


def load_target_profile(
    path: Path,
    business_types: dict[str, BusinessTypeDef],
    offers: dict[str, Offer] | None = None,
) -> TargetProfile:
    """Load one targets/<name>.yaml file and cross-validate its
    business_type (and, if `offers` is given, its outreach.offer_id)
    against what's actually registered."""
    raw = _read_yaml(path)
    try:
        profile = TargetProfile.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"invalid target profile {path}: {exc}") from exc

    if profile.business_type not in business_types:
        raise ConfigError(
            f"target profile {path} references unknown business_type "
            f"{profile.business_type!r}; known types: {sorted(business_types)}"
        )

    if offers is not None and profile.outreach.offer_id not in offers:
        raise ConfigError(
            f"target profile {path} references unknown offer_id "
            f"{profile.outreach.offer_id!r}; known offers: {sorted(offers)}"
        )

    return profile


def load_target_profiles(
    targets_dir: Path,
    business_types: dict[str, BusinessTypeDef],
    offers: dict[str, Offer] | None = None,
) -> list[TargetProfile]:
    """Load every *.yaml file in targets/."""
    if not targets_dir.is_dir():
        raise ConfigError(f"targets directory not found: {targets_dir}")
    return [
        load_target_profile(path, business_types, offers)
        for path in sorted(targets_dir.glob("*.yaml"))
    ]
