"""Scan-builder UI (docs/10): turn a filled-in form into a
`targets/*.yaml` file instead of hand-editing one, and let a human browse
and reuse the ones that already exist.

Every field maps directly onto `TargetProfile` (config/models.py) --
`TargetProfile.model_validate()` is the actual validation, run against
whatever the form submits, so a profile created here can never be less
valid than one written by hand. Cross-checks the loader also does
(business_type / offer_id must reference something real) are repeated
here since a plain string field can't express "must be a known business
type" at the pydantic-model level alone.

Writing is create-only, on purpose: a name colliding with an existing
`targets/<name>.yaml` is rejected rather than silently overwritten --
someone's hand-tuned `exclude_domains` list (see docs/07) shouldn't be
one form submission away from being clobbered. Edit an existing profile
by hand; use this to start a new one, optionally seeded from an existing
one via "Duplicate".
"""

from __future__ import annotations

import re
from html import escape
from pathlib import Path

import yaml
from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError

from leadgen.api.nav import PAGE_SIZE, nav_bar, pagination_bar
from leadgen.config.loader import ConfigError, load_business_types, load_offers, load_target_profile
from leadgen.config.models import KNOWN_SIGNALS, TargetProfile

router = APIRouter()

TARGETS_DIR = Path("targets")
BUSINESS_TYPES_PATH = Path("config/business_types.yaml")
OFFERS_DIR = Path("config/offers")

LOCATION_MODES = ["radius", "city", "bbox", "admin_area"]
SIGNAL_OPTIONS = sorted(KNOWN_SIGNALS)

# Matches target-profile filenames: lowercase, digits, hyphens, so
# `name` can never escape `targets/` (path traversal) or produce a
# filename YAML/the shell would treat specially.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")

_DEFAULT_VALUES: dict = {
    "name": "",
    "enabled": True,
    "business_type": "",
    "location_mode": "radius",
    "center": "",
    "radius_km": "",
    "bbox": "",
    "source_primary": "overpass",
    "source_fallback": "",
    "max_results": "500",
    "must_have_website": False,
    "must_have_phone": False,
    "exclude_domains": "",
    "exclude_name_patterns": "",
    "min_name_length": "0",
    "crawl_pages": "/, /contact, /about, /services",
    "max_pages": "8",
    "enrichment_signals": [],
    "require_email": True,
    "require_any_signal": [],
    "min_signal_count": "0",
    "stale_content_before_year": "",
    "max_page_weight_mb": "",
    "offer_id": "",
    "sender_pool": "",
    "daily_cap_per_mailbox": "40",
}


def load_registries() -> tuple[dict, dict, list[str]]:
    """Business types and offers, read fresh each request (cheap, small
    files) so a config edit shows up without restarting the server.
    Returns (business_types, offers, load_errors)."""
    errors: list[str] = []
    try:
        business_types = load_business_types(BUSINESS_TYPES_PATH)
    except ConfigError as exc:
        business_types = {}
        errors.append(f"Could not load {BUSINESS_TYPES_PATH}: {exc}")
    try:
        offers = load_offers(OFFERS_DIR)
    except ConfigError as exc:
        offers = {}
        errors.append(f"Could not load offers from {OFFERS_DIR}: {exc}")
    return business_types, offers, errors


