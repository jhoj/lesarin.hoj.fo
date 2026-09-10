"""Migrations are now the only thing that shapes the schema, so they have to
agree with the models — and they have to cope with the databases that already
exist, including the deployed one that predates Alembic entirely.
"""

from __future__ import annotations

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from app import db, repo
from app.db import Base


@pytest.fixture(autouse=True)
def restore_engine():
    """use_database rebinds module state — put it back for the other tests."""
    original_path, original_url = db.DB_PATH, db.DATABASE_URL
    yield
    # Restore via the URL when there was one — passing the path would clear it
    # and quietly drop the rest of the suite back onto SQLite.
    db.use_database(original_url or original_path)


def test_migrations_build_the_whole_schema(tmp_path):
    db.use_database(tmp_path / "fresh.db")
    db.init_db()

    tables = set(inspect(db.engine).get_table_names())
    assert set(Base.metadata.tables) <= tables
    assert "alembic_version" in tables


def test_migrated_schema_matches_the_models(tmp_path):
    """Guards against drift: a model changed without a migration written for
    it, which would pass every other test and then fail on deploy."""
    db.use_database(tmp_path / "drift.db")
    db.init_db()

    with db.engine.connect() as connection:
        context = MigrationContext.configure(connection)
        diff = compare_metadata(context, Base.metadata)

    assert diff == [], f"models and migrations disagree: {diff}"


def test_a_pre_alembic_database_is_adopted_not_rebuilt(tmp_path):
    """The deployed SQLite file has tables but no version stamp. Startup must
    adopt it in place — creating the tables again would fail, and dropping
    anything would take every learned vendor template with it."""
    path = tmp_path / "legacy.db"
    legacy = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(legacy)  # what the old init_db() did
    legacy.dispose()

    db.use_database(path)
    with db.SessionLocal() as session:
        repo.create_vendor(
            session,
            identifier="314188",
            name="Effo",
            mappings=[{"output": "InvoiceNo", "strategy": "label", "label": "Fakturanr"}],
        )

    db.init_db()

    with db.engine.connect() as connection:
        stamped = MigrationContext.configure(connection).get_current_revision()
    assert stamped is not None

    with db.SessionLocal() as session:
        vendors = repo.list_vendors(session)
    assert [(v.identifier, v.name) for v in vendors] == [("314188", "Effo")]


def test_a_stale_version_stamp_does_not_leave_an_empty_schema(tmp_path):
    """If the tables are dropped but the version table survives, the recorded
    revision is a lie — startup has to notice and rebuild rather than trust it
    and create nothing."""
    db.use_database(tmp_path / "stale.db")
    db.init_db()

    Base.metadata.drop_all(db.engine)  # alembic_version deliberately survives
    assert not set(inspect(db.engine).get_table_names()) & set(Base.metadata.tables)

    db.init_db()

    assert set(Base.metadata.tables) <= set(inspect(db.engine).get_table_names())
