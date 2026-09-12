# How to use this, step by step

This is the operator's guide — the exact commands to run, in order, to go
from a fresh clone to a real test email sent through your own Gmail
account. For *what's built and why*, see [README.md](README.md); for the
full reasoning behind every non-obvious decision, see [docs/](docs/).

Current build status: steps 1–6 of PROJECT.md's build order. You can run
discover → enrich → CSV today, and you can authorize a mailbox and send a
real test email — but nothing yet turns a lead list into an actual
outbound campaign. See step 8 below for exactly where that line is.

---

## 1. Install and start the local environment

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
Docker Desktop first (`open -a Docker` on macOS), wait a few seconds, and
retry.

---

## 2. Get a Google Cloud OAuth client (one-time)

You need this before anything in step 6 (sending) works. Discover/enrich
(steps 1–5) don't need it at all.

1. Go to [console.cloud.google.com](https://console.cloud.google.com) and
   create a project (or use an existing one).
2. **APIs & Services → Library** → search "Gmail API" → **Enable**.
3. **APIs & Services → OAuth consent screen**:
   - Publishing status: leave as **Testing** for now.
   - Under **Audience** (or the "Test users" card on older console
     layouts) → **+ Add users** → add your own Gmail address. This is
     required even for the account that owns the project — owning the
     project doesn't automatically grant access while the app is in
     Testing status.
   - Under **Data access** (or the Scopes step of the older wizard) →
     **Add or remove scopes** → search `gmail.send` → check
     `https://www.googleapis.com/auth/gmail.send` → **Update** → **Save**.
4. **APIs & Services → Credentials → + Create credentials → OAuth client
   ID**:
   - Application type: **Desktop app** (not Web application — there's no
     hosted redirect URI in this project, and Desktop app clients get a
     working loopback redirect automatically with nothing to configure).
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

---

## 3. Generate the token-encryption key (one-time)

Refresh tokens are stored encrypted, never in plaintext:

```bash
uv run python -c "from leadgen.send.crypto import generate_key; print(generate_key())"
```

Paste the output into `.env`:
```
TOKEN_ENCRYPTION_KEY=<paste>
```

---

## 4. Run the discover → enrich → CSV pipeline

This is steps 1–5 — find businesses, crawl their sites, qualify them,
write a CSV. No Google credentials needed for this part.

```python
import httpx
from pathlib import Path

from leadgen.config.loader import load_business_types, load_target_profile
from leadgen.discover.geocode import NominatimClient
from leadgen.pipeline import export_csv, run_target_profile

business_types = load_business_types(Path("config/business_types.yaml"))
profile = load_target_profile(Path("targets/dentists-gurugram.yaml"), business_types)

user_agent = "your-bot/0.1 (+contact: you@example.com)"
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

export_csv(rows, Path("leads.csv"))
```

Open `leads.csv` and actually read it — PROJECT.md's build order treats
this as a mandatory human checkpoint before trusting anything downstream.
**Live network reachability to Overpass/Nominatim from your machine
hasn't been independently confirmed** — see
[docs/03](docs/03-overpass-discoverer.md) if this hangs or times out.

---

## 5. Authorize a mailbox (one-time per sending Gmail account)

Requires steps 2–3 done (Client ID/Secret + encryption key in `.env`) and
step 1's Postgres running and migrated.

```bash
uv run python scripts/authorize_mailbox.py <short-name> <email-address>
# example:
uv run python scripts/authorize_mailbox.py sales1 you@yourdomain.com
```

What happens:
1. Your browser opens to Google's consent screen.
2. Log in with the Gmail account you want to send *from* (must be added
   as a test user in step 2, or you'll get `Error 403: access_denied`).
3. You'll likely see an "unverified app" warning — click **Advanced →
   Go to [app name] (unsafe)**. This is expected while the app is in
   Testing status; it doesn't mean anything is actually wrong.
4. Approve the `gmail.send` permission.
5. The script catches the redirect, exchanges the code for a refresh
   token, encrypts it, and inserts a row into `mailboxes`.

Run this once per mailbox you want in a `sender_pool`. If you re-run it
for an account you've already authorized, Google may not return a new
refresh token unless you first revoke the app's access at
[myaccount.google.com/permissions](https://myaccount.google.com/permissions)
— the script tells you this explicitly if it happens.

---

## 6. Send a real test email

Confirms the whole chain — stored refresh token → fresh access token →
actual Gmail API delivery — works before you trust it for anything real.

```bash
uv run python scripts/send_test_email.py <mailbox-name> <to-address>
# example:
uv run python scripts/send_test_email.py sales1 you@yourdomain.com
```

A successful run prints a real Gmail message id and the email will
actually arrive in the recipient's inbox. **This script bypasses the
suppression and daily-cap checks on purpose** — it's a connectivity
smoke test, not the production send path. Never point it at a real lead.

---

## 7. What you can't do yet

Nothing past this point exists in the codebase — don't go looking for it:

- **No orchestration loop.** Nothing reads a CSV of qualified leads and
  turns it into `campaigns`/`messages` rows, and nothing loops over
  approved messages calling `send.queue.check_sendable()` +
  `send.gmail.send_message()` for you. Steps 5 and 6 exist as separate,
  manually-run pieces right now.
- **No review/approval UI.** PROJECT.md's hard rule — no message sends
  without a human clicking approve on the exact rendered text — has no
  UI to click yet. That's step 8 (FastAPI + minimal review UI).
- **No bounce/reply monitoring.** Step 7. Needs the restricted
  `gmail.readonly`/`gmail.modify` scopes (a CASA security review, unlike
  the `gmail.send`-only scope used so far) and isn't started.
- **The step-5 hand-review hasn't happened.** PROJECT.md frames "read 200
  rows by hand" as a gate before building anything past CSV export. Step
  6 was built ahead of that on direct instruction (see
  [docs/06](docs/06-gmail-oauth-and-send-queue.md)) — worth doing that
  review before investing in the orchestration loop above.

For the reasoning behind every decision mentioned here, see the matching
file in [docs/](docs/README.md).
