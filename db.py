"""Database connection for the Saath AI brain (Milestone 1).

The prototype stores data in a local SQLite file (see DATABASE_URL in
`.env`). Switching to PostgreSQL later means changing that one setting,
because all database access goes through SQLAlchemy.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from config import settings


class Base(DeclarativeBase):
    """Base class every database table inherits from."""


_engine = None
_SessionFactory = None


def get_engine():
    """Create (once) and return the database engine."""
    global _engine
    if _engine is None:
        connect_args = {}
        if settings.database_url.startswith("sqlite"):
            connect_args = {"check_same_thread": False}
        _engine = create_engine(settings.database_url, connect_args=connect_args)
    return _engine


def get_session_factory():
    """Create (once) and return the session factory."""
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=get_engine())
    return _SessionFactory


def init_db() -> None:
    """Create all tables. Runs once when the server starts."""
    import models  # noqa: F401  (importing registers the tables)

    Base.metadata.create_all(get_engine())


def get_session():
    """FastAPI dependency: hands each request its own database session."""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()
