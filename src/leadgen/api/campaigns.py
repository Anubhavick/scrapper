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

from leadgen.api.nav import nav_bar
from leadgen.api.targets import load_registries
from leadgen.db.campaigns import CampaignError, create_campaign, generate_campaign_messages
from leadgen.db.models import Business, Campaign, CampaignMailbox, Contact, Mailbox, Message, TargetRun, TargetRunBusiness
from leadgen.db.session import session_scope

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


def _page(title: str, active: str, body: str) -> str:
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{escape(title)}</title>
<style>{_STYLE}</style>
</head>
<body>
  {nav_bar(active)}
  {body}
</body>
</html>"""


# ---------------------------------------------------------------- index


def _render_index(rows: list[tuple]) -> str:
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
    body = f"""
  <h1>Campaigns</h1>
  <div class="meta">{len(rows)} campaign(s) &middot; <a href="/campaigns/new" class="btn-secondary">+ New campaign</a></div>
  <table>
    <thead><tr><th>Campaign</th><th>Sender pool</th><th>Messages</th><th>Status breakdown</th><th></th></tr></thead>
    <tbody>{body_rows}</tbody>
  </table>
    """
    return _page("Campaigns", "/campaigns", body)


@router.get("/campaigns", response_class=HTMLResponse)
def list_campaigns() -> HTMLResponse:
    with session_scope() as session:
        campaigns = session.execute(select(Campaign).order_by(Campaign.created_at.desc())).scalars().all()
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
        html = _render_index(rows)
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


# ------------------------------------------------------------------ show


def _render_show(
    campaign: Campaign,
    target_name: str,
    mailbox_names: list[str],
    rows: list[tuple[Message, Contact, Business]],
    approver: str,
    notice: str,
) -> str:
    def message_row(message: Message, contact: Contact, business: Business) -> str:
        badge = _badge(message.status, _STATUS_COLORS.get(message.status, "#57606a"))
        actions = ""
        if message.status == "queued":
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
        elif message.status == "approved":
            when = message.approved_at.strftime("%Y-%m-%d %H:%M") if message.approved_at else ""
            actions = f'<span class="help">by {escape(message.approved_by or "?")} at {when}</span>'
        return f"""
        <tr>
          <td><strong>{escape(business.name)}</strong></td>
          <td>{escape(contact.email)}</td>
          <td>{escape(message.subject or "")}
            <details><summary>show body</summary><pre>{escape(message.body or "")}</pre></details>
          </td>
          <td>{badge}</td>
          <td>{actions}</td>
        </tr>
        """

    body_rows = "".join(message_row(*r) for r in rows) or (
        '<tr><td colspan="5" style="text-align:center;color:#57606a;padding:24px;">No messages in this campaign.</td></tr>'
    )
    notice_html = f'<div class="notice">{escape(notice)}</div>' if notice else ""
    body = f"""
  <h1>Campaign -- {escape(campaign.name or str(campaign.id)[:8])}</h1>
  <div class="meta">target: {escape(target_name)} &middot; offer: {escape(campaign.offer_id)} &middot; sender pool: {escape(", ".join(mailbox_names))}</div>
  {notice_html}
  <form method="get" action="/campaigns/{campaign.id}" style="margin-bottom:16px;">
    <label style="display:inline;font-size:13px;">Approving as:
      <input type="text" name="approver" value="{escape(approver)}" placeholder="your name" style="width:200px;display:inline;">
    </label>
    <button type="submit" class="btn-secondary">Set</button>
  </form>
  <table>
    <thead><tr><th>Business</th><th>Contact</th><th>Subject / body</th><th>Status</th><th></th></tr></thead>
    <tbody>{body_rows}</tbody>
  </table>
    """
    return _page(f"Campaign -- {campaign.name or ''}", "/campaigns", body)


@router.get("/campaigns/{campaign_id}", response_class=HTMLResponse)
def show_campaign(campaign_id: str, approver: str = "", created: str = "", skipped: str = "") -> HTMLResponse:
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
        mailbox_names = (
            session.execute(
                select(Mailbox.name)
                .join(CampaignMailbox, CampaignMailbox.mailbox_id == Mailbox.id)
                .where(CampaignMailbox.campaign_id == campaign.id)
            )
            .scalars()
            .all()
        )
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

        html = _render_show(campaign, target_name, list(mailbox_names), list(rows), approver, notice)
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
