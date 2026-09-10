"""Engine and session plumbing, for SQLite or Postgres.

Two deployments, one data layer:

* **SQLite** (default) — a single file, zero configuration. This is what the
  CLI, the TUI and a self-hosted site use, and what the tests run against.
* **Postgres** — set ``LESARIN_DATABASE_URL`` and the service uses that
  instead, which is what lets the deployed API run more than one worker.

Schema changes go through Alembic either way; :func:`init_db` applies any
outstanding migrations at startup, so neither deployment needs a manual step.
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, select
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# DB location is overridable (tests point it at a temp file / in-memory).
_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "lesarin.db"
DB_PATH = Path(os.environ.get("LESARIN_DB", _DEFAULT_PATH))
# A full SQLAlchemy URL wins over the SQLite path when set, e.g.
# postgresql+psycopg://lesarin:...@localhost/lesarin
DATABASE_URL = os.environ.get("LESARIN_DATABASE_URL") or None


class Base(DeclarativeBase):
    pass


def current_url() -> str:
    if DATABASE_URL:
        return DATABASE_URL
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{DB_PATH}"


def _make_engine(url: str | None = None):
    url = url or current_url()
    if url.startswith("sqlite"):
        # check_same_thread=False so the cache/uvicorn worker threads can share it.
        eng = create_engine(url, future=True, connect_args={"check_same_thread": False})

        # SQLite ignores foreign keys unless asked, per connection. Without
        # this every ondelete in the schema is decoration: CASCADE doesn't
        # clean up, SET NULL doesn't null, and the two databases behave
        # differently under the same code.
        @event.listens_for(eng, "connect")
        def _enforce_foreign_keys(dbapi_connection, _record):  # pragma: no cover - trivial
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        return eng
    # Postgres: recycle connections the server may have closed underneath us,
    # which a long-lived service behind a proxy will otherwise trip over.
    return create_engine(url, future=True, pool_pre_ping=True)


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db() -> None:
    """Bring the database up to date. Safe to call repeatedly."""
    from . import db_models  # noqa: F401 — register mappers before any DDL

    run_migrations()
    seed_canonical_fields()


def run_migrations() -> None:
    """Apply outstanding Alembic migrations against the current engine.

    Databases that predate Alembic (the deployed SQLite file, or a test that
    built its schema straight from the models) already have the tables but no
    version stamp. Running an upgrade there would try to create what exists, so
    those are stamped at the baseline first and then upgraded normally.
    """
    from alembic import command
    from alembic.autogenerate import compare_metadata
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext

    root = Path(__file__).resolve().parent.parent
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", current_url())
    config.attributes["connection"] = engine

    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        stamped = context.get_current_revision() is not None

    present = set(inspect(engine).get_table_names())
    has_tables = bool(present & set(Base.metadata.tables))

    if has_tables and not stamped:
        # An untracked database with tables in it. Where it stands depends on
        # what's actually there: a pre-Alembic production file is at the
        # baseline, while a schema just built from the models (create_all, as
        # the tests do) is already current. Ask the schema rather than guess —
        # stamping the wrong one either re-runs migrations against columns that
        # exist, or skips migrations that are genuinely needed.
        with engine.connect() as connection:
            diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
        command.stamp(config, "head" if not diff else _BASELINE_REVISION)
    elif stamped and not has_tables:
        # The stamp outlived the schema — someone dropped the tables and the
        # version table survived, so the recorded revision is a lie. Upgrading
        # from it would create nothing at all; start over from base.
        command.stamp(config, "base", purge=True)

    command.upgrade(config, "head")


# The first migration describes the schema as it stood before Alembic existed,
# so an untracked database can be adopted by stamping this and moving forward.
_BASELINE_REVISION = "0001_baseline"


def seed_canonical_fields() -> None:
    """Ensure the shared canonical vocabulary exists as output_fields rows.

    These are the keys vendor templates and customer profiles agree on. Seeding
    is idempotent and never overwrites a row a user may have customised.
    """
    from .canonical import CANONICAL_FIELDS, CANONICAL_ORDER
    from .db_models import OutputField

    with SessionLocal() as session:
        existing = {f.key for f in session.scalars(select(OutputField))}
        for order, (key, spec) in enumerate(CANONICAL_FIELDS.items()):
            if key in existing:
                continue
            session.add(
                OutputField(
                    key=key,
                    display_name=spec["display_name"],
                    value_type=spec["value_type"],
                    sort_order=CANONICAL_ORDER.index(key),
                    aliases=[],
                )
            )
        session.commit()


def use_database(path_or_url: str | Path) -> None:
    """Point the engine + session factory at a different database.

    Lets the CLI honour ``--db`` without a process restart; the module-level
    ``engine`` (used by ``init_db``) and ``SessionLocal`` are both rebound.
    Accepts a SQLite path or a full URL — and clears any URL from the
    environment when given a path, so ``--db`` always wins over
    ``LESARIN_DATABASE_URL``.
    """
    global engine, DB_PATH, DATABASE_URL
    text = str(path_or_url)
    if "://" in text:
        DATABASE_URL = text
    else:
        DATABASE_URL = None
        DB_PATH = Path(text)
    engine = _make_engine()
    SessionLocal.configure(bind=engine)


def get_session():
    """FastAPI dependency: yield a session, always closed afterwards."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
