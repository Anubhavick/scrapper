"""Run the discover -> filter -> crawl -> qualify -> CSV pipeline against
a real target profile and write the result to a CSV file.

Usage:
    uv run python scripts/run_pipeline.py <target-profile.yaml> <output.csv>
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

from leadgen.config.loader import load_business_types, load_target_profile
from leadgen.discover.geocode import NominatimClient
from leadgen.pipeline import export_csv, run_target_profile


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: uv run python scripts/run_pipeline.py <target.yaml> <output.csv>")
    target_path, output_path = Path(sys.argv[1]), Path(sys.argv[2])

    business_types = load_business_types(Path("config/business_types.yaml"))
    profile = load_target_profile(target_path, business_types)

    user_agent = "leadgen-bot/0.1 (+contact: anubhav.ickk@gmail.com)"
    geocoder = NominatimClient(user_agent=user_agent, cache_dir=Path(".cache/nominatim"))

    with httpx.Client(timeout=60.0) as overpass_client, httpx.Client(timeout=30.0) as crawl_client:
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
    print(f"{len(rows)} businesses found, {qualified} qualified. Wrote {output_path}")


if __name__ == "__main__":
    main()
