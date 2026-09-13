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
| — | Orchestration loop: reads `approved` messages, sends via Gmail respecting caps/suppression/the randomised gap | [12-orchestration-loop.md](12-orchestration-loop.md) |
| — | Send from the campaigns UI: a per-campaign "Send" button backed by an RQ background job, plus two more real bugs caught along the way | [13-send-from-ui.md](13-send-from-ui.md) |
| — | Manual suppression-list UI: `/suppressions`, create-only, normalised the same way the real send path checks it | [14-suppression-list-ui.md](14-suppression-list-ui.md) |
| — | Fixed `last_content_year` mistaking a copyright-footer year for real content freshness | [15-last-content-year-copyright-fix.md](15-last-content-year-copyright-fix.md) |
| — | Preflight token-health check in the orchestration loop -- a dead refresh token now blocks a mailbox's whole queue up front instead of failing per message | [16-orchestration-token-health-preflight.md](16-orchestration-token-health-preflight.md) |
| — | HTTP Basic Auth on the whole web UI -- one shared team credential gating every route | [17-web-ui-basic-auth.md](17-web-ui-basic-auth.md) |
| — | `/mailboxes` health page -- live token-health, cap usage, and last-real-send-error visibility per mailbox | [18-mailbox-health-page.md](18-mailbox-health-page.md) |
| — | Five smaller items: target-profile edit-in-place, pagination, bulk-approve, mailbox reassignment, mid-run mailbox-health re-check | [19-smaller-backlog-batch.md](19-smaller-backlog-batch.md) |
