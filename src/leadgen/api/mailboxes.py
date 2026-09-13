"""Mailbox health page (ROADMAP.md item 6, docs/18): "no way to tell from
inside the app if Gmail has started flagging a sending account as spam."

A real spam-reputation signal (Postmaster Tools-style deliverability
data) isn't available through the `gmail.send` scope this system holds,
and getting it would mean the same restricted-scope/CASA path step 7 is
already deliberately deferred on (see ROADMAP.md) -- not solved here.
What *is* buildable now, with zero new scopes: token validity
(`send/oauth.py`'s `validate_token_health()`, a real call made live on
page load, not cached), today's send volume against the daily cap
(`db/repository.count_sent_today`), and whether the mailbox's last real
send attempt actually succeeded (`Mailbox.last_send_error`/
`last_send_error_at`, written by `db/orchestration.py`'s `on_sent`/
`on_error`). Together these are the things that would actually change
right before Gmail starts rejecting a mailbox's mail -- a dead token, a
mailbox already at its cap, or Gmail itself starting to bounce/reject
sends -- surfaced in one place instead of only visible in log files.

Read-only, like `/runs` -- no route here writes anything.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from html import escape

import httpx
from dotenv import load_dotenv
from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from leadgen.api.nav import nav_bar
from leadgen.db.models import Mailbox
from leadgen.db.repository import count_sent_today
from leadgen.db.session import session_scope
from leadgen.send.crypto import TokenCipher
from leadgen.send.oauth import TokenHealth, validate_token_health

router = APIRouter()

__all__ = ["router"]

_STYLE = """
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #1f2328; background: #ffffff; }
  h1 { font-size: 20px; }
  table { width: 100%; border-collapse: collapse; }
  th { text-align: left; font-size: 12px; text-transform: uppercase; color: #57606a; padding: 8px; border-bottom: 2px solid #d0d7de; }
  td { padding: 10px 8px; border-bottom: 1px solid #eaeef2; vertical-align: top; font-size: 14px; }
  .meta { color: #57606a; font-size: 13px; margin-bottom: 16px; }
"""


def _badge(text: str, color: str) -> str:
    return (
        f'<span style="background:{color}1a;color:{color};border:1px solid {color}55;'
        f'padding:2px 8px;border-radius:999px;font-size:12px;font-weight:600;">{escape(text)}</span>'
    )


def _page(body: str) -> str:
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Mailboxes</title>
<style>{_STYLE}</style>
</head>
<body>
  {nav_bar("/mailboxes")}
  {body}
</body>
</html>"""


def _token_health_badge(health: TokenHealth | None) -> str:
    if health is None:
        return _badge("unknown", "#57606a")
    if health.healthy:
        return _badge("token ok", "#1a7f37")
    return _badge(f"token dead: {health.reason}", "#cf222e")


def _cap_badge(sent_today: int, daily_cap: int) -> str:
    if sent_today >= daily_cap:
        return _badge(f"{sent_today}/{daily_cap} (at cap)", "#cf222e")
    if sent_today >= daily_cap * 0.8:
        return _badge(f"{sent_today}/{daily_cap}", "#9a6700")
    return _badge(f"{sent_today}/{daily_cap}", "#1a7f37")


def _last_error_html(mailbox: Mailbox) -> str:
    if not mailbox.last_send_error:
        return '<span style="color:#57606a;">none since last success</span>'
    when = mailbox.last_send_error_at.strftime("%Y-%m-%d %H:%M") if mailbox.last_send_error_at else "unknown time"
    return f'<span style="color:#cf222e;">{escape(mailbox.last_send_error)}</span><br><span style="color:#57606a;font-size:12px;">{when}</span>'


def _check_token_health(mailbox: Mailbox) -> TokenHealth | None:
    """A real call to Google's token endpoint per mailbox, on every page
    load -- deliberately not cached: this is an occasionally-viewed admin
    page, not a hot path, and a stale "looked fine 10 minutes ago" beats
    the purpose of a health check. Returns None (rendered as "unknown")
    rather than raising on anything that stops the check itself from
    running -- missing config (GOOGLE_OAUTH_CLIENT_ID unset in a dev
    environment that hasn't set up sending yet), a corrupted/undecryptable
    stored token, or a transient network failure reaching Google -- so
    one mailbox's check failing doesn't 500 the whole page for every
    other mailbox. `validate_token_health()` itself already turns a
    real *rejection* from Google (dead token) into `healthy=False`
    rather than raising; this only guards the layers around that call."""
    try:
        load_dotenv()
        client_id = os.environ["GOOGLE_OAUTH_CLIENT_ID"]
        client_secret = os.environ["GOOGLE_OAUTH_CLIENT_SECRET"]
        cipher = TokenCipher(os.environ["TOKEN_ENCRYPTION_KEY"])
        refresh_token = cipher.decrypt(mailbox.oauth_refresh_token_encrypted)
        with httpx.Client(timeout=15.0) as client:
            return validate_token_health(
                client, client_id=client_id, client_secret=client_secret, refresh_token=refresh_token
            )
    except Exception:
        return None


def _render(mailboxes: list[Mailbox], sent_today: dict, health: dict) -> str:
    rows_html = "".join(
        f"""<tr>
          <td>{escape(mailbox.email_address)}{'' if mailbox.is_active else ' ' + _badge('inactive', '#57606a')}</td>
          <td>{_token_health_badge(health.get(mailbox.id))}</td>
          <td>{_cap_badge(sent_today.get(mailbox.id, 0), mailbox.daily_cap)}</td>
          <td>{_last_error_html(mailbox)}</td>
        </tr>"""
        for mailbox in mailboxes
    ) or '<tr><td colspan="4" style="text-align:center;color:#57606a;padding:24px;">No mailboxes registered yet -- see scripts/authorize_mailbox.py.</td></tr>'

    body = f"""
  <h1>Mailboxes</h1>
  <div class="meta">{len(mailboxes)} registered &middot; token health checked live on every page load &middot;
  last-send-error clears automatically on the next real success &middot; see docs/18 for what this can and can't detect.</div>
  <table>
    <thead><tr><th>Mailbox</th><th>Token</th><th>Sent today</th><th>Last send error</th></tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
    """
    return _page(body)


@router.get("/mailboxes", response_class=HTMLResponse)
def list_mailboxes() -> HTMLResponse:
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        mailboxes = list(session.execute(select(Mailbox).order_by(Mailbox.email_address)).scalars())
        sent_today = {mailbox.id: count_sent_today(session, mailbox.id, now) for mailbox in mailboxes}
        health = {mailbox.id: _check_token_health(mailbox) for mailbox in mailboxes}
        html = _render(mailboxes, sent_today, health)
    return HTMLResponse(html)
