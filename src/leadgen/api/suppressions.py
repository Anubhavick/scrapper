"""Manual suppression-list management (ROADMAP.md's top backlog item):
until step 7 (bounce/reply monitoring) exists, `suppressions` has no
automatic writer at all -- the hard rule "suppression check before every
send, no exceptions" (`send/suppression.py`, checked before every real
send) has nothing populating the table it depends on besides a raw DB
insert. This gives a human a way to add one without touching Postgres
directly, so a reply asking to stop contact can be honored the same day
it arrives, not whenever step 7 eventually ships.

Create-only, like `api/targets.py`'s target profiles -- no delete or
edit. A suppression is meant to be a permanent record ("checked before
every send... forever", CLAUDE.md's schema-decisions note); a UI that
could un-suppress someone as easily as a stray click would undermine
that guarantee far more than the inconvenience of a wrong entry sitting
there until it's fixed directly in Postgres. `value` is normalised the
same way the actual send path checks it (`send/suppression.py`) so what
gets stored here matches what a real send would compare against.
"""

from __future__ import annotations

from html import escape

from fastapi import APIRouter, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from leadgen.api.nav import nav_bar
from leadgen.db.models import Suppression
from leadgen.db.session import session_scope
from leadgen.util.domains import normalise_domain

router = APIRouter()

__all__ = ["router"]

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
  input[type=text], select { width: 100%; max-width: 480px; padding: 6px 8px; border: 1px solid #d0d7de; border-radius: 6px; font-size: 14px; box-sizing: border-box; }
  .errors { background: #fff0ef; border: 1px solid #cf222e55; color: #cf222e; padding: 10px 14px; border-radius: 6px; margin-bottom: 16px; }
  .btn { padding: 8px 16px; border-radius: 6px; border: 1px solid #1a7f37; background: #1a7f37; color: white; cursor: pointer; font-size: 14px; }
"""


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{escape(title)}</title>
<style>{_STYLE}</style>
</head>
<body>
  {nav_bar("/suppressions")}
  {body}
</body>
</html>"""


def _normalise_email(value: str) -> str:
    """Lowercase + a minimal shape check -- not trying to be a full RFC
    5322 validator, just enough to reject obvious typos before they're
    stored as a permanent record. Matches `send/suppression.py`'s own
    `email.strip().lower()` comparison exactly."""
    value = value.strip().lower()
    if "@" not in value or value.startswith("@") or value.endswith("@") or " " in value:
        raise ValueError(f"{value!r} doesn't look like an email address")
    return value


def _render(rows: list[Suppression], errors: list[str], values: dict) -> str:
    errors_html = ""
    if errors:
        items = "".join(f"<li>{escape(e)}</li>" for e in errors)
        errors_html = f'<div class="errors"><strong>Fix the following:</strong><ul>{items}</ul></div>'

    rows_html = "".join(
        f"""<tr>
          <td>{escape(s.scope)}</td>
          <td>{escape(s.value)}</td>
          <td>{escape(s.reason)}</td>
          <td>{s.created_at.strftime("%Y-%m-%d %H:%M")}</td>
        </tr>"""
        for s in rows
    ) or '<tr><td colspan="4" style="text-align:center;color:#57606a;padding:24px;">No suppressions yet.</td></tr>'

    scope = values.get("scope", "email")
    body = f"""
  <h1>Suppressions</h1>
  <div class="meta">{len(rows)} entr{"y" if len(rows) == 1 else "ies"} &middot; checked before every send, permanently &middot;
  no delete here on purpose (see this page's module docstring) -- fix a mistaken entry directly in Postgres.</div>
  {errors_html}
  <form method="post" action="/suppressions">
    <fieldset>
      <legend>Add a suppression</legend>
      <label>Scope</label>
      <select name="scope">
        <option value="email" {"selected" if scope == "email" else ""}>email</option>
        <option value="domain" {"selected" if scope == "domain" else ""}>domain</option>
      </select>
      <label>Value</label>
      <input type="text" name="value" value="{escape(values.get('value', ''))}" placeholder="lead@example.com or example.com">
      <label>Reason</label>
      <input type="text" name="reason" value="{escape(values.get('reason', ''))}" placeholder="e.g. replied asking to stop contact, 2026-09-13">
      <button type="submit" class="btn" style="margin-top:12px;">Add</button>
    </fieldset>
  </form>
  <table>
    <thead><tr><th>Scope</th><th>Value</th><th>Reason</th><th>Added</th></tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
    """
    return _page("Suppressions", body)


@router.get("/suppressions", response_class=HTMLResponse)
def list_suppressions() -> HTMLResponse:
    with session_scope() as session:
        rows = session.execute(select(Suppression).order_by(Suppression.created_at.desc())).scalars().all()
        html = _render(list(rows), [], {})
    return HTMLResponse(html)


@router.post("/suppressions", response_class=HTMLResponse)
def add_suppression(
    scope: str = Form(""), value: str = Form(""), reason: str = Form("")
) -> HTMLResponse:
    # Form(...) (required) treats a submitted-but-empty field as *missing*
    # and raises FastAPI's raw 422 instead of reaching this function at
    # all -- the same footgun docs/09 hit with an empty `approver` field.
    # Form("") + manual validation below is the established fix.
    scope = scope.strip().lower()
    raw_value = value.strip()
    reason = reason.strip()
    values = {"scope": scope, "value": raw_value, "reason": reason}

    errors: list[str] = []
    normalised: str | None = None
    if scope not in ("email", "domain"):
        errors.append("Scope must be 'email' or 'domain'.")
    elif not raw_value:
        errors.append("Value is required.")
    else:
        try:
            normalised = _normalise_email(raw_value) if scope == "email" else normalise_domain(raw_value)
        except ValueError as exc:
            errors.append(str(exc))
    if not reason:
        errors.append("Reason is required -- this becomes a permanent record of why.")

    if not errors:
        with session_scope() as session:
            existing = session.execute(
                select(Suppression).where(Suppression.scope == scope, Suppression.value == normalised)
            ).scalar_one_or_none()
            if existing is not None:
                errors.append(
                    f"{normalised!r} is already suppressed (added {existing.created_at.strftime('%Y-%m-%d')})."
                )

    if errors:
        with session_scope() as session:
            rows = session.execute(select(Suppression).order_by(Suppression.created_at.desc())).scalars().all()
            html = _render(list(rows), errors, values)
        return HTMLResponse(html, status_code=400)

    with session_scope() as session:
        session.add(Suppression(scope=scope, value=normalised, reason=reason))

    return RedirectResponse(url="/suppressions", status_code=303)
