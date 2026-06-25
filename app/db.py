"""SQLite engine and session plumbing.

A single small SQLite file (``data/lesarin.db``) holds the customer's expected
output format, the known vendors, and each vendor's field mappings. Tables are
created on demand at startup — no migration tool needed for a store this small.
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# DB location is overridable (tests point it at a temp file / in-memory).
_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "lesarin.db"
DB_PATH = Path(os.environ.get("LESARIN_DB", _DEFAULT_PATH))


class Base(DeclarativeBase):
    pass


def _make_engine(url: str | None = None):
    if url is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{DB_PATH}"
    # check_same_thread=False so the cache/uvicorn worker threads can share it.
    return create_engine(url, future=True, connect_args={"check_same_thread": False})


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db() -> None:
    """Create tables if they don't exist. Safe to call repeatedly."""
    from . import db_models  # noqa: F401 — register mappers before create_all

    Base.metadata.create_all(bind=engine)


def get_session():
    """FastAPI dependency: yield a session, always closed afterwards."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
