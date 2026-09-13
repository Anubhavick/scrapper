"""Shared fixtures for the Postgres-backed test tier (ROADMAP.md item 12,
docs/22): db/repository.py, db/persist.py, db/campaigns.py, and
db/orchestration.py can't be exercised meaningfully against SQLite (their
JSONB/UUID columns aren't SQLite-compatible, per CLAUDE.md's Architecture
notes) and were previously verified by hand only, every time.

Every fixture here talks to a real, ephemeral Postgres spun up via
testcontainers -- **never** the dev `DATABASE_URL` from `.env`, which can
hold real campaign/send data (a real send happened 2026-09-13, see
CLAUDE.md). Reusing that database for tests would risk touching real
records; a fresh per-test-session container makes that structurally
impossible instead of relying on every test author remembering not to.

Tests that need this tier live in `test_*_db.py` files and pull in
`db_session` (direct db/*.py function tests, wrapped in a rolled-back
transaction) or `db_env` (full FastAPI-route tests that go through
`leadgen.db.session.session_scope()`, cleaned up with a real TRUNCATE).
Everything else in tests/ is unaffected and still needs no Docker/Postgres
at all -- `uv run pytest` on a machine with no Docker daemon just skips
this tier instead of failing the whole suite.
"""

from __future__ import annotations

import base64
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

