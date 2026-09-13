# How to use this, step by step

The operator's runbook — every command you need, in the order you need
it, from a fresh clone to a real sent campaign. If you ever come back
after a break and can't remember where you left off, start at the
section header that matches what you last did and pick up from the next
one.

For *what's built and why*, see [README.md](README.md) / [MANUAL.md](MANUAL.md);
for the reasoning behind any non-obvious decision, see [docs/](docs/).

**Fast path, once Part A's one-time setup is done:**

```bash
scripts/dev.sh up      # docker + migrations + web UI + worker, one shot; opens the browser
scripts/dev.sh status  # what's currently running
scripts/dev.sh logs    # tail the web UI's and worker's output
scripts/dev.sh down    # stop everything cleanly
```

This replaces manually running `docker compose up -d`, `alembic upgrade
head`, `uvicorn ...`, and `rq worker ...` in separate terminals every
time (Part B's setup step below still explains what each piece does and
how to run them by hand, if you ever need to).

Two parts:

- **Part A — one-time setup.** Do this once per machine / once per
  mailbox you want to send from. Skip straight to Part B if you've
  already done this.
- **Part B — the per-campaign workflow.** Do this every time you want to
  target a new city/vertical or run a new campaign. This is the loop
  you'll repeat.

---

# Part A — one-time setup

## A1. Install and start the local environment

```bash
uv sync                        # installs Python deps into .venv
cp .env.example .env           # only if you don't already have one
docker compose up -d           # postgres:16 + redis:7 (needs Docker Desktop running)
uv run alembic upgrade head    # creates all tables
uv run pytest                  # should be all green before continuing
```

If `uv sync` times out downloading Python or packages, retry with
`UV_HTTP_TIMEOUT=240 uv sync` — this environment's network has a slow
ramp-up on large transfers, it's not actually stuck.

If `docker compose up -d` fails to connect to the Docker daemon, start
Docker Desktop first (`open -a Docker` on macOS), wait a few seconds,
and retry.

Whenever you come back after time away, first confirm Postgres/Redis are
actually up:

```bash
docker compose ps               # both should say "healthy"
```

## A2. Get a Google Cloud OAuth client (one-time)

You need this before anything involving sending mailboxes (A4 onward).
Discovering/crawling (Part B, steps 1–3) doesn't need it at all.

1. Go to [console.cloud.google.com](https://console.cloud.google.com) and
   create a project (or use an existing one).
2. **APIs & Services → Library** → search "Gmail API" → **Enable**.
3. **APIs & Services → OAuth consent screen**:
   - Publishing status: leave as **Testing** for now.
   - Under **Audience** (or "Test users" on older console layouts) →
     **+ Add users** → add your own Gmail address. Required even for the
     account that owns the project.
   - Under **Data access** (or Scopes) → **Add or remove scopes** →
     search `gmail.send` → check
     `https://www.googleapis.com/auth/gmail.send` → **Update** → **Save**.
4. **APIs & Services → Credentials → + Create credentials → OAuth client
   ID**:
   - Application type: **Desktop app** (not Web application — Desktop
     app clients get a working loopback redirect automatically).
   - Name it whatever you like, click **Create**.
   - Copy the **Client ID** and **Client Secret** shown.
5. Put them in `.env`:
   ```
   GOOGLE_OAUTH_CLIENT_ID=<paste>
   GOOGLE_OAUTH_CLIENT_SECRET=<paste>
   ```
   Never paste the secret anywhere it'll be logged (chat, commit
   messages, issue trackers) — `.env` is gitignored specifically so this
   is safe to put there.

## A3. Generate the token-encryption key (one-time)

Refresh tokens are stored encrypted, never in plaintext:

```bash
uv run python -c "from leadgen.send.crypto import generate_key; print(generate_key())"
```

Paste the output into `.env`:
```
TOKEN_ENCRYPTION_KEY=<paste>
```

## A4. Authorize a mailbox (once per Gmail account you want to send from)

Requires A2–A3 done.

```bash
uv run python scripts/authorize_mailbox.py <short-name> <email-address>
# example:
uv run python scripts/authorize_mailbox.py sales1 you@yourdomain.com
```

