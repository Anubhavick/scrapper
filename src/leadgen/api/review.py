"""Minimal local review UI for a pipeline's CSV output.

Scoped-down step 8: this replaces "open the CSV in an editor" with a
filterable table and a one-click reject that writes to a target profile's
existing `exclude_domains` denylist (see targets/dentists-austin-tx.yaml's
touchto.io entry) instead of a manual YAML edit.

Deliberately reads the CSV pipeline.py already writes, not a database --
persistence (businesses/contacts/enrichment_signals) is still unbuilt
(see CLAUDE.md), and this UI doesn't need to wait on it. Approving a
*rendered outreach message* before send is out of scope here too -- that
needs campaigns/messages rows, which nothing creates yet. This is the
lead-review half of step 8 only.
"""

from __future__ import annotations

import csv
import re
from html import escape
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from leadgen.util.domains import normalise_domain

app = FastAPI(title="Lead Review")

CRAWL_STATUSES = ["ok", "partial", "unreachable", "no_website"]
DEFAULT_CSV = "leads-austin-dentists.csv"
DEFAULT_PROFILE = "targets/dentists-austin-tx.yaml"


def _load_rows(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open(newline="") as f:
        return list(csv.DictReader(f))


def _row_domain(row: dict[str, str]) -> str | None:
    url = row.get("website_url")
    if not url:
        return None
    try:
        return normalise_domain(url)
    except ValueError:
        return None


def add_excluded_domain(profile_path: Path, domain: str) -> bool:
    """Append `domain` to the profile's `filters.exclude_domains` list via
    a targeted text edit rather than a YAML round-trip -- pyyaml's dumper
    would silently drop the human-readable comments already living next to
    that key. Returns False if the domain was already present."""
    text = profile_path.read_text()
    match = re.search(r"^( *exclude_domains:\s*)\[(.*?)\]", text, re.MULTILINE)
    if match:
        existing = [d.strip() for d in match.group(2).split(",") if d.strip()]
        if domain in existing:
            return False
        new_line = f"{match.group(1)}[{', '.join(existing + [domain])}]"
        text = text[: match.start()] + new_line + text[match.end() :]
    else:
        filters_match = re.search(r"^filters:\s*$", text, re.MULTILINE)
        if filters_match is None:
            raise ValueError(f"{profile_path} has no `filters:` block to add exclude_domains under")
        text = text[: filters_match.end()] + f"\n  exclude_domains: [{domain}]" + text[filters_match.end() :]
    profile_path.write_text(text)
    return True


def _filtered(
    rows: list[dict[str, str]], qualified: str, status: str, q: str
) -> list[dict[str, str]]:
    q_lower = q.strip().lower()
    out = []
    for row in rows:
        if qualified != "all" and row.get("qualified") != qualified:
            continue
        if status != "all" and row.get("crawl_status", "") != status:
            continue
        if q_lower and q_lower not in row.get("name", "").lower():
            continue
        out.append(row)
    return out


_STATUS_COLORS = {
    "ok": "#1a7f37",
    "partial": "#9a6700",
    "unreachable": "#cf222e",
    "no_website": "#57606a",
}


def _badge(text: str, color: str) -> str:
    return (
        f'<span style="background:{color}1a;color:{color};border:1px solid {color}55;'
        f'padding:2px 8px;border-radius:999px;font-size:12px;font-weight:600;">{escape(text)}</span>'
    )


def _row_html(row: dict[str, str], csv_path: str, profile_path: str, filter_qs: str) -> str:
    domain = _row_domain(row)
    qualified = row.get("qualified", "no")
    status = row.get("crawl_status", "")
    q_badge = _badge("qualified", "#1a7f37") if qualified == "yes" else _badge("not qualified", "#57606a")
    s_badge = _badge(status, _STATUS_COLORS.get(status, "#57606a")) if status else ""
    tags = escape(row.get("tags", ""))
    website = row.get("website_url", "")
    website_html = (
        f'<a href="{escape(website)}" target="_blank" rel="noopener">{escape(website)}</a>' if website else ""
    )
    emails_html = escape(row.get("emails", "")) or '<span style="color:#cf222e;">none</span>'
    reject_form = ""
    if domain:
        reject_form = f"""
        <form method="post" action="/reject" style="display:inline">
          <input type="hidden" name="name" value="{escape(row.get('name', ''))}">
          <input type="hidden" name="domain" value="{escape(domain)}">
          <input type="hidden" name="csv" value="{escape(csv_path)}">
          <input type="hidden" name="profile" value="{escape(profile_path)}">
          <input type="hidden" name="filter_qs" value="{escape(filter_qs)}">
          <button type="submit" style="background:#cf222e;color:white;border:none;
            padding:4px 10px;border-radius:6px;cursor:pointer;font-size:12px;">Reject</button>
        </form>
        """
    return f"""
    <tr>
      <td><strong>{escape(row.get('name', ''))}</strong><br>
          <span style="color:#57606a;font-size:12px;">{escape(row.get('address', '') or '')}</span></td>
      <td>{website_html}<br><span style="font-size:12px;color:#57606a;">{escape(row.get('phone', '') or '')}</span></td>
      <td>{emails_html}</td>
      <td>{q_badge}</td>
      <td>{s_badge}</td>
      <td style="max-width:260px;font-size:12px;color:#57606a;">{tags}</td>
      <td>{reject_form}</td>
    </tr>
    """


def _filter_bar(csv_path: str, profile_path: str, qualified: str, status: str, q: str) -> str:
    def opt(value: str, current: str, label: str) -> str:
        selected = "selected" if value == current else ""
        return f'<option value="{value}" {selected}>{label}</option>'

    qualified_options = "".join(
        opt(v, qualified, l) for v, l in [("all", "All"), ("yes", "Qualified"), ("no", "Not qualified")]
    )
    status_options = "".join(
        opt(v, status, v) for v in ["all", *CRAWL_STATUSES]
    )
    return f"""
    <form method="get" action="/" style="display:flex;gap:12px;align-items:center;margin-bottom:16px;flex-wrap:wrap;">
      <input type="hidden" name="csv" value="{escape(csv_path)}">
      <input type="hidden" name="profile" value="{escape(profile_path)}">
      <input name="q" value="{escape(q)}" placeholder="Search name..."
             style="padding:6px 10px;border:1px solid #d0d7de;border-radius:6px;">
      <select name="qualified" style="padding:6px 10px;border:1px solid #d0d7de;border-radius:6px;">
        {qualified_options}
      </select>
      <select name="status" style="padding:6px 10px;border:1px solid #d0d7de;border-radius:6px;">
        {status_options}
      </select>
      <button type="submit" style="padding:6px 14px;border-radius:6px;border:1px solid #d0d7de;
        background:#f6f8fa;cursor:pointer;">Filter</button>
    </form>
    """


def _render_page(
    rows: list[dict[str, str]],
    all_count: int,
    csv_path: str,
    profile_path: str,
    qualified: str,
    status: str,
    q: str,
    notice: str = "",
) -> str:
    filter_qs = urlencode({"csv": csv_path, "profile": profile_path, "qualified": qualified, "status": status, "q": q})
    body_rows = "".join(_row_html(row, csv_path, profile_path, filter_qs) for row in rows) or (
        '<tr><td colspan="7" style="text-align:center;color:#57606a;padding:24px;">No rows match these filters.</td></tr>'
    )
    notice_html = (
        f'<div style="background:#ddf4ff;border:1px solid #54aeff55;padding:10px 14px;'
        f'border-radius:6px;margin-bottom:16px;">{escape(notice)}</div>'
        if notice
        else ""
    )
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Lead review</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #1f2328; background: #ffffff; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th {{ text-align: left; font-size: 12px; text-transform: uppercase; color: #57606a; padding: 8px; border-bottom: 2px solid #d0d7de; }}
  td {{ padding: 10px 8px; border-bottom: 1px solid #eaeef2; vertical-align: top; font-size: 14px; }}
  h1 {{ font-size: 20px; }}
  .meta {{ color: #57606a; font-size: 13px; margin-bottom: 16px; }}
</style>
</head>
<body>
  <h1>Lead review -- {escape(csv_path)}</h1>
  <div class="meta">{len(rows)} of {all_count} rows shown &middot; profile: {escape(profile_path)}</div>
  {notice_html}
  {_filter_bar(csv_path, profile_path, qualified, status, q)}
  <table>
    <thead>
      <tr><th>Business</th><th>Website / phone</th><th>Emails</th><th>Qualified</th><th>Crawl status</th><th>Tags</th><th></th></tr>
    </thead>
    <tbody>{body_rows}</tbody>
  </table>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def index(
    csv: str = DEFAULT_CSV,
    profile: str = DEFAULT_PROFILE,
    qualified: str = "all",
    status: str = "all",
    q: str = "",
    rejected: str = "",
    already: str = "",
) -> HTMLResponse:
    csv_path = Path(csv)
    if not csv_path.exists():
        return HTMLResponse(f"<p>CSV not found: {escape(csv)}</p>", status_code=404)

    all_rows = _load_rows(csv_path)
    rows = _filtered(all_rows, qualified, status, q)

    notice = ""
    if rejected:
        notice = (
            f"{escape(rejected)} was already excluded." if already == "1" else f"Excluded {escape(rejected)}."
        )

    html = _render_page(rows, len(all_rows), csv, profile, qualified, status, q, notice)
    return HTMLResponse(html)


@app.post("/reject")
def reject(
    name: str = Form(...),
    domain: str = Form(...),
    csv: str = Form(...),
    profile: str = Form(...),
    filter_qs: str = Form(""),
) -> RedirectResponse:
    added = add_excluded_domain(Path(profile), domain)
    params = filter_qs + f"&rejected={domain}&already={'0' if added else '1'}"
    return RedirectResponse(url=f"/?{params}", status_code=303)
