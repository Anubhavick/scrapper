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
| 6 | Gmail OAuth, compose, and send-queue decision logic (caps + suppression) | [06-gmail-oauth-and-send-queue.md](06-gmail-oauth-and-send-queue.md) |
| 8 | Lead-review UI (scoped down), crawl_status/tag-dedup fixes from reading real data | [07-review-ui.md](07-review-ui.md) |
| — | Postgres persistence: pipeline.py -> businesses/contacts/enrichment_signals/target_runs | [08-persistence.md](08-persistence.md) |
| — | Run history UI: `/runs`, `/runs/{id}` -- browsing past scans from Postgres instead of a CSV | [09-run-history-ui.md](09-run-history-ui.md) |
| — | Scan-builder UI: `/targets`, `/targets/new`, `/targets/{name}` -- a form that writes targets/*.yaml | [10-scan-builder-ui.md](10-scan-builder-ui.md) |
| — | Campaigns + message approval: `/campaigns`, `/campaigns/new`, `/campaigns/{id}` -- target_run -> campaign -> rendered, human-approved messages | [11-campaigns-and-message-approval.md](11-campaigns-and-message-approval.md) |