What happens:
1. Your browser opens to Google's consent screen.
2. Log in with the Gmail account you want to send *from* (must be added
   as a test user in A2, or you'll get `Error 403: access_denied`).
3. You'll likely see an "unverified app" warning — click **Advanced →
   Go to [app name] (unsafe)**. Expected while the app is in Testing
   status.
4. Approve the `gmail.send` permission.
5. The script catches the redirect, exchanges the code for a refresh
   token, encrypts it, and inserts a row into `mailboxes`.

Run this once per mailbox you want available as a sender. Re-running it
for an already-authorized account may not return a new refresh token
unless you first revoke access at
[myaccount.google.com/permissions](https://myaccount.google.com/permissions)
— the script tells you if this happens.

## A5. Send a real test email (confirms the mailbox actually works)

```bash
uv run python scripts/send_test_email.py <mailbox-name> <to-address>
# example:
uv run python scripts/send_test_email.py sales1 you@yourdomain.com
```

A successful run prints a real Gmail message id and the email actually
arrives. **This bypasses suppression/cap checks on purpose** — it's a
connectivity smoke test, never point it at a real lead.

**Part A is done once you've reached this point with at least one
mailbox authorized.** Everything below is the workflow you repeat.

---

# Part B — the per-campaign workflow

**Shortcut: `scripts/dev.sh up` does everything below (web UI + worker,
both backgrounded) in one command and opens the browser for you** — skip
to B1 if you use it. The manual steps below are what it's running under
the hood, useful if you want them in foreground terminals instead.

Start the web UI once per terminal session — it stays running in its
own terminal while you use the rest of this section from a browser:

```bash
uv run uvicorn leadgen.api.review:app --reload
```

Leave that running and open [http://127.0.0.1:8000](http://127.0.0.1:8000)
in a browser. All four pages (review, run history, scan-builder,
campaigns) live on this one server.

**Also start a background worker, in a second terminal**, if you want
to use the campaigns page's **Send** button (B6 below) instead of the
terminal (B7). Sending can take hours (90–600s between each message), so
the button doesn't run it inline — it hands the job to this worker:

```bash
uv run rq worker leadgen -u redis://localhost:6379/0
```

Leave this running too. On macOS you may hit a crash on the first job
(`Work-horse terminated unexpectedly ... signal 6`, from an unrelated
Objective-C fork-safety check some networking library trips) — if so,
set this and restart the worker:

```bash
OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES uv run rq worker leadgen -u redis://localhost:6379/0
```

You can skip this worker entirely if you're always going to send from
the terminal (B7) instead of the UI button.

## B1. Create or pick a target profile — 🌐 UI

`http://127.0.0.1:8000/targets`

Either pick an existing `targets/*.yaml` file from the list, or
**New target** to build one from a form (business type, location,
filters, offer, sender pool). This just writes a YAML file — it doesn't
run anything yet.

(Terminal alternative: hand-write a file in `targets/`, same shape as
the existing examples.)

## B2. Run the scan — 💻 terminal

The scan-builder page deliberately does **not** trigger this itself —
discovering + crawling can take minutes, too long for a single web
request. Run it from the terminal instead, in a **second terminal tab**
(leave uvicorn running in the first):

```bash
uv run python scripts/run_pipeline.py targets/<your-profile>.yaml leads.csv
```

This discovers businesses, crawls their sites, qualifies them, persists
everything to Postgres (a `target_runs` row + businesses/contacts/
signals), and writes `leads.csv` too. Add `--no-db` if you only want the
CSV and don't want this run to show up in `/runs`.

## B3. Read the results by hand — 🌐 UI

`http://127.0.0.1:8000/runs` (every past scan) or `http://127.0.0.1:8000/`
(the CSV directly, `?csv=leads.csv&profile=targets/<your-profile>.yaml`).

**Actually read these before trusting them** — PROJECT.md treats this as
a mandatory checkpoint, not a formality. Use the Reject button on
anything OSM mistagged (adds it to that profile's `exclude_domains` so
it won't reappear on a re-scan).

## B4. Build a campaign — 🌐 UI

`http://127.0.0.1:8000/campaigns/new`

Pick the completed run from B2, an offer (from `config/offers/`), and a
sender pool (checkboxes of mailboxes authorized in A4). This creates the
`campaigns` row and renders one message per qualified business.

## B5. Edit and approve each message — 🌐 UI

`http://127.0.0.1:8000/campaigns/{id}`

Read the full rendered subject/body per message. While a message is
still **queued** you can edit its text directly on this page. Click
**Approve** (typing your name first — `approved_by` is not optional) or
**Reject**. Nothing sends yet — approving just marks a message ready.

## B6. Send — 🌐 UI (needs the worker from above)

Scroll down on the same `/campaigns/{id}` page — once at least one
message is `approved`, a **Send** section appears showing a per-mailbox
breakdown (approved count, sent today, how many would go out right now,
how many would be cap-blocked). **This part is read-only and safe to
look at any time** — it's the same zero-write preview as the terminal's
no-flag mode (B7).

To actually send: type `send real email` into the confirm box and click
**Start sending**. This hands the job to the background worker and
redirects back to the same page, which now shows "A send is currently
queued/started" and auto-refreshes every 15s. Progress is just the
messages table below updating live — no separate progress bar. Only one
send can run per campaign at a time; clicking again while one's already
running is refused, not queued twice.

## B7. Send — 💻 terminal (works without the worker running)

The same three modes either way — from a terminal:

```bash
uv run python scripts/send_approved_messages.py           # safe preview, zero writes -- same as B6's table
uv run python scripts/send_approved_messages.py --dry-run  # full loop, fake Gmail, REAL reservation writes -- disposable test data ONLY, never a real campaign
uv run python scripts/send_approved_messages.py --live     # the real thing, across every approved message system-wide (not just one campaign)
```

`--live` shows a warning and asks you to type back `send real email`
before anything happens. Then, for each approved message (one mailbox's
queue at a time): sleeps a random 90–600s, re-checks suppression/cap
against fresh state, reserves the slot, sends via Gmail, and records
`sent_at`/`gmail_message_id`/`gmail_thread_id`. A message blocked by the
mailbox's daily cap stays `approved` and will go out next time (cap
resets at UTC midnight); a suppressed contact's message stays `approved`
and needs a human decision, not a retry.

Either way (UI button or `--live`), this can take a while for a full
queue — a mailbox with 20 approved messages is roughly up to 20 × 600s ≈
3.3 hours worst case. Let it run; there's no harm leaving it, and
re-running later picks up wherever it left off (already-`sent` messages
aren't resent). The one difference: the UI button only ever acts on that
one campaign; the terminal's `--live` acts on every approved message
across every campaign at once.

## B9. Monitor replies/bounces — ❌ not built yet

There's no step here yet. Nothing currently marks a contact
suppressed automatically from a reply, bounce, or unsubscribe — that's
step 7 of PROJECT.md's build order (needs a Google CASA review for the
restricted Gmail scopes it requires). Until it exists, suppression is
manual: insert a row into `suppressions` yourself if someone asks not to
be contacted again.

---

## Quick reference: which command, right now?

| I want to... | Run this |
|---|---|
| Start everything (docker + migrations + web UI + worker) | `scripts/dev.sh up` |
| Stop everything cleanly | `scripts/dev.sh down` |
| See what's running / tail logs | `scripts/dev.sh status` / `scripts/dev.sh logs` |
| Confirm Postgres/Redis are up (manual) | `docker compose ps` |
| Start the web UI (manual) | `uv run uvicorn leadgen.api.review:app --reload` |
| Start the background worker (manual, needed for the UI's Send button) | `uv run rq worker leadgen -u redis://localhost:6379/0` |
| Run a new scan | `uv run python scripts/run_pipeline.py targets/<profile>.yaml leads.csv` |
| Check what would send, safely | `uv run python scripts/send_approved_messages.py` |
| Test the send loop, disposable data only | `uv run python scripts/send_approved_messages.py --dry-run` |
| Actually send approved messages (all campaigns) | `uv run python scripts/send_approved_messages.py --live` |
| Actually send one campaign's approved messages | Click **Start sending** on `/campaigns/{id}` (needs the worker) |
| Authorize a new sending mailbox | `uv run python scripts/authorize_mailbox.py <name> <email>` |
| Run the test suite | `uv run pytest` |
| Apply a new migration | `uv run alembic upgrade head` |

For the reasoning behind any decision mentioned here, see the matching
file in [docs/](docs/README.md) — docs/10 (scan-builder), docs/11
(campaigns + approval), and docs/12 (the orchestration loop) cover
everything in Part B.
