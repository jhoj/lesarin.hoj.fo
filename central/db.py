"""SQLite engine for the central service — a store entirely separate from a
site's own ``data/lesarin.db`` (see ``app/db.py``). Same "no migration tool
needed at this size" approach: tables created on demand at startup.
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "central.db"
DB_PATH = Path(os.environ.get("CENTRAL_DB", _DEFAULT_PATH))


class Base(DeclarativeBase):
    pass


def _make_engine(url: str | None = None):
    if url is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{DB_PATH}"
    return create_engine(url, future=True, connect_args={"check_same_thread": False})


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db() -> None:
    from . import models  # noqa: F401 — register mappers before create_all

    Base.metadata.create_all(bind=engine)


def use_database(path: str | Path) -> None:
    global engine, DB_PATH
    DB_PATH = Path(path)
    engine = _make_engine()
    SessionLocal.configure(bind=engine)


def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
