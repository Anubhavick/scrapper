"""Engine/session setup. Mirrors alembic/env.py: reads DATABASE_URL from
the environment via python-dotenv rather than a hardcoded default, so the
same code points at local, staging, or prod Postgres by env alone."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

__all__ = ["get_engine", "get_sessionmaker", "session_scope"]

_engine: Engine | None = None
_sessionmaker: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        load_dotenv()
        database_url = os.environ["DATABASE_URL"]
        _engine = create_engine(database_url)
    return _engine


def get_sessionmaker() -> sessionmaker[Session]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = sessionmaker(bind=get_engine())
    return _sessionmaker


@contextmanager
def session_scope() -> Iterator[Session]:
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
