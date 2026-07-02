"""The shared extraction engine: vendor template first, heuristics fill gaps."""

from __future__ import annotations

import pytest

from app import engine, repo
from app.db import Base, SessionLocal, engine as db_engine, init_db
from app.extraction import loader


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(db_engine)
    Base.metadata.create_all(db_engine)
    init_db()
    yield
    Base.metadata.drop_all(db_engine)


def _seed_vendor(session):
    return repo.create_vendor(
        session, identifier="314188", name="Effo", match_keywords=["Føroya Handil"],
        mappings=[
            {"output": "InvoiceNo", "strategy": "label", "label": "Fakturanr"},
            {"output": "DueDate", "strategy": "label", "label": "Forfaldsdato", "value_type": "date"},
        ],
    )


def test_unmapped_document_falls_back_to_heuristics(sample_invoice_pdf):
    document = loader.load(sample_invoice_pdf)
    with SessionLocal() as session:
        ext = engine.extract(session, document)
    assert ext.matched is False
    assert ext.source == "heuristic"
    # The heuristics still locate the invoice number, tagged as such.
    assert ext.fields["InvoiceNo"].value == "2026-0014"
    assert ext.fields["InvoiceNo"].source == "heuristic"


def test_mapped_document_uses_the_template(sample_invoice_pdf):
    with SessionLocal() as session:
        _seed_vendor(session)
        document = loader.load(sample_invoice_pdf)
        ext = engine.extract(session, document)
    assert ext.matched is True
    assert ext.source == "template"
    assert ext.fields["InvoiceNo"].value == "2026-0014"
    assert ext.fields["InvoiceNo"].source == "template-label"
    assert ext.fields["DueDate"].value == "2026-01-26"
    # The applied mapping is reported back (Result<mapping>).
    assert {m["output"] for m in ext.applied_template} == {"InvoiceNo", "DueDate"}


def test_values_returns_only_found(sample_invoice_pdf):
    document = loader.load(sample_invoice_pdf)
    with SessionLocal() as session:
        ext = engine.extract(session, document)
    values = ext.values()
    assert all(v is not None for v in values.values())
    assert values.get("InvoiceNo") == "2026-0014"
