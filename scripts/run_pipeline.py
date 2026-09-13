"""Run the discover -> filter -> crawl -> qualify -> CSV pipeline against
a real target profile, persist it to Postgres, and also write a CSV.

Usage:
    uv run python scripts/run_pipeline.py <target-profile.yaml> <output.csv> [--no-db]
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

from leadgen.config.loader import load_business_types, load_target_profile
from leadgen.db.session import session_scope
from leadgen.discover.geocode import NominatimClient
from leadgen.pipeline import export_csv, run_target_profile


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--no-db"]
    use_db = "--no-db" not in sys.argv
    if len(args) != 2:
        raise SystemExit(
            "usage: uv run python scripts/run_pipeline.py <target.yaml> <output.csv> [--no-db]"
        )
    target_path, output_path = Path(args[0]), Path(args[1])

    business_types = load_business_types(Path("config/business_types.yaml"))
    profile = load_target_profile(target_path, business_types)
    profile_yaml_text = target_path.read_text()

    user_agent = "leadgen-bot/0.1 (+contact: anubhav.ickk@gmail.com)"
    geocoder = NominatimClient(user_agent=user_agent, cache_dir=Path(".cache/nominatim"))

    with httpx.Client(timeout=60.0) as overpass_client, httpx.Client(timeout=30.0) as crawl_client:
        if use_db:
            with session_scope() as session:
                rows = run_target_profile(
                    profile,
                    business_types[profile.business_type],
                    overpass_client=overpass_client,
                    crawl_client=crawl_client,
                    user_agent=user_agent,
                    geocoder=geocoder,
                    session=session,
                    target_name=profile.name,
                    profile_yaml_text=profile_yaml_text,
                )
        else:
            rows = run_target_profile(
                profile,
                business_types[profile.business_type],
                overpass_client=overpass_client,
                crawl_client=crawl_client,
                user_agent=user_agent,
                geocoder=geocoder,
            )

    export_csv(rows, output_path)
    qualified = sum(1 for r in rows if r.qualified)
    persisted = " and persisted to Postgres" if use_db else ""
    print(f"{len(rows)} businesses found, {qualified} qualified. Wrote {output_path}{persisted}.")


if __name__ == "__main__":
    main()
