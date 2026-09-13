"""Pure-function tests for api/campaigns.py's bulk-approve helpers -- the
routes themselves need a real Postgres (same JSONB/UUID-vs-SQLite reason
as every other DB-touching api/ module, CLAUDE.md/MANUAL.md §7), verified
manually instead (docs/19)."""

import uuid

from leadgen.api.campaigns import _bulk_approve_phrase, _render_bulk_approve


def test_bulk_approve_phrase_includes_the_count() -> None:
    assert _bulk_approve_phrase(7) == "approve all 7"


def test_render_bulk_approve_hidden_for_zero_queued() -> None:
    assert _render_bulk_approve(uuid.uuid4(), "alice", 0, "") == ""


def test_render_bulk_approve_hidden_for_exactly_one_queued() -> None:
    # A single queued message already has its own Approve button --
    # bulk-approve is for the "large campaign" tedium case, not a second
    # way to do the same one-click thing.
    assert _render_bulk_approve(uuid.uuid4(), "alice", 1, "") == ""


def test_render_bulk_approve_shown_for_multiple_queued() -> None:
    html = _render_bulk_approve(uuid.uuid4(), "alice", 5, "")
    assert "approve all 5" in html
    assert "Approve all queued (5)" in html


def test_render_bulk_approve_shows_error() -> None:
    html = _render_bulk_approve(uuid.uuid4(), "alice", 5, "Type exactly 'approve all 5' to confirm.")
    assert "Type exactly" in html


def test_render_bulk_approve_escapes_approver_name() -> None:
    html = _render_bulk_approve(uuid.uuid4(), "<script>alert(1)</script>", 3, "")
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html
