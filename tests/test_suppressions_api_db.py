"""Real-Postgres, real-HTTP tests for api/suppressions.py's create flow --
the manual lever behind the hard rule "suppression check before every
send" (send/suppression.py). `test_suppressions_api.py` covers
`_normalise_email` in isolation; this covers the actual insert path,
previously verified only by hand (docs/14) via direct HTTP requests
against a live server.
"""

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from leadgen.db.models import Suppression


def test_list_suppressions_empty(client, db_env) -> None:
    resp = client.get("/suppressions")
    assert resp.status_code == 200
    assert "No suppressions yet" in resp.text


def test_add_suppression_email_normalises_and_persists(client, db_env) -> None:
    resp = client.post(
        "/suppressions",
        data={"scope": "email", "value": "  Lead@Example.COM  ", "reason": "asked to stop"},
        follow_redirects=False,
    )

    assert resp.status_code == 303

    verify = sessionmaker(bind=db_env)()
    row = verify.execute(select(Suppression).where(Suppression.scope == "email")).scalar_one()
    assert row.value == "lead@example.com"
    assert row.reason == "asked to stop"
    verify.close()


def test_add_suppression_domain_normalises_and_persists(client, db_env) -> None:
    resp = client.post(
        "/suppressions",
        data={"scope": "domain", "value": "WWW.Example.com/", "reason": "spam complaint"},
        follow_redirects=False,
    )

    assert resp.status_code == 303

    verify = sessionmaker(bind=db_env)()
    row = verify.execute(select(Suppression).where(Suppression.scope == "domain")).scalar_one()
    assert row.value == "example.com"
    verify.close()


def test_add_suppression_rejects_duplicate(client, db_env) -> None:
    client.post("/suppressions", data={"scope": "email", "value": "lead@example.com", "reason": "r1"})

    resp = client.post("/suppressions", data={"scope": "email", "value": "lead@example.com", "reason": "r2"})

    assert resp.status_code == 400
    assert "already suppressed" in resp.text

    verify = sessionmaker(bind=db_env)()
    rows = verify.execute(select(Suppression).where(Suppression.value == "lead@example.com")).scalars().all()
    assert len(rows) == 1
    verify.close()


def test_add_suppression_rejects_malformed_email_without_writing(client, db_env) -> None:
    resp = client.post("/suppressions", data={"scope": "email", "value": "not-an-email", "reason": "x"})

    assert resp.status_code == 400
    verify_rows = client.get("/suppressions")
    assert "No suppressions yet" in verify_rows.text


def test_add_suppression_rejects_empty_reason_instead_of_422(client, db_env) -> None:
    """docs/09/docs/14's Form(...)-treats-empty-as-missing footgun --
    `reason` must be `Form("")` + a manual check, or FastAPI 422s before
    this route's own validation ever runs."""
    resp = client.post("/suppressions", data={"scope": "email", "value": "lead@example.com", "reason": ""})

    assert resp.status_code == 400
    assert "Reason is required" in resp.text


def test_list_suppressions_shows_added_rows(client, db_env) -> None:
    client.post("/suppressions", data={"scope": "email", "value": "lead@example.com", "reason": "asked to stop"})

    resp = client.get("/suppressions")

    assert "lead@example.com" in resp.text
    assert "asked to stop" in resp.text
