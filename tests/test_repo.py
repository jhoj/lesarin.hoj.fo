"""Vendor / output-field CRUD and V-tal detection over a throwaway SQLite file."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import sessionmaker

import app.db_models  # noqa: F401 — register mappers
from app.db import Base, _make_engine
from app import repo


@pytest.fixture()
def session(tmp_path):
    engine = _make_engine(f"sqlite:///{tmp_path / 'repo.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)()


def test_output_field_upsert_and_list(session):
    repo.upsert_output_field(session, "VendorNumber", "Vendor number", "string", 1)
    repo.upsert_output_field(session, "DueDate", "Due date", "date", 2)
    # Upsert again updates rather than duplicates.
    repo.upsert_output_field(session, "VendorNumber", "Supplier number", "string", 0)

    fields = repo.list_output_fields(session)
    keys = [f.key for f in fields]
    assert keys == ["VendorNumber", "DueDate"]  # sorted by sort_order
    assert fields[0].display_name == "Supplier number"


def test_create_vendor_with_mappings(session):
    vendor = repo.create_vendor(
        session,
        identifier="314188",
        name="Effo",
        match_keywords=["Effo"],
        mappings=[
            {
                "output": "VendorNumber", "strategy": "label", "label": "Veitara nr.",
                "relation": "right", "value_type": "string", "page": 1,
                "bbox": [10, 20, 80, 30],
            }
        ],
    )
    assert vendor.id is not None
    assert len(vendor.mappings) == 1
    assert vendor.mappings[0].output_key == "VendorNumber"
    assert vendor.mappings[0].bbox == [10, 20, 80, 30]


def test_update_vendor_replaces_mappings(session):
    vendor = repo.create_vendor(session, identifier="1", name="X", mappings=[
        {"output": "A", "strategy": "label", "label": "a"},
    ])
    updated = repo.update_vendor(session, vendor.id, name="X2", mappings=[
        {"output": "B", "strategy": "region", "page": 1, "bbox": [1, 2, 3, 4]},
    ])
    assert updated.name == "X2"
    assert [m.output_key for m in updated.mappings] == ["B"]
    assert updated.mappings[0].strategy == "region"


def test_detect_vendor_by_vtal_and_keyword(session):
    repo.create_vendor(session, identifier="314188", name="Effo", match_keywords=["Føroya Handil"])
    repo.create_vendor(session, identifier="557788", name="Other")

    assert repo.detect_vendor(session, "Faktura\nVtal: 314 188\n").name == "Effo"
    assert repo.detect_vendor(session, "From Føroya Handil P/F").name == "Effo"
    assert repo.detect_vendor(session, "Reg 55-77-88 here").name == "Other"
    assert repo.detect_vendor(session, "no identifiers at all") is None
