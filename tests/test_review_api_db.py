"""Real-Postgres, real-HTTP tests for api/review.py's `/runs` and
`/runs/{id}` -- the actual "history of previous scans" view
(CLAUDE.md), reading persisted `target_runs`/`target_run_businesses`/
`businesses`/`contacts` instead of a CSV. Previously verified only by
hand (docs/09).
"""

from datetime import datetime, timezone

from sqlalchemy.orm import sessionmaker

from leadgen.db.models import Contact, TargetRunBusiness
from tests.conftest import make_business, make_target_run


def test_list_runs_shows_persisted_runs(client, db_env) -> None:
    session_factory = sessionmaker(bind=db_env)
    setup = session_factory()
    make_target_run(setup, target_name="dentists-austin-tx")
    setup.commit()
    setup.close()

    resp = client.get("/runs")

    assert resp.status_code == 200
    assert "dentists-austin-tx" in resp.text


def test_list_runs_filters_by_target_name(client, db_env) -> None:
    session_factory = sessionmaker(bind=db_env)
    setup = session_factory()
    make_target_run(setup, target_name="dentists-austin-tx")
    make_target_run(setup, target_name="dentists-gurugram")
    setup.commit()
    setup.close()

    resp = client.get("/runs", params={"target_name": "dentists-gurugram"})

    assert "dentists-gurugram" in resp.text
    assert "dentists-austin-tx" not in resp.text


def test_show_run_not_found_returns_404(client, db_env) -> None:
    import uuid

    resp = client.get(f"/runs/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_show_run_invalid_id_returns_404(client, db_env) -> None:
    resp = client.get("/runs/not-a-uuid")
    assert resp.status_code == 404


def test_show_run_lists_its_businesses_and_contacts(client, db_env) -> None:
    session_factory = sessionmaker(bind=db_env)
    setup = session_factory()
    run = make_target_run(setup, target_name="dentists-austin-tx")
    business = make_business(setup, name="Smile Dental Clinic")
    setup.add(
        TargetRunBusiness(target_run_id=run.id, business_id=business.id, qualified=True, crawl_status="ok", tags=[])
    )
    setup.add(
        Contact(
            business_id=business.id, email="jane@smile-dental.example.com", source="website",
            fetched_at=datetime.now(timezone.utc), legal_basis="public_contact_published_on_business_website",
        )
    )
    setup.commit()
    run_id = run.id
    setup.close()

    resp = client.get(f"/runs/{run_id}")

    assert resp.status_code == 200
    assert "Smile Dental Clinic" in resp.text
    assert "jane@smile-dental.example.com" in resp.text


def test_show_run_qualified_filter(client, db_env) -> None:
    session_factory = sessionmaker(bind=db_env)
    setup = session_factory()
    run = make_target_run(setup)
    qualified_biz = make_business(setup, name="Qualified Dental")
    unqualified_biz = make_business(setup, name="Unqualified Dental")
    setup.add(
        TargetRunBusiness(
            target_run_id=run.id, business_id=qualified_biz.id, qualified=True, crawl_status="ok", tags=[]
        )
    )
    setup.add(
        TargetRunBusiness(
            target_run_id=run.id, business_id=unqualified_biz.id, qualified=False, crawl_status="ok", tags=[]
        )
    )
    setup.commit()
    run_id = run.id
    setup.close()

    resp = client.get(f"/runs/{run_id}", params={"qualified": "yes"})

    assert "Qualified Dental" in resp.text
    assert "Unqualified Dental" not in resp.text
