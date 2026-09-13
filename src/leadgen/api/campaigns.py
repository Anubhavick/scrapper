"""Campaign creation + message-approval UI (docs/11): the third of the
three UI pages, and the piece that finally gives the hard rule "no send
without a human clicking approve" something to click.

`/campaigns/new` turns a target_run's qualified leads into a `campaigns`
row and one rendered `messages` row per lead (`db/campaigns.py` does the
actual work); `/campaigns/{id}` is the approval screen -- read the full
rendered subject/body, Approve or Reject each one. Nothing here sends
anything: that's a separate orchestration step (see CLAUDE.md) that reads
`approved` messages and calls `send/queue.py` + `send/gmail.py` -- kept
out of a web request handler on purpose, same reasoning as the
scan-builder page not triggering a scan (docs/10): a real send touches
external mailboxes/recipients and has to run under the 90-600s delay
between messages, which has no place inside an HTTP request/response
cycle.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from html import escape
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select

from leadgen.api.nav import PAGE_SIZE, nav_bar, pagination_bar
from leadgen.api.targets import load_registries
from leadgen.db.campaigns import CampaignError, create_campaign, generate_campaign_messages
from leadgen.db.models import Business, Campaign, CampaignMailbox, Contact, Mailbox, Message, TargetRun, TargetRunBusiness
from leadgen.db.orchestration import preview_approved_messages
from leadgen.db.session import session_scope
from leadgen.jobs import CONFIRMATION_PHRASE, send_campaign_messages_job
from leadgen.queue import get_queue

_ACTIVE_JOB_STATUSES = {"queued", "started", "deferred", "scheduled"}

router = APIRouter()

_STYLE = """
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #1f2328; background: #ffffff; }
  h1 { font-size: 20px; }
  table { width: 100%; border-collapse: collapse; }
  th { text-align: left; font-size: 12px; text-transform: uppercase; color: #57606a; padding: 8px; border-bottom: 2px solid #d0d7de; }
  td { padding: 10px 8px; border-bottom: 1px solid #eaeef2; vertical-align: top; font-size: 14px; }
  .meta { color: #57606a; font-size: 13px; margin-bottom: 16px; }
  fieldset { border: 1px solid #d0d7de; border-radius: 6px; margin-bottom: 16px; padding: 12px 16px; }
  legend { font-size: 13px; font-weight: 600; padding: 0 6px; }
  label { display: block; font-size: 13px; margin: 10px 0 4px; }
  label.inline { display: inline-block; margin: 6px 12px 6px 0; font-weight: normal; }
  input[type=text], select { width: 100%; max-width: 480px; padding: 6px 8px; border: 1px solid #d0d7de; border-radius: 6px; font-size: 14px; box-sizing: border-box; }
  .help { color: #57606a; font-size: 12px; margin-top: 2px; }
  .errors { background: #fff0ef; border: 1px solid #cf222e55; color: #cf222e; padding: 10px 14px; border-radius: 6px; margin-bottom: 16px; }
  .notice { background: #ddf4ff; border: 1px solid #54aeff55; padding: 10px 14px; border-radius: 6px; margin-bottom: 16px; }
  .btn { padding: 8px 16px; border-radius: 6px; border: 1px solid #1a7f37; background: #1a7f37; color: white; cursor: pointer; font-size: 14px; }
  .btn-reject { padding: 6px 12px; border-radius: 6px; border: 1px solid #cf222e; background: #cf222e; color: white; cursor: pointer; font-size: 12px; }
  .btn-secondary { padding: 6px 12px; border-radius: 6px; border: 1px solid #d0d7de; background: #f6f8fa; color: #1f2328; text-decoration: none; font-size: 13px; }
  pre { background: #f6f8fa; border: 1px solid #d0d7de; border-radius: 6px; padding: 12px; overflow-x: auto; font-size: 13px; white-space: pre-wrap; }
  details summary { cursor: pointer; color: #0969da; font-size: 13px; }
"""

_STATUS_COLORS = {
    "queued": "#9a6700",
    "approved": "#1a7f37",
    "sent": "#0969da",
    "bounced": "#cf222e",
    "replied": "#8250df",
    "rejected": "#57606a",
}


def _badge(text: str, color: str) -> str:
    return (
        f'<span style="background:{color}1a;color:{color};border:1px solid {color}55;'
        f'padding:2px 8px;border-radius:999px;font-size:12px;font-weight:600;">{escape(text)}</span>'
    )


def _page(title: str, active: str, body: str, extra_head: str = "") -> str:
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{escape(title)}</title>
<style>{_STYLE}</style>
{extra_head}
</head>
<body>
  {nav_bar(active)}
  {body}
</body>
</html>"""


# ---------------------------------------------------------------- index


def _render_index(rows: list[tuple], page: int = 1, total: int = 0) -> str:
    def row_html(campaign: Campaign, target_name: str, counts: dict, mailbox_names: list[str]) -> str:
        total = sum(counts.values())
        badges = " ".join(
            _badge(f"{status}: {count}", _STATUS_COLORS.get(status, "#57606a"))
            for status, count in sorted(counts.items())
        ) or "<em>no messages generated</em>"
        return f"""
        <tr>
          <td><strong>{escape(campaign.name or str(campaign.id)[:8])}</strong><br>
              <span style="color:#57606a;font-size:12px;">target: {escape(target_name)} &middot; offer: {escape(campaign.offer_id)}</span></td>
          <td>{escape(", ".join(mailbox_names)) or "<em>none</em>"}</td>
          <td>{total}</td>
          <td>{badges}</td>
          <td><a href="/campaigns/{campaign.id}" class="btn-secondary">Open</a></td>
        </tr>
        """

    body_rows = "".join(row_html(*r) for r in rows) or (
        '<tr><td colspan="5" style="text-align:center;color:#57606a;padding:24px;">'
        "No campaigns yet -- create one below.</td></tr>"
    )
    pager = pagination_bar(page, total, "/campaigns", page_size=PAGE_SIZE)
    body = f"""
  <h1>Campaigns</h1>
  <div class="meta">{total} campaign(s) &middot; <a href="/campaigns/new" class="btn-secondary">+ New campaign</a></div>
  <table>
    <thead><tr><th>Campaign</th><th>Sender pool</th><th>Messages</th><th>Status breakdown</th><th></th></tr></thead>
    <tbody>{body_rows}</tbody>
  </table>
  {pager}
    """
    return _page("Campaigns", "/campaigns", body)


@router.get("/campaigns", response_class=HTMLResponse)
def list_campaigns(page: int = 1) -> HTMLResponse:
    page = max(1, page)
    with session_scope() as session:
        total = session.execute(select(func.count()).select_from(Campaign)).scalar_one()
        campaigns = (
            session.execute(
                select(Campaign)
                .order_by(Campaign.created_at.desc())
                .offset((page - 1) * PAGE_SIZE)
                .limit(PAGE_SIZE)
            )
            .scalars()
            .all()
        )
        rows = []
        for campaign in campaigns:
            target_run = session.get(TargetRun, campaign.target_run_id)
            target_name = target_run.target_name if target_run else "(deleted run)"
            counts = dict(
                session.execute(
                    select(Message.status, func.count())
                    .where(Message.campaign_id == campaign.id)
                    .group_by(Message.status)
                ).all()
            )
            mailbox_names = (
                session.execute(
                    select(Mailbox.name)
                    .join(CampaignMailbox, CampaignMailbox.mailbox_id == Mailbox.id)
                    .where(CampaignMailbox.campaign_id == campaign.id)
                )
                .scalars()
                .all()
            )
            rows.append((campaign, target_name, counts, mailbox_names))
        html = _render_index(rows, page, total)
    return HTMLResponse(html)


# ------------------------------------------------------------------ new


def _eligible_target_runs(session) -> list[tuple[TargetRun, int]]:
    qualified_by_run = dict(
        session.execute(
            select(TargetRunBusiness.target_run_id, func.count())
            .where(TargetRunBusiness.qualified.is_(True))
            .group_by(TargetRunBusiness.target_run_id)
        ).all()
    )
    runs = session.execute(
        select(TargetRun).where(TargetRun.status == "completed").order_by(TargetRun.started_at.desc())
    ).scalars().all()
    return [(run, qualified_by_run.get(run.id, 0)) for run in runs if qualified_by_run.get(run.id, 0) > 0]


def _render_new_form(
    eligible_runs: list[tuple[TargetRun, int]],
    offers: list[str],
    mailboxes: list[Mailbox],
    errors: list[str],
    values: dict,
) -> str:
    errors_html = ""
    if errors:
        items = "".join(f"<li>{escape(e)}</li>" for e in errors)
        errors_html = f'<div class="errors"><strong>Fix the following:</strong><ul>{items}</ul></div>'

    if not eligible_runs:
        run_field = (
            '<div class="help">No completed run has any qualified business yet -- '
            'run a scan and get at least one qualified lead first (see <a href="/targets">Targets</a>).</div>'
        )
    else:
        options = "".join(
            f'<option value="{run.id}" {"selected" if str(run.id) == values.get("target_run_id") else ""}>'
            f'{escape(run.target_name)} -- {qualified_count} qualified (run {run.started_at.strftime("%Y-%m-%d %H:%M")})'
            f"</option>"
            for run, qualified_count in eligible_runs
        )
        run_field = f'<select name="target_run_id">{options}</select>'

    if not offers:
        offer_field = '<div class="help">No offers found in config/offers/ -- add one first.</div>'
    else:
        options = "".join(
            f'<option value="{escape(o)}" {"selected" if o == values.get("offer_id") else ""}>{escape(o)}</option>'
            for o in offers
        )
        offer_field = f'<select name="offer_id">{options}</select>'

    if not mailboxes:
        sender_field = (
            '<div class="help">No mailboxes authorized yet -- run scripts/authorize_mailbox.py first (see HOWTO.md).</div>'
        )
    else:
        boxes = "".join(
            f'<label class="inline"><input type="checkbox" name="sender_pool" value="{escape(m.name)}" '
            f'{"checked" if m.name in values.get("sender_pool", []) else ""}> {escape(m.name)} ({escape(m.email_address)})</label>'
            for m in mailboxes
        )
        sender_field = f"<div>{boxes}</div>"

    body = f"""
  <h1>New campaign</h1>
  <div class="meta">Turns a target run's qualified leads into rendered messages you approve one by one -- nothing is sent by creating this.</div>
  {errors_html}
  <form method="post" action="/campaigns">
    <fieldset>
      <legend>Source</legend>
      <label>Target run</label>
      {run_field}
      <label>Offer</label>
      {offer_field}
    </fieldset>
    <fieldset>
      <legend>Sender pool</legend>
      {sender_field}
    </fieldset>
    <fieldset>
      <legend>Name (optional)</legend>
      <input type="text" name="name" value="{escape(values.get('name', ''))}" placeholder="e.g. austin-dentists-round-1">
    </fieldset>
    <button type="submit" class="btn" {"disabled" if not eligible_runs or not offers or not mailboxes else ""}>Create campaign</button>
    <a href="/campaigns" class="btn-secondary" style="margin-left:8px;">Cancel</a>
  </form>
    """
    return _page("New campaign", "/campaigns", body)


@router.get("/campaigns/new", response_class=HTMLResponse)
def new_campaign_form() -> HTMLResponse:
    _, offers, load_errors = load_registries()
    with session_scope() as session:
        eligible = _eligible_target_runs(session)
        mailboxes = session.execute(select(Mailbox).where(Mailbox.is_active.is_(True))).scalars().all()
        html = _render_new_form(eligible, sorted(offers), list(mailboxes), load_errors, {})
    return HTMLResponse(html)


@router.post("/campaigns", response_class=HTMLResponse)
async def create_campaign_route(request: Request) -> HTMLResponse:
    form = await request.form()
    target_run_id_raw = form.get("target_run_id") or ""
    offer_id = form.get("offer_id") or ""
    sender_pool = form.getlist("sender_pool")
    name = (form.get("name") or "").strip() or None
    values = {"target_run_id": target_run_id_raw, "offer_id": offer_id, "sender_pool": sender_pool, "name": name or ""}

    errors: list[str] = []
    target_run_uuid = None
    try:
        target_run_uuid = uuid.UUID(target_run_id_raw)
    except ValueError:
        errors.append("Pick a target run.")

    business_types, offers, load_errors = load_registries()
    errors.extend(load_errors)
    offer = offers.get(offer_id)
    if offer is None and not errors:
        errors.append(f"Unknown offer_id {offer_id!r}.")

    if not sender_pool:
        errors.append("Pick at least one sender mailbox.")

    if errors:
        with session_scope() as session:
            eligible = _eligible_target_runs(session)
            mailboxes = list(session.execute(select(Mailbox).where(Mailbox.is_active.is_(True))).scalars().all())
            html = _render_new_form(eligible, sorted(offers), mailboxes, errors, values)
        return HTMLResponse(html)

    try:
        with session_scope() as session:
            campaign = create_campaign(
                session, target_run_id=target_run_uuid, offer_id=offer_id, sender_pool=sender_pool, name=name
            )
            session.flush()
            created, skipped = generate_campaign_messages(session, campaign, offer=offer)
            campaign_id = campaign.id
            created_count, skipped_count = len(created), len(skipped)
    except CampaignError as exc:
        with session_scope() as session:
            eligible = _eligible_target_runs(session)
            mailboxes = list(session.execute(select(Mailbox).where(Mailbox.is_active.is_(True))).scalars().all())
            html = _render_new_form(eligible, sorted(offers), mailboxes, [str(exc)], values)
        return HTMLResponse(html)

    notice = f"created={created_count}&skipped={skipped_count}"
    return RedirectResponse(url=f"/campaigns/{campaign_id}?{notice}", status_code=303)


# ------------------------------------------------------------------ send


def _send_job_id(campaign_id) -> str:
    return f"send-campaign-{campaign_id}"


def _active_send_job_status(campaign_id) -> str | None:
    """The RQ status string if a send job for this campaign is currently
    queued/running, else None. Called from a GET route (rendering the
    page) as well as the POST route (deciding whether to enqueue) --
    a Redis hiccup here should degrade to "no active job" rather than
    break the page, so failures are swallowed rather than raised."""
    try:
        job = get_queue().fetch_job(_send_job_id(campaign_id))
    except Exception:
        return None
    if job is None:
        return None
    status = job.get_status(refresh=True)
    return status if status in _ACTIVE_JOB_STATUSES else None


def _render_send_section(
    campaign: Campaign,
    preview: list[dict],
    mailbox_names_by_id: dict,
    active_status: str | None,
    send_error: str = "",
) -> str:
    error_html = f'<div class="errors">{escape(send_error)}</div>' if send_error else ""

    if active_status is not None:
        return f"""
    <fieldset>
      <legend>Send</legend>
      <div class="notice">A send is currently <strong>{escape(active_status)}</strong> for this campaign.
      This page refreshes itself every 15s -- progress is read straight from each message's status below,
      not from a separate progress bar.</div>
    </fieldset>
        """

    if not preview:
        return f"""
    <fieldset>
      <legend>Send</legend>
      {error_html}
      <div class="help">No approved messages ready to send yet -- approve some below first.</div>
    </fieldset>
        """

    rows = "".join(
        f"""<tr>
          <td>{escape(mailbox_names_by_id.get(row['mailbox_id'], str(row['mailbox_id'])[:8]))}</td>
          <td>{row['queued']}</td>
          <td>{row['sent_today']}/{row['daily_cap']}</td>
          <td>{row['would_send_now']}</td>
          <td>{row['would_be_cap_blocked']}</td>
        </tr>"""
        for row in preview
    )
    return f"""
    <fieldset>
      <legend>Send</legend>
      {error_html}
      <table style="margin-bottom:12px;">
        <thead><tr><th>Mailbox</th><th>Approved (this campaign)</th><th>Sent today (mailbox-wide)</th>
        <th>Would send now</th><th>Would be cap-blocked</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
      <div class="help">Sending is slow on purpose: a randomised 90&ndash;600s gap between each message, with
      suppression and the daily cap re-checked against fresh state right before each one goes out. This runs in
      the background once started &mdash; you can leave this page and come back.</div>
      <form method="post" action="/campaigns/{campaign.id}/send" style="margin-top:10px;">
        <label>Type &ldquo;{escape(CONFIRMATION_PHRASE)}&rdquo; to confirm, then start sending real email:</label>
        <input type="text" name="confirm" placeholder="{escape(CONFIRMATION_PHRASE)}">
        <button type="submit" class="btn" style="margin-top:8px;">Start sending</button>
      </form>
    </fieldset>
    """


@router.post("/campaigns/{campaign_id}/send", response_class=HTMLResponse)
def start_send(campaign_id: str, confirm: str = Form("")) -> HTMLResponse:
    try:
        campaign_uuid = uuid.UUID(campaign_id)
    except ValueError:
        return HTMLResponse(f"<p>Not a valid campaign id: {escape(campaign_id)}</p>", status_code=404)

    with session_scope() as session:
        campaign_exists = session.get(Campaign, campaign_uuid) is not None
    if not campaign_exists:
        return HTMLResponse(f"<p>No campaign found for id {escape(campaign_id)}</p>", status_code=404)

    if confirm.strip() != CONFIRMATION_PHRASE:
        error = quote(f'Confirmation phrase did not match "{CONFIRMATION_PHRASE}" -- nothing started.')
        return RedirectResponse(url=f"/campaigns/{campaign_id}?send_error={error}", status_code=303)

    if _active_send_job_status(campaign_uuid) is not None:
        error = quote("A send is already in progress for this campaign -- nothing new started.")
        return RedirectResponse(url=f"/campaigns/{campaign_id}?send_error={error}", status_code=303)

    try:
        get_queue().enqueue(
            send_campaign_messages_job,
            campaign_id=str(campaign_uuid),
            live=True,
            job_id=_send_job_id(campaign_uuid),
            job_timeout=12 * 60 * 60,
        )
    except Exception as exc:
        error = quote(f"Could not reach the job queue (Redis) -- is `docker compose up -d` running? {exc}")
        return RedirectResponse(url=f"/campaigns/{campaign_id}?send_error={error}", status_code=303)

    return RedirectResponse(url=f"/campaigns/{campaign_id}", status_code=303)


# ------------------------------------------------------------------ show


def _bulk_approve_phrase(queued_count: int) -> str:
    return f"approve all {queued_count}"


def _render_bulk_approve(campaign_id, approver: str, queued_count: int, bulk_error: str) -> str:
    """Only shown once there's more than one `queued` message -- this is
    for a large campaign's worth of tedium, not a replacement for the
    single-message Approve button. Typing back the exact count (not just
    clicking a button, and not a generic JS confirm() dialog -- this
    codebase has none, on purpose, everything server-rendered) is the
    same "type something back" friction `jobs.py`'s `CONFIRMATION_PHRASE`
    already uses for a real send, scaled to this smaller-but-still-
    irreversible action. It does **not** relax PROJECT.md's "no send
    without a human clicking approve on the exact rendered text" hard
    rule: every queued message's full subject/body is already rendered
    directly on this same page (in the editable textarea above, not
    behind a collapsed <details>), so approving all of them here approves
    text that was already fully visible, not text nobody looked at."""
    if queued_count < 2:
        return ""
    error_html = f'<div class="errors">{escape(bulk_error)}</div>' if bulk_error else ""
    phrase = _bulk_approve_phrase(queued_count)
    return f"""
  <form method="post" action="/campaigns/{campaign_id}/bulk-approve" style="margin:16px 0;padding:12px 16px;border:1px solid #d0d7de;border-radius:6px;">
    {error_html}
    <input type="hidden" name="approver" value="{escape(approver)}">
    <label style="font-size:13px;">Type <code>{escape(phrase)}</code> to approve all {queued_count} queued messages below at once:
      <input type="text" name="confirm" placeholder="{escape(phrase)}" style="width:220px;">
    </label>
    <button type="submit" class="btn" style="margin-left:8px;">Approve all queued ({queued_count})</button>
  </form>
    """


def _render_show(
    campaign: Campaign,
    target_name: str,
    mailbox_names: list[str],
    rows: list[tuple[Message, Contact, Business]],
    approver: str,
    notice: str,
    error: str = "",
    send_section: str = "",
    auto_refresh: bool = False,
    bulk_error: str = "",
) -> str:
    def message_row(message: Message, contact: Contact, business: Business) -> str:
        badge = _badge(message.status, _STATUS_COLORS.get(message.status, "#57606a"))
        actions = ""
        if message.status == "queued":
            content = f"""
            <form method="post" action="/campaigns/{campaign.id}/messages/{message.id}/edit" style="margin-bottom:8px;">
              <input type="hidden" name="approver" value="{escape(approver)}">
              <input type="text" name="subject" value="{escape(message.subject or '')}"
                     style="width:100%;padding:5px 8px;border:1px solid #d0d7de;border-radius:6px;font-size:13px;box-sizing:border-box;margin-bottom:6px;">
              <textarea name="body" rows="6"
                        style="width:100%;padding:6px 8px;border:1px solid #d0d7de;border-radius:6px;font-size:13px;font-family:inherit;box-sizing:border-box;">{escape(message.body or '')}</textarea>
              <button type="submit" class="btn-secondary" style="margin-top:6px;">Save changes</button>
            </form>
            """
            actions = f"""
            <form method="post" action="/campaigns/{campaign.id}/messages/{message.id}/approve" style="display:inline">
              <input type="hidden" name="approver" value="{escape(approver)}">
              <button type="submit" class="btn" style="padding:6px 12px;font-size:12px;">Approve</button>
            </form>
            <form method="post" action="/campaigns/{campaign.id}/messages/{message.id}/reject" style="display:inline">
              <input type="hidden" name="approver" value="{escape(approver)}">
              <button type="submit" class="btn-reject">Reject</button>
            </form>
            """
        else:
            content = f"""
            {escape(message.subject or "")}
            <details><summary>show body</summary><pre>{escape(message.body or "")}</pre></details>
            """
            if message.status == "approved":
                when = message.approved_at.strftime("%Y-%m-%d %H:%M") if message.approved_at else ""
                actions = f'<span class="help">by {escape(message.approved_by or "?")} at {when}</span>'
        return f"""
        <tr>
          <td><strong>{escape(business.name)}</strong></td>
          <td>{escape(contact.email)}</td>
          <td>{content}</td>
          <td>{badge}</td>
          <td>{actions}</td>
        </tr>
        """

    body_rows = "".join(message_row(*r) for r in rows) or (
        '<tr><td colspan="5" style="text-align:center;color:#57606a;padding:24px;">No messages in this campaign.</td></tr>'
    )
    queued_count = sum(1 for m, _, _ in rows if m.status == "queued")
    notice_html = f'<div class="notice">{escape(notice)}</div>' if notice else ""
    error_html = f'<div class="errors">{escape(error)}</div>' if error else ""
    bulk_approve_html = _render_bulk_approve(campaign.id, approver, queued_count, bulk_error)
    body = f"""
  <h1>Campaign -- {escape(campaign.name or str(campaign.id)[:8])}</h1>
  <div class="meta">target: {escape(target_name)} &middot; offer: {escape(campaign.offer_id)} &middot; sender pool: {escape(", ".join(mailbox_names))}</div>
  {notice_html}
  {error_html}
  <form method="get" action="/campaigns/{campaign.id}" style="margin-bottom:16px;">
    <label style="display:inline;font-size:13px;">Approving as:
      <input type="text" name="approver" value="{escape(approver)}" placeholder="your name" style="width:200px;display:inline;">
    </label>
    <button type="submit" class="btn-secondary">Set</button>
  </form>
  {send_section}
  {bulk_approve_html}
  <table>
    <thead><tr><th>Business</th><th>Contact</th><th>Subject / body</th><th>Status</th><th></th></tr></thead>
    <tbody>{body_rows}</tbody>
  </table>
    """
    extra_head = '<meta http-equiv="refresh" content="15">' if auto_refresh else ""
    return _page(f"Campaign -- {campaign.name or ''}", "/campaigns", body, extra_head=extra_head)


@router.get("/campaigns/{campaign_id}", response_class=HTMLResponse)
def show_campaign(
    campaign_id: str,
    approver: str = "",
    created: str = "",
    skipped: str = "",
    edit_error: str = "",
    send_error: str = "",
    bulk_error: str = "",
) -> HTMLResponse:
    try:
        campaign_uuid = uuid.UUID(campaign_id)
    except ValueError:
        return HTMLResponse(f"<p>Not a valid campaign id: {escape(campaign_id)}</p>", status_code=404)

    with session_scope() as session:
        campaign = session.get(Campaign, campaign_uuid)
        if campaign is None:
            return HTMLResponse(f"<p>No campaign found for id {escape(campaign_id)}</p>", status_code=404)

        target_run = session.get(TargetRun, campaign.target_run_id)
        target_name = target_run.target_name if target_run else "(deleted run)"
        campaign_mailboxes = session.execute(
            select(Mailbox.id, Mailbox.name)
            .join(CampaignMailbox, CampaignMailbox.mailbox_id == Mailbox.id)
            .where(CampaignMailbox.campaign_id == campaign.id)
        ).all()
        mailbox_names = [name for _, name in campaign_mailboxes]
        mailbox_names_by_id = dict(campaign_mailboxes)
        rows = session.execute(
            select(Message, Contact, Business)
            .join(Contact, Message.contact_id == Contact.id)
            .join(Business, Contact.business_id == Business.id)
            .where(Message.campaign_id == campaign.id)
            .order_by(Business.name)
        ).all()

        notice = ""
        if created:
            notice = f"Created {created} message(s)" + (f", skipped {skipped}" if skipped and skipped != "0" else "") + "."

        active_status = _active_send_job_status(campaign_uuid)
        preview = [] if active_status else preview_approved_messages(session, campaign_id=campaign_uuid)
        send_section = _render_send_section(campaign, preview, mailbox_names_by_id, active_status, send_error)

        html = _render_show(
            campaign,
            target_name,
            list(mailbox_names),
            list(rows),
            approver,
            notice,
            edit_error,
            send_section=send_section,
            auto_refresh=active_status is not None,
            bulk_error=bulk_error,
        )
    return HTMLResponse(html)


def _find_message(session, campaign_id: str, message_id: str) -> Message | None:
    try:
        campaign_uuid = uuid.UUID(campaign_id)
        message_uuid = uuid.UUID(message_id)
    except ValueError:
        return None
    message = session.get(Message, message_uuid)
    if message is None or message.campaign_id != campaign_uuid:
        return None
    return message


@router.post("/campaigns/{campaign_id}/messages/{message_id}/edit")
def edit_message(
    campaign_id: str,
    message_id: str,
    subject: str = Form(...),
    body: str = Form(...),
    approver: str = Form(""),
) -> HTMLResponse:
    """Lets a human rewrite one lead's rendered text before approving it --
    the same template + one-generated-line render is a solid default, not
    a guarantee it reads naturally for every business. Only touches a
    `queued` message: once approved, `messages.subject`/`body` is the
    record of exactly what a human signed off on (CLAUDE.md's
    schema-decisions note on why `messages` carries rendered text, not a
    template reference) -- editing after that would make that record
    wrong, so this refuses rather than silently allowing it."""
    subject = subject.strip()
    body = body.strip()
    approver = approver.strip()
    error = ""
    with session_scope() as session:
        message = _find_message(session, campaign_id, message_id)
        if message is None:
            return HTMLResponse("<p>Message not found.</p>", status_code=404)
        if message.status != "queued":
            error = "Only a queued message can be edited -- it's already been approved or rejected."
        elif not subject or not body:
            error = "Subject and body can't be empty."
        else:
            message.subject = subject
            message.body = body
    params = f"?approver={quote(approver)}"
    if error:
        params += f"&edit_error={quote(error)}"
    return RedirectResponse(url=f"/campaigns/{campaign_id}{params}", status_code=303)


@router.post("/campaigns/{campaign_id}/messages/{message_id}/approve")
def approve_message(campaign_id: str, message_id: str, approver: str = Form("")) -> HTMLResponse:
    approver = approver.strip()
    with session_scope() as session:
        message = _find_message(session, campaign_id, message_id)
        if message is None:
            return HTMLResponse("<p>Message not found.</p>", status_code=404)
        if not approver:
            return HTMLResponse(
                '<p>Type your name in "Approving as" before approving a message '
                f'-- <a href="/campaigns/{campaign_id}">go back</a>.</p>',
                status_code=400,
            )
        if message.status == "queued":
            message.status = "approved"
            message.approved_by = approver
            message.approved_at = datetime.now(timezone.utc)
    return RedirectResponse(url=f"/campaigns/{campaign_id}?approver={quote(approver)}", status_code=303)


@router.post("/campaigns/{campaign_id}/messages/{message_id}/reject")
def reject_message(campaign_id: str, message_id: str, approver: str = Form("")) -> HTMLResponse:
    approver = approver.strip()
    with session_scope() as session:
        message = _find_message(session, campaign_id, message_id)
        if message is None:
            return HTMLResponse("<p>Message not found.</p>", status_code=404)
        if message.status == "queued":
            message.status = "rejected"
    return RedirectResponse(url=f"/campaigns/{campaign_id}?approver={quote(approver)}", status_code=303)


@router.post("/campaigns/{campaign_id}/bulk-approve")
def bulk_approve(campaign_id: str, approver: str = Form(""), confirm: str = Form("")) -> HTMLResponse:
    """Approves every currently-`queued` message in one campaign at once
    -- see `_render_bulk_approve`'s docstring for why this doesn't relax
    the "no send without approving the exact rendered text" hard rule.
    Re-counts `queued` messages at submit time (not trusting whatever
    count the page happened to show when it was loaded) and requires the
    typed phrase to match *that* count -- if someone else approved or
    rejected a message in the meantime, a stale phrase now mismatches and
    this refuses rather than approving a different set than what the
    phrase named."""
    approver = approver.strip()
    confirm = confirm.strip().lower()
    try:
        campaign_uuid = uuid.UUID(campaign_id)
    except ValueError:
        return HTMLResponse(f"<p>Not a valid campaign id: {escape(campaign_id)}</p>", status_code=404)

    error = ""
    if not approver:
        error = 'Type your name in "Approving as" before bulk-approving.'
    else:
        with session_scope() as session:
            queued = session.execute(
                select(Message).where(Message.campaign_id == campaign_uuid, Message.status == "queued")
            ).scalars().all()
            expected = _bulk_approve_phrase(len(queued))
            if not queued:
                error = "No queued messages left to approve."
            elif confirm != expected:
                error = f"Type exactly {expected!r} to confirm -- got {confirm!r}."
            else:
                now = datetime.now(timezone.utc)
                for message in queued:
                    message.status = "approved"
                    message.approved_by = approver
                    message.approved_at = now

    params = f"?approver={quote(approver)}"
    if error:
        params += f"&bulk_error={quote(error)}"
    return RedirectResponse(url=f"/campaigns/{campaign_id}{params}", status_code=303)
