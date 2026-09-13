"""Top-nav strip shared by every page in the leadgen web UI. Its own tiny
module so review.py and targets.py can both use it without importing each
other."""

from __future__ import annotations

_PAGES = [
    ("/targets", "Targets"),
    ("/runs", "Run history"),
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