import leadgen.db.session as db_session_module
from leadgen.db.models import (
    Base,
    Business,
    Campaign,
    CampaignMailbox,
    Contact,
    Mailbox,
    Message,
    TargetRun,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_migrations(database_url: str) -> None:
    """Applies the real migration history (not `Base.metadata.create_all`)
    so this tier exercises the same schema-creation path production
    does -- `alembic/env.py` reads `DATABASE_URL` from the environment,
    so this sets it for the duration of the upgrade call only, restoring
    whatever was there before (nothing, normally, since pytest doesn't
    otherwise need it)."""
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    try:
        cfg = Config(str(REPO_ROOT / "alembic.ini"))
        command.upgrade(cfg, "head")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


@pytest.fixture(scope="session")
def db_engine():
    """One ephemeral Postgres 16 container for the whole test session,
    migrated once via real Alembic history. Skips (never errors) the
    whole DB tier when Docker isn't available, so `uv run pytest` still
    passes on a machine with no Docker daemon -- CI and any dev machine
    with Docker running get the real coverage; anyone else still gets
    everything else this suite already covered."""
    try:
        from testcontainers.community.postgres import PostgresContainer
    except ImportError:
        pytest.skip("testcontainers not installed -- Postgres-backed test tier unavailable")

    try:
        container = PostgresContainer("postgres:16", driver="psycopg")
        container.start()
    except Exception as exc:  # noqa: BLE001 -- docker unavailable/misconfigured, not a code bug
        pytest.skip(f"could not start a Postgres testcontainer (is Docker running?): {exc}")

    try:
        url = container.get_connection_url()
        _run_migrations(url)
        engine = create_engine(url)
        yield engine
        engine.dispose()
    finally:
        container.stop()


@pytest.fixture
def db_session(db_engine):
    """One Session per test, wrapped in an outer transaction that is
    always rolled back at teardown -- via SQLAlchemy's documented
    join-a-session-to-an-external-transaction/SAVEPOINT recipe, so even a
    real `session.commit()` inside the code under test (db/orchestration
    .py's `_reserve_fn` does this on purpose, see its docstring) doesn't
    leave rows behind for the next test. Use this for direct db/*.py
    function tests."""
    connection = db_engine.connect()
    outer_transaction = connection.begin()
    session_factory = sessionmaker(bind=connection)
    session = session_factory()

    nested = connection.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def _restart_savepoint(sess, transaction) -> None:
        nonlocal nested
        if not nested.is_active:
            nested = connection.begin_nested()

    try:
        yield session
    finally:
        session.close()
        outer_transaction.rollback()
        connection.close()


@pytest.fixture
def db_env(db_engine, monkeypatch):
    """For tests that exercise real API routes: those call
    `leadgen.db.session.session_scope()` directly (a module-level
    function, not a FastAPI `Depends`), so this points that module's
    cached engine/sessionmaker at the test container instead. Route code
    commits for real, against its own pooled connections from
    `db_engine` -- not the single connection `db_session` controls -- so
    cleanup here is a real `TRUNCATE ... CASCADE` after the test rather
    than a rollback."""
    monkeypatch.setattr(db_session_module, "_engine", db_engine)
    monkeypatch.setattr(db_session_module, "_sessionmaker", sessionmaker(bind=db_engine))
    yield db_engine
    table_names = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    with db_engine.begin() as conn:
        conn.exec_driver_sql(f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE")


@pytest.fixture
def client(db_env, monkeypatch) -> TestClient:
    """A TestClient for the real FastAPI app (`leadgen.api.review.app`,
    which mounts every other api/ router), authenticated, and pointed at
    the same ephemeral test database `db_env` just wired up. Depends on
    `db_env` (not just `db_engine`) so any test using this fixture gets
    the real-DB routing and the post-test TRUNCATE for free."""
    monkeypatch.setenv("WEB_UI_USERNAME", "test-user")
    monkeypatch.setenv("WEB_UI_PASSWORD", "test-pass")
    from leadgen.api.review import app

    token = base64.b64encode(b"test-user:test-pass").decode()
    return TestClient(app, headers={"Authorization": f"Basic {token}"})


# ------------------------------------------------------------- factories
#
# Plain helpers, not fixtures -- each test decides exactly which fields
# matter to it and overrides them; everything else gets a valid-per-
# constraint default so tests read as "what's different here", not a
# wall of required boilerplate. All take `session` first and flush before
# returning so FK-dependent follow-up inserts (a Contact needing its
# Business's id, say) always see a real primary key.


def make_business(session: Session, **overrides) -> Business:
    defaults = dict(
        name="Test Business",
        business_type="dentist",
        source="csv",
        source_id=str(uuid.uuid4()),
    )
    defaults.update(overrides)
    business = Business(**defaults)
    session.add(business)
    session.flush()
    return business


def make_contact(session: Session, business_id, **overrides) -> Contact:
    defaults = dict(
        business_id=business_id,
        email=f"test-{uuid.uuid4().hex[:8]}@example.com",
        source="website",
        fetched_at=datetime.now(timezone.utc),
        legal_basis="public_contact_published_on_business_website",
    )
    defaults.update(overrides)
    contact = Contact(**defaults)
    session.add(contact)
    session.flush()
    return contact


def make_mailbox(session: Session, **overrides) -> Mailbox:
    tag = uuid.uuid4().hex[:8]
    defaults = dict(
        name=f"test-mailbox-{tag}",
        email_address=f"test-mailbox-{tag}@example.com",
        oauth_refresh_token_encrypted="encrypted-placeholder",
        daily_cap=50,
        is_active=True,
    )
    defaults.update(overrides)
    mailbox = Mailbox(**defaults)
    session.add(mailbox)
    session.flush()
    return mailbox


#: A complete, valid TargetProfile YAML -- `db/orchestration.py`'s
#: `build_send_jobs()` parses `profile_yaml` for real (the `legal_region`
#: gate from docs/20), so a placeholder missing required fields breaks
#: any route that renders a campaign's send preview, not just tests that
#: care about the profile's content. Override `profile_yaml` explicitly
#: (see test_orchestration_db.py's EU_PROFILE_YAML) for tests that do.
DEFAULT_PROFILE_YAML = yaml.safe_dump({
    "name": "test-target",
    "business_type": "dentist",
    "legal_region": "us",
    "location": {"mode": "radius", "center": "Test City", "radius_km": 10},
    "source": {"primary": "overpass"},
    "outreach": {"offer_id": "appointment-automation", "sender_pool": ["s1"]},
})


def make_target_run(session: Session, **overrides) -> TargetRun:
    defaults = dict(
        target_name="test-target",
        profile_hash="hash",
        profile_yaml=DEFAULT_PROFILE_YAML,
        status="completed",
    )
    defaults.update(overrides)
    run = TargetRun(**defaults)
    session.add(run)
    session.flush()
    return run


def make_campaign(session: Session, target_run_id, mailbox_ids: list = (), **overrides) -> Campaign:
    defaults = dict(target_run_id=target_run_id, offer_id="appointment-automation")
    defaults.update(overrides)
    campaign = Campaign(**defaults)
    session.add(campaign)
    session.flush()
    for mailbox_id in mailbox_ids:
        session.add(CampaignMailbox(campaign_id=campaign.id, mailbox_id=mailbox_id))
    session.flush()
    return campaign


def make_message(session: Session, campaign_id, contact_id, mailbox_id=None, **overrides) -> Message:
    defaults = dict(
        campaign_id=campaign_id,
        contact_id=contact_id,
        mailbox_id=mailbox_id,
        status="approved",
        subject="subject",
        body="body",
    )
    defaults.update(overrides)
    message = Message(**defaults)
    session.add(message)
    session.flush()
    return message
