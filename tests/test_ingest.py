"""The structured-sources extension point: a KnowledgeSource yields
bundle-shaped dicts, ingest() applies each one attributed to a Site — and a
push through it publishes immediately, exactly like a real site's push."""

from __future__ import annotations

import atexit
import json
import os
import tempfile

import pytest

_fd, _tmp_central_db = tempfile.mkstemp(suffix=".central-ingest-test.db")
os.close(_fd)
os.environ.setdefault("CENTRAL_DB", _tmp_central_db)
atexit.register(lambda: os.path.exists(_tmp_central_db) and os.remove(_tmp_central_db))

from central.db import Base, SessionLocal, engine, init_db
from central.ingest import LocalFileSource, ingest
from central.models import Site, VendorTemplate


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    init_db()
    yield
    Base.metadata.drop_all(engine)


def test_local_file_source_ingests_a_confirmed_bundle(tmp_path):
    bundle = {
        "bundle_version": 1,
        "confirmed_templates": [{
            "identifier": "700100", "identifier_kind": "vtal", "name": "Dataset Vendor",
            "match_keywords": [], "layout_fingerprint": "fp-dataset",
            "mappings": [{"output": "InvoiceNo", "strategy": "label", "label": "Fakturanr",
                          "relation": "right", "value_type": "string", "page": None, "bbox": None,
                          "confirmed": True}],
        }],
        "label_observations": [],
    }
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(bundle), encoding="utf-8")

    with SessionLocal() as session:
        site = Site(name="dataset-ingest", public_key="n/a", fingerprint="dataset-fp", status="active")
        session.add(site)
        session.commit()
        session.refresh(site)

        results = ingest(session, LocalFileSource([path]), site)
        assert results == [{
            "templates_created": 1, "templates_replaced": 0, "vocabulary_revealed": 0,
        }]

        template = session.query(VendorTemplate).filter_by(identifier="700100").one()
        assert template.withdrawn_at is None
        assert template.contributed_by_fingerprint == "dataset-fp"
