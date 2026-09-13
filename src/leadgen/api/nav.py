"""Top-nav strip (and the shared pagination bar) for the leadgen web UI.
Its own tiny module so review.py/targets.py/campaigns.py can all use
these without importing each other."""

from __future__ import annotations

from urllib.parse import urlencode

PAGE_SIZE = 25

_PAGES = [
    ("/targets", "Targets"),
    ("/runs", "Run history"),
    ("/campaigns", "Campaigns"),
    ("/suppressions", "Suppressions"),
    ("/mailboxes", "Mailboxes"),
    ("/", "Lead review (CSV)"),
]


def nav_bar(active: str) -> str:
    links = []
    for href, label in _PAGES:
        style = "font-weight:600;color:#1f2328;" if href == active else "color:#0969da;"
        links.append(
            f'<a href="{href}" style="{style}text-decoration:none;margin-right:16px;font-size:13px;">{label}</a>'
        )
    return (
        '<div style="margin-bottom:20px;padding-bottom:12px;border-bottom:1px solid #d0d7de;">'
        + "".join(links)
        + "</div>"
    )


def pagination_bar(
    page: int, total: int, base_url: str, extra_params: dict[str, str] | None = None, page_size: int = PAGE_SIZE
) -> str:
    """A Prev/Next bar for a page of `total` items, `page_size` per page
    (1-indexed `page`). Returns "" when everything fits on one page, so
    callers can drop this in unconditionally without an if/else at every
    call site. `extra_params` carries other query params (a filter, e.g.
    `/runs?target_name=...`) through Prev/Next so paging doesn't silently
    drop an active filter."""
    total_pages = max(1, -(-total // page_size))  # ceil division
    if total_pages <= 1:
        return ""

    def _url(target_page: int) -> str:
        params = dict(extra_params or {})
        params["page"] = str(target_page)
        return f"{base_url}?{urlencode(params)}"

    prev_html = (
        f'<a href="{_url(page - 1)}" style="color:#0969da;text-decoration:none;">&larr; Prev</a>'
        if page > 1
        else '<span style="color:#8c959f;">&larr; Prev</span>'
    )
    next_html = (
        f'<a href="{_url(page + 1)}" style="color:#0969da;text-decoration:none;">Next &rarr;</a>'
        if page < total_pages
        else '<span style="color:#8c959f;">Next &rarr;</span>'
    )
    return (
        '<div style="display:flex;justify-content:space-between;align-items:center;'
        'color:#57606a;font-size:13px;margin-top:12px;">'
        f"{prev_html}<span>Page {page} of {total_pages} &middot; {total} total</span>{next_html}"
        "</div>"
    )
