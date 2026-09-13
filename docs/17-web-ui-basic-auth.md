# 17 — HTTP Basic Auth on the whole web UI

ROADMAP.md item 5: "No auth on the web UI at all. Fine for one person on
localhost; a real problem the moment it's reachable by anyone else — no
login currently stands between a visitor and creating a campaign or
approving a send." Five pages (`/`, `/runs`, `/targets`, `/campaigns`,
`/suppressions`) all sat on one shared FastAPI app with zero access
control.

## What was built

- **`src/leadgen/api/auth.py`** — `require_auth`, a FastAPI dependency
  using `fastapi.security.HTTPBasic`. Compares the request's credentials
  against `WEB_UI_USERNAME`/`WEB_UI_PASSWORD` (env, plaintext in `.env`
  the same way `GOOGLE_OAUTH_CLIENT_SECRET`/`TOKEN_ENCRYPTION_KEY`
  already are) via `secrets.compare_digest` for both fields — a plain
  `==` would leak how many leading characters of a guessed
  username/password were correct through response timing, which is
  exactly what FastAPI's own HTTP Basic Auth docs call out and
  `compare_digest` avoids. A 401 carries `WWW-Authenticate: Basic`, which
  is what makes a browser show its native login prompt instead of a
  blank failure.
- **`src/leadgen/api/review.py`** — `app = FastAPI(..., dependencies=[
  Depends(require_auth)])`. A dependency at the `FastAPI()` constructor
  level, not per-router, applies to *every* route on that app — the four
  routers mounted via `include_router` (`targets`, `suppressions`,
  `campaigns`) and the auto-generated `/docs`/`/openapi.json` too, all in
  one line, with no risk of a new route someday forgetting to opt in.
- **One shared credential, not per-user accounts, on purpose.** This is
  a small team on one internal tool, and per-user identity already has a
  place in this system that has nothing to do with who's logged into the
  browser — typing a name to approve a message (docs/11) or add a
  suppression reason. Building a users table, login form, and session
  cookies to solve a problem nobody has yet (distinguishing which team
  member is *browsing*, as opposed to who *approved* something) would be
  new surface area for its own sake. HTTP Basic against one shared
  secret directly closes the actual gap ROADMAP.md flagged — an
  unauthenticated visitor reaching any page — with no new tables, no
  session/cookie code, no login page to build or style.
- **`.env.example`** gained `WEB_UI_USERNAME`/`WEB_UI_PASSWORD` (blank,
  same convention as the other secrets there). **`scripts/dev.sh`**
  gained a preflight check (`_check_web_ui_credentials`, run before
  Docker even starts) that fails with a clear message if either is
  unset in `.env` — without it, the script's own readiness check
  (`curl -s -o /dev/null`, which doesn't fail on a non-2xx response)
  would happily print "Ready" while every real page 500s underneath.

## A real bug caught by actually hitting the running server

The first version of `_configured_credentials()` read
`os.environ.get(...)` directly with no `load_dotenv()` call. Every other
module that reads a required env var (`db/session.py`'s `get_engine()`,
`queue.py`'s `get_queue()`, `jobs.py`) calls `load_dotenv()` lazily right
before reading — `uv run` does **not** auto-load `.env` into the process
environment (confirmed directly: `uv run python -c "import os;
print(os.environ.get('DATABASE_URL'))"` prints `None` even with a
populated `.env` present), so any module that skips this simply never
sees `.env`'s values unless some *other* code path already called
`load_dotenv()` first in that same process — which happens to be true
for DB-touching routes (`db/session.py` gets there first) but not for a
plain page like `/targets`, which touches no database at all.

Caught by actually starting `scripts/dev.sh up` and hitting the real
running server with `curl`: requests with *no* credentials correctly
got `401`, but requests with the *correct* credentials got `500` —
`_configured_credentials()` raised its "not configured" `RuntimeError`
even though `.env` had both variables set, because nothing had loaded
`.env` into that request's process environment yet. Fixed by adding the
same `load_dotenv()` call `db/session.py` makes, in the same place
(lazily, inside the function, right before reading `os.environ`) — a
unit test alone (which sets env vars directly via `monkeypatch.setenv`,
never touching the `.env`-loading path at all) could not have caught
this; it needed a real process actually started the way `scripts/dev.sh`
starts it.

This also surfaced a second-order test-isolation issue while fixing it:
`load_dotenv()`'s default `override=False` means it fills in only
variables *absent* from `os.environ` — safe for tests that set values
via `monkeypatch.setenv` (already present, never overwritten), but a
test asserting the *unconfigured* case (`monkeypatch.delenv(...)`) would
have had this repo's own real `.env` silently refill the deleted values
back in via that same `load_dotenv()` call, defeating the test. Fixed by
also stubbing `leadgen.api.auth.load_dotenv` to a no-op in that one test.

## Tests

`tests/test_auth.py` (new): `require_auth()` as a pure function —
correct credentials pass, wrong username and wrong password each 401
with `WWW-Authenticate: Basic`, and the unconfigured case raises a clear
`RuntimeError` rather than silently allowing access — plus three
integration-style checks against a real (throwaway) `FastAPI` app wired
the same way `api/review.py` wires it: no credentials → 401, wrong
credentials → 401, correct credentials → 200 with the real response
body.

`tests/test_review_api.py` and `tests/test_targets_api.py`'s `client()`
fixtures now set `WEB_UI_USERNAME`/`WEB_UI_PASSWORD` via `monkeypatch`
and send a Basic Auth header on every request (`TestClient` in the
installed Starlette version has no `auth=` kwarg, unlike plain `httpx`
— confirmed by trying it and reading the resulting `TypeError` before
switching to a manually-built `Authorization` header) — otherwise every
existing test in both files would now 401 before ever reaching the route
under test.

`uv run pytest`: 217/217 passing (210 before this batch, +7 new).

## Verification against the real running server

Ran `scripts/dev.sh up` for real (real Postgres/Redis/RQ worker, real
`WEB_UI_USERNAME=admin` / a placeholder `WEB_UI_PASSWORD` set in this
machine's own `.env`) and hit it with `curl`, not just `TestClient`:

- No `Authorization` header at all → `401`, `WWW-Authenticate: Basic`
  present.
- Wrong password (`-u admin:wrongpass`) → `401`.
- Correct credentials (`-u admin:change-me-please`) → `200`, real page
  content (`<title>Target profiles</title>`) returned.
- `/campaigns`, `/suppressions`, and `/runs` (not just `/targets`) each
  independently confirmed `401` with no credentials — the app-level
  `dependencies=` really does apply to every router mounted on `app`,
  not just the one route exercised in the walkthrough above.
- `scripts/dev.sh up`'s new preflight check confirmed separately: with
  the `.env` credentials temporarily blanked, the script refused to
  start (before touching Docker) with the intended message instead of
  the old silent "Ready" outcome.

Both containers stopped afterward (`scripts/dev.sh down`); no state left
running.

## Not done here, still real gaps

- **One shared credential for the whole team** — see "on purpose" above.
  Revisit if this ever needs per-user audit of who *viewed* something,
  not just who *approved* it.
- **No rate limiting on login attempts.** HTTP Basic has no concept of
  lockout; a determined attacker who can already reach this
  (localhost-only today, per the README) could brute-force it. Not worth
  building against a threat model that doesn't exist yet for a
  localhost-only tool — revisit if this is ever exposed past localhost.
- **Credentials travel in cleartext unless the connection itself is
  TLS.** Basic Auth base64-encodes, it does not encrypt. Fine over
  localhost; would need HTTPS in front of this (a reverse proxy, not
  application code) before it's reachable over a real network.
