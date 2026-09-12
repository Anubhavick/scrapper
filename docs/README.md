# Docs index

A running log of what's been built, in what order, and why — one file
per step of PROJECT.md's build order. Each file is written when that
step is finished, not planned in advance, so it records what actually
happened (including any deviation from PROJECT.md) rather than intent.

For the current always-up-to-date state of the repo, see
[../README.md](../README.md) (setup, usage) and [../CLAUDE.md](../CLAUDE.md)
(conventions, schema decisions, hard rules). This folder is the history
those two files distill from.

| # | Step | Doc |
|---|---|---|
| 1 | Skeleton: pyproject, docker-compose, Alembic, db schema, `normalise_domain()` | [01-skeleton-and-schema.md](01-skeleton-and-schema.md) |
| 2 | Config loader: target profiles, business types, offers | [02-config-loader.md](02-config-loader.md) |
| 3 | Overpass discoverer: Nominatim geocoding + Overpass query/parse | [03-overpass-discoverer.md](03-overpass-discoverer.md) |
| 4 | Site crawler: robots.txt, contact emails, enrichment signals | [04-site-crawler.md](04-site-crawler.md) |
| 5 | Filters, qualification, CSV export — the discover→enrich→CSV pipeline | [05-csv-export-and-qualification.md](05-csv-export-and-qualification.md) |