def _parse_csv(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def _format_pydantic_errors(exc: ValidationError) -> list[str]:
    out = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"])
        out.append(f"{loc}: {err['msg']}" if loc else err["msg"])
    return out


def _to_yaml_safe(obj):
    if isinstance(obj, (tuple, list)):
        return [_to_yaml_safe(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _to_yaml_safe(v) for k, v in obj.items()}
    return obj


def _values_from_form(form) -> dict:
    return {
        "name": (form.get("name") or "").strip(),
        "enabled": form.get("enabled") is not None,
        "business_type": form.get("business_type") or "",
        "location_mode": form.get("location_mode") or "radius",
        "center": form.get("center") or "",
        "radius_km": form.get("radius_km") or "",
        "bbox": form.get("bbox") or "",
        "source_primary": form.get("source_primary") or "overpass",
        "source_fallback": form.get("source_fallback") or "",
        "max_results": form.get("max_results") or "",
        "must_have_website": form.get("must_have_website") is not None,
        "must_have_phone": form.get("must_have_phone") is not None,
        "exclude_domains": form.get("exclude_domains") or "",
        "exclude_name_patterns": form.get("exclude_name_patterns") or "",
        "min_name_length": form.get("min_name_length") or "",
        "crawl_pages": form.get("crawl_pages") or "",
        "max_pages": form.get("max_pages") or "",
        "enrichment_signals": form.getlist("enrichment_signals"),
        "require_email": form.get("require_email") is not None,
        "require_any_signal": form.getlist("require_any_signal"),
        "min_signal_count": form.get("min_signal_count") or "",
        "stale_content_before_year": form.get("stale_content_before_year") or "",
        "max_page_weight_mb": form.get("max_page_weight_mb") or "",
        "offer_id": form.get("offer_id") or "",
        "sender_pool": form.get("sender_pool") or "",
        "daily_cap_per_mailbox": form.get("daily_cap_per_mailbox") or "",
    }


def _values_from_raw(raw: dict) -> dict:
    """Prefill shape for "Duplicate" -- reads an existing profile's parsed
    YAML back into the same flat shape `_render_form` expects."""
    location = raw.get("location") or {}
    source = raw.get("source") or {}
    filters = raw.get("filters") or {}
    enrichment = raw.get("enrichment") or {}
    qualification = raw.get("qualification") or {}
    outreach = raw.get("outreach") or {}

    def _opt_str(value) -> str:
        return "" if value is None else str(value)

    return {
        "name": raw.get("name", ""),
        "enabled": raw.get("enabled", True),
        "business_type": raw.get("business_type", ""),
        "location_mode": location.get("mode", "radius"),
        "center": location.get("center", ""),
        "radius_km": _opt_str(location.get("radius_km")),
        "bbox": ", ".join(str(v) for v in location.get("bbox", [])),
        "source_primary": source.get("primary", "overpass"),
        "source_fallback": source.get("fallback") or "",
        "max_results": _opt_str(source.get("max_results", 500)),
        "must_have_website": filters.get("must_have_website", False),
        "must_have_phone": filters.get("must_have_phone", False),
        "exclude_domains": ", ".join(filters.get("exclude_domains", [])),
        "exclude_name_patterns": ", ".join(filters.get("exclude_name_patterns", [])),
        "min_name_length": _opt_str(filters.get("min_name_length", 0)),
        "crawl_pages": ", ".join(enrichment.get("crawl_pages", ["/"])),
        "max_pages": _opt_str(enrichment.get("max_pages", 8)),
        "enrichment_signals": enrichment.get("signals", []),
        "require_email": qualification.get("require_email", True),
        "require_any_signal": qualification.get("require_any_signal", []),
        "min_signal_count": _opt_str(qualification.get("min_signal_count", 0)),
        "stale_content_before_year": _opt_str(qualification.get("stale_content_before_year")),
        "max_page_weight_mb": _opt_str(qualification.get("max_page_weight_mb")),
        "offer_id": outreach.get("offer_id", ""),
        "sender_pool": ", ".join(outreach.get("sender_pool", [])),
        "daily_cap_per_mailbox": _opt_str(outreach.get("daily_cap_per_mailbox", 40)),
    }


def _build_profile_dict(values: dict) -> dict:
    """Same shape as `_values_from_form` output, but nested to match
    TargetProfile -- left to pydantic to type-coerce and validate, so a
    non-numeric `max_pages` etc. surfaces as one of its own error
    messages rather than a manual parse failure here."""
    location_mode = values["location_mode"]
    if location_mode == "bbox":
        location = {"mode": "bbox", "bbox": _parse_csv(values["bbox"])}
    elif location_mode == "radius":
        location = {"mode": "radius", "center": values["center"], "radius_km": values["radius_km"] or "0"}
    else:
        location = {"mode": location_mode, "center": values["center"]}

    source = {"primary": values["source_primary"], "max_results": values["max_results"] or "500"}
    if values["source_fallback"]:
        source["fallback"] = values["source_fallback"]

    return {
        "name": values["name"],
        "enabled": bool(values["enabled"]),
        "business_type": values["business_type"],
        "location": location,
        "source": source,
        "filters": {
            "must_have_website": bool(values["must_have_website"]),
            "must_have_phone": bool(values["must_have_phone"]),
            "exclude_domains": _parse_csv(values["exclude_domains"]),
            "exclude_name_patterns": _parse_csv(values["exclude_name_patterns"]),
            "min_name_length": values["min_name_length"] or "0",
        },
        "enrichment": {
            "crawl_pages": _parse_csv(values["crawl_pages"]) or ["/"],
            "max_pages": values["max_pages"] or "8",
            "signals": values["enrichment_signals"],
        },
        "qualification": {
            "require_email": bool(values["require_email"]),
            "require_any_signal": values["require_any_signal"],
            "min_signal_count": values["min_signal_count"] or "0",
            "stale_content_before_year": values["stale_content_before_year"] or None,
            "max_page_weight_mb": values["max_page_weight_mb"] or None,
        },
        "outreach": {
            "offer_id": values["offer_id"],
            "sender_pool": _parse_csv(values["sender_pool"]),
            "daily_cap_per_mailbox": values["daily_cap_per_mailbox"] or "40",
        },
    }


def _validate_profile(values: dict, business_types: dict, offers: dict) -> tuple[TargetProfile | None, list[str]]:
    """Shared by create and edit -- the only difference between them is
    what happens to the filesystem afterward (a new file vs. an
    overwrite), never how a submission is validated."""
    errors: list[str] = []
    profile: TargetProfile | None = None
    try:
        profile = TargetProfile.model_validate(_build_profile_dict(values))
    except ValidationError as exc:
        errors.extend(_format_pydantic_errors(exc))

    if profile is not None:
        if profile.business_type not in business_types:
            errors.append(
                f"Unknown business_type {profile.business_type!r}; known: {sorted(business_types)}"
            )
        if profile.outreach.offer_id not in offers:
            errors.append(
                f"Unknown offer_id {profile.outreach.offer_id!r}; known: {sorted(offers)}"
            )
    return profile, errors


def _write_profile(path: Path, profile: TargetProfile) -> None:
    data = _to_yaml_safe(profile.model_dump(exclude_none=True))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, default_flow_style=False))


def _location_summary(location) -> str:
    mode = location.mode
    if mode == "radius":
        return f"radius: {location.center} ({location.radius_km} km)"
    if mode == "bbox":
        return f"bbox: {location.bbox}"
    return f"{mode}: {location.center}"


# ---------------------------------------------------------------- render

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
  input[type=text], input[type=number], select { width: 100%; max-width: 480px; padding: 6px 8px; border: 1px solid #d0d7de; border-radius: 6px; font-size: 14px; box-sizing: border-box; }
  .help { color: #57606a; font-size: 12px; margin-top: 2px; }
  .errors { background: #fff0ef; border: 1px solid #cf222e55; color: #cf222e; padding: 10px 14px; border-radius: 6px; margin-bottom: 16px; }
  .notice { background: #ddf4ff; border: 1px solid #54aeff55; padding: 10px 14px; border-radius: 6px; margin-bottom: 16px; }
  .btn { padding: 8px 16px; border-radius: 6px; border: 1px solid #1a7f37; background: #1a7f37; color: white; cursor: pointer; font-size: 14px; }
  .btn-secondary { padding: 6px 12px; border-radius: 6px; border: 1px solid #d0d7de; background: #f6f8fa; color: #1f2328; text-decoration: none; font-size: 13px; }
  pre { background: #f6f8fa; border: 1px solid #d0d7de; border-radius: 6px; padding: 12px; overflow-x: auto; font-size: 13px; }
"""


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


def _render_index(
    rows: list[tuple[str, TargetProfile | None, str | None]], notice: str, page: int = 1, total: int = 0
) -> str:
    def row_html(name: str, profile: TargetProfile | None, error: str | None) -> str:
        if error:
            return f"""
            <tr>
              <td><strong>{escape(name)}</strong></td>
              <td colspan="3" style="color:#cf222e;">Invalid: {escape(error)}</td>
              <td><a href="/targets/{escape(name)}" class="btn-secondary">View</a></td>
            </tr>
            """
        badge = (
            '<span style="color:#1a7f37;">enabled</span>'
            if profile.enabled
            else '<span style="color:#57606a;">disabled</span>'
        )
        return f"""
        <tr>
          <td><strong>{escape(name)}</strong></td>
          <td>{escape(profile.business_type)}</td>
          <td>{escape(_location_summary(profile.location))}</td>
          <td>{badge}</td>
          <td>
            <a href="/targets/{escape(name)}" class="btn-secondary">View</a>
            <a href="/targets/{escape(name)}/edit" class="btn-secondary">Edit</a>
            <a href="/targets/new?from={escape(name)}" class="btn-secondary">Duplicate</a>
            <a href="/runs?target_name={escape(name)}" class="btn-secondary">History</a>
          </td>
        </tr>
        """

    body_rows = "".join(row_html(*r) for r in rows) or (
        '<tr><td colspan="5" style="text-align:center;color:#57606a;padding:24px;">'
        "No target profiles yet -- create one below.</td></tr>"
    )
    notice_html = f'<div class="notice">{escape(notice)}</div>' if notice else ""
    pager = pagination_bar(page, total, "/targets", page_size=PAGE_SIZE)
    body = f"""
  <h1>Target profiles</h1>
  <div class="meta">{total} profile(s) in targets/ &middot; <a href="/targets/new" class="btn-secondary">+ New target</a></div>
  {notice_html}
  <table>
    <thead><tr><th>Name</th><th>Business type</th><th>Location</th><th>Status</th><th></th></tr></thead>
    <tbody>{body_rows}</tbody>
  </table>
  {pager}
    """
    return _page("Target profiles", "/targets", body)


def _field_text(name: str, label: str, value: str, help_text: str = "", input_type: str = "text") -> str:
    return f"""
    <label for="{name}">{escape(label)}</label>
    <input type="{input_type}" id="{name}" name="{name}" value="{escape(str(value))}">
    {f'<div class="help">{escape(help_text)}</div>' if help_text else ''}
    """


def _field_checkbox(name: str, label: str, checked: bool) -> str:
    checked_attr = "checked" if checked else ""
    return f"""
    <label class="inline"><input type="checkbox" name="{name}" {checked_attr}> {escape(label)}</label>
    """


def _field_select(name: str, label: str, options: list[str], current: str, help_text: str = "") -> str:
    opts = "".join(
        f'<option value="{escape(o)}" {"selected" if o == current else ""}>{escape(o)}</option>' for o in options
    )
    return f"""
    <label for="{name}">{escape(label)}</label>
    <select id="{name}" name="{name}">{opts}</select>
    {f'<div class="help">{escape(help_text)}</div>' if help_text else ''}
    """


def _field_checkbox_group(name: str, label: str, options: list[str], selected: list[str]) -> str:
    boxes = "".join(
        f'<label class="inline"><input type="checkbox" name="{name}" value="{escape(o)}" '
        f'{"checked" if o in selected else ""}> {escape(o)}</label>'
        for o in options
    )
    return f"""
    <label>{escape(label)}</label>
    <div>{boxes}</div>
    """


def _render_form(
    values: dict, errors: list[str], business_types: list[str], offers: list[str], edit_name: str | None = None
) -> str:
    errors_html = ""
    if errors:
        items = "".join(f"<li>{escape(e)}</li>" for e in errors)
        errors_html = f'<div class="errors"><strong>Fix the following:</strong><ul>{items}</ul></div>'

    is_edit = edit_name is not None
    if is_edit:
        name_field = f"""
        <label>Name</label>
        <input type="text" value="{escape(edit_name)}" disabled>
        <div class="help">Renaming isn't supported here -- create a new profile (Duplicate) if you need a different name.</div>
        """
        edit_warning = (
            '<div class="errors" style="background:#fff8c5;border-color:#d4a72c55;color:#7d4e00;">'
            "<strong>Saving rewrites this file from the fields below.</strong> Any hand-written YAML "
            "comments in it (e.g. a note next to <code>exclude_domains</code>, see docs/07) will be lost. "
            f"To keep them, edit <code>targets/{escape(edit_name)}.yaml</code> directly instead."
            "</div>"
        )
        form_action = f"/targets/{escape(edit_name)}/edit"
        submit_label = "Save changes"
        title = f"Edit target profile -- {edit_name}"
    else:
        name_field = _field_text(
            "name", "Name (becomes targets/<name>.yaml)", values["name"],
            "lowercase letters, digits, hyphens only, e.g. dentists-austin-tx",
        )
        edit_warning = ""
        form_action = "/targets"
        submit_label = "Create target profile"
        title = "New target profile"

    business_type_field = (
        _field_select("business_type", "Business type", business_types, values["business_type"])
        if business_types
        else _field_text(
            "business_type",
            "Business type",
            values["business_type"],
            help_text="No business types found in config/business_types.yaml -- add one first.",
        )
    )
    offer_field = (
        _field_select("offer_id", "Offer", offers, values["offer_id"])
        if offers
        else _field_text(
            "offer_id",
            "Offer id",
            values["offer_id"],
            help_text="No offers found in config/offers/ -- add one first.",
        )
    )

    body = f"""
  <h1>{escape(title)}</h1>
  <div class="meta">Fills in a <code>targets/&lt;name&gt;.yaml</code> file the same pipeline reads today -- see <a href="/targets">existing profiles</a> to duplicate one instead of starting blank.</div>
  {edit_warning}
  {errors_html}
  <form method="post" action="{form_action}">
    <fieldset>
      <legend>Identity</legend>
      {name_field}
      {_field_checkbox("enabled", "Enabled", values["enabled"])}
      {business_type_field}
    </fieldset>

    <fieldset>
      <legend>Location</legend>
      {_field_select("location_mode", "Mode", LOCATION_MODES, values["location_mode"])}
      {_field_text("center", "Center (place name; used by radius/city/admin_area)", values["center"], 'e.g. "Austin, Texas, USA"')}
      {_field_text("radius_km", "Radius km (radius mode only)", values["radius_km"], input_type="number")}
      {_field_text("bbox", "Bbox: south,west,north,east (bbox mode only)", values["bbox"])}
    </fieldset>

    <fieldset>
      <legend>Discovery source</legend>
      {_field_select("source_primary", "Primary", ["overpass", "places", "csv"], values["source_primary"])}
      {_field_select("source_fallback", "Fallback (optional)", ["", "overpass", "places", "csv"], values["source_fallback"])}
      {_field_text("max_results", "Max results", values["max_results"], input_type="number")}
    </fieldset>

    <fieldset>
      <legend>Filters</legend>
      {_field_checkbox("must_have_website", "Must have website", values["must_have_website"])}
      {_field_checkbox("must_have_phone", "Must have phone", values["must_have_phone"])}
      {_field_text("exclude_domains", "Exclude domains (comma-separated)", values["exclude_domains"], "e.g. touchto.io, some-agency.com")}
      {_field_text("exclude_name_patterns", "Exclude name patterns (comma-separated regexes)", values["exclude_name_patterns"])}
      {_field_text("min_name_length", "Min name length", values["min_name_length"], input_type="number")}
    </fieldset>

    <fieldset>
      <legend>Enrichment</legend>
      {_field_text("crawl_pages", "Pages to crawl (comma-separated)", values["crawl_pages"], "e.g. /, /contact, /about, /services")}
      {_field_text("max_pages", "Max pages per site", values["max_pages"], input_type="number")}
      {_field_checkbox_group("enrichment_signals", "Signals to compute", SIGNAL_OPTIONS, values["enrichment_signals"])}
    </fieldset>

    <fieldset>
      <legend>Qualification</legend>
      {_field_checkbox("require_email", "Require at least one email", values["require_email"])}
      {_field_checkbox_group("require_any_signal", "Qualify if any of these signals is true", SIGNAL_OPTIONS, values["require_any_signal"])}
      {_field_text("min_signal_count", "Min matching signal count", values["min_signal_count"], input_type="number")}
      {_field_text("stale_content_before_year", "Stale-content threshold year (optional)", values["stale_content_before_year"], "leave blank unless you want last_content_year to only count when older than this", input_type="number")}
      {_field_text("max_page_weight_mb", "Max page weight MB (optional)", values["max_page_weight_mb"], "leave blank unless you want page_weight_mb to only count above this", input_type="number")}
    </fieldset>

    <fieldset>
      <legend>Outreach</legend>
      {offer_field}
      {_field_text("sender_pool", "Sender pool (comma-separated mailbox names)", values["sender_pool"], "these aren't validated against authorized mailboxes yet -- see HOWTO.md for authorizing one")}
      {_field_text("daily_cap_per_mailbox", "Daily cap per mailbox", values["daily_cap_per_mailbox"], input_type="number")}
    </fieldset>

    <button type="submit" class="btn">{escape(submit_label)}</button>
    <a href="/targets" class="btn-secondary" style="margin-left:8px;">Cancel</a>
  </form>
    """
    return _page(title, "/targets", body)


def _render_show(
    name: str, raw_text: str, profile: TargetProfile | None, error: str | None, created: bool, updated: bool = False
) -> str:
    notice_html = ""
    if created:
        notice_html = f'<div class="notice">Created targets/{escape(name)}.yaml.</div>'
    elif updated:
        notice_html = f'<div class="notice">Saved changes to targets/{escape(name)}.yaml.</div>'
    error_html = (
        f'<div class="errors"><strong>This profile does not currently load:</strong> {escape(error)}</div>'
        if error
        else ""
    )
    run_command = f"uv run python scripts/run_pipeline.py targets/{name}.yaml leads-{name}.csv"
    body = f"""
  <h1>Target -- {escape(name)}</h1>
  <div class="meta">
    <a href="/targets/{escape(name)}/edit" class="btn-secondary">Edit</a>
    <a href="/targets/new?from={escape(name)}" class="btn-secondary">Duplicate as new</a>
    <a href="/runs?target_name={escape(name)}" class="btn-secondary">View run history</a>
  </div>
  {notice_html}
  {error_html}
  <h3>Run this scan</h3>
  <pre>{escape(run_command)}</pre>
  <div class="help">Running a scan isn't triggered from this UI yet -- discovery/crawl hit real external services and can take minutes; see CLAUDE.md's orchestration-loop notes for why that's still a manual step.</div>
  <h3>targets/{escape(name)}.yaml</h3>
  <pre>{escape(raw_text)}</pre>
    """
    return _page(f"Target -- {name}", "/targets", body)


# ----------------------------------------------------------------- routes


@router.get("/targets", response_class=HTMLResponse)
def list_targets(created: str = "", page: int = 1) -> HTMLResponse:
    page = max(1, page)
    files = sorted(TARGETS_DIR.glob("*.yaml")) if TARGETS_DIR.is_dir() else []
    total = len(files)
    page_files = files[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
    business_types, offers, _load_errors = load_registries()

    rows: list[tuple[str, TargetProfile | None, str | None]] = []
    for path in page_files:
        name = path.stem
        try:
            profile = load_target_profile(path, business_types, offers)
            rows.append((name, profile, None))
        except ConfigError as exc:
            rows.append((name, None, str(exc)))

    notice = f"Created {escape(created)}.yaml." if created else ""
    return HTMLResponse(_render_index(rows, notice, page, total))


@router.get("/targets/new", response_class=HTMLResponse)
def new_target_form(from_: str = Query("", alias="from")) -> HTMLResponse:
    business_types, offers, load_errors = load_registries()
    values = dict(_DEFAULT_VALUES)

    if from_ and _NAME_RE.match(from_):
        src = TARGETS_DIR / f"{from_}.yaml"
        if src.is_file():
            try:
                raw = yaml.safe_load(src.read_text()) or {}
                values = _values_from_raw(raw)
                values["name"] = f"{from_}-copy"
            except yaml.YAMLError:
                pass

    return HTMLResponse(_render_form(values, load_errors, sorted(business_types), sorted(offers)))


@router.post("/targets", response_class=HTMLResponse)
async def create_target(request: Request) -> HTMLResponse:
    form = await request.form()
    values = _values_from_form(form)
    errors: list[str] = []

    if not _NAME_RE.match(values["name"]):
        errors.append(
            "Name must be lowercase letters, digits, and hyphens, starting with a letter or digit "
            "(e.g. dentists-austin-tx)."
        )

    business_types, offers, load_errors = load_registries()
    errors.extend(load_errors)

    profile, validation_errors = _validate_profile(values, business_types, offers)
    errors.extend(validation_errors)

    target_path = TARGETS_DIR / f"{values['name']}.yaml" if _NAME_RE.match(values["name"]) else None
    if target_path is not None and target_path.exists():
        errors.append(
            f"targets/{values['name']}.yaml already exists -- pick a different name, "
            "or edit that file directly to change it."
        )

    if errors or profile is None or target_path is None:
        return HTMLResponse(_render_form(values, errors, sorted(business_types), sorted(offers)))

    _write_profile(target_path, profile)

    return RedirectResponse(url=f"/targets/{values['name']}?created=1", status_code=303)


@router.get("/targets/{name}", response_class=HTMLResponse)
def show_target(name: str, created: str = "", updated: str = "") -> HTMLResponse:
    if not _NAME_RE.match(name):
        return HTMLResponse(f"<p>Not a valid target name: {escape(name)}</p>", status_code=404)

    path = TARGETS_DIR / f"{name}.yaml"
    if not path.is_file():
        return HTMLResponse(f"<p>No target profile named {escape(name)}</p>", status_code=404)

    raw_text = path.read_text()
    business_types, offers, _load_errors = load_registries()

    profile: TargetProfile | None = None
    error: str | None = None
    try:
        profile = load_target_profile(path, business_types, offers)
    except ConfigError as exc:
        error = str(exc)

    return HTMLResponse(_render_show(name, raw_text, profile, error, created == "1", updated == "1"))


@router.get("/targets/{name}/edit", response_class=HTMLResponse)
def edit_target_form(name: str) -> HTMLResponse:
    if not _NAME_RE.match(name):
        return HTMLResponse(f"<p>Not a valid target name: {escape(name)}</p>", status_code=404)

    path = TARGETS_DIR / f"{name}.yaml"
    if not path.is_file():
        return HTMLResponse(f"<p>No target profile named {escape(name)}</p>", status_code=404)

    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        return HTMLResponse(
            f"<p>targets/{escape(name)}.yaml doesn't parse as YAML ({escape(str(exc))}) -- "
            "fix it by hand before editing it here.</p>",
            status_code=400,
        )

    values = _values_from_raw(raw)
    values["name"] = name
    business_types, offers, load_errors = load_registries()
    return HTMLResponse(_render_form(values, load_errors, sorted(business_types), sorted(offers), edit_name=name))


@router.post("/targets/{name}/edit", response_class=HTMLResponse)
async def edit_target(name: str, request: Request) -> HTMLResponse:
    if not _NAME_RE.match(name):
        return HTMLResponse(f"<p>Not a valid target name: {escape(name)}</p>", status_code=404)

    path = TARGETS_DIR / f"{name}.yaml"
    if not path.is_file():
        return HTMLResponse(f"<p>No target profile named {escape(name)}</p>", status_code=404)

    form = await request.form()
    values = _values_from_form(form)
    values["name"] = name  # renaming isn't supported via this form -- see _render_form's edit_warning

    business_types, offers, errors = load_registries()
    profile, validation_errors = _validate_profile(values, business_types, offers)
    errors.extend(validation_errors)

    if errors or profile is None:
        return HTMLResponse(_render_form(values, errors, sorted(business_types), sorted(offers), edit_name=name))

    _write_profile(path, profile)

    return RedirectResponse(url=f"/targets/{name}?updated=1", status_code=303)
