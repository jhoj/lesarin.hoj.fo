"""End-to-end /api flow: setup table → vendor template → upload → read."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import Base, engine
from app.main import app


@pytest.fixture(autouse=True)
def fresh_db():
    """Reset the SQLite tables around each test for isolation."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def test_output_fields_crud(client):
    # The canonical vocabulary is seeded on startup, so assert by membership
    # rather than exact contents.
    assert client.post("/api/output-fields", json={"key": "VendorNumber"}).status_code == 200
    keys = {f["key"] for f in client.get("/api/output-fields").json()}
    assert "VendorNumber" in keys
    assert client.delete("/api/output-fields/VendorNumber").status_code == 200
    assert "VendorNumber" not in {f["key"] for f in client.get("/api/output-fields").json()}


def test_full_teach_and_read_flow(client, sample_invoice_pdf):
    # 1. Define expected outputs.
    client.post("/api/output-fields", json={"key": "InvoiceNumber", "value_type": "string"})
    client.post("/api/output-fields", json={"key": "DueDate", "value_type": "date"})

    # 2. Teach a vendor (V-tal + mappings = its template).
    vendor = client.post("/api/vendors", json={
        "identifier": "314188", "name": "Effo", "match_keywords": ["Føroya Handil"],
        "mappings": [
            {"output": "InvoiceNumber", "strategy": "label", "label": "Fakturanr"},
            {"output": "DueDate", "strategy": "label", "label": "Forfaldsdato", "value_type": "date"},
        ],
    }).json()
    assert len(vendor["mappings"]) == 2

    # 3. Upload a PDF → vendor auto-detected by V-tal.
    info = client.post(
        "/api/documents", files={"file": ("inv.pdf", sample_invoice_pdf, "application/pdf")}
    ).json()
    assert info["detected_vendor"]["name"] == "Effo"
    assert info["n_pages"] == 1

    # 4. The cached PDF can be fetched back (for the viewer).
    f = client.get(f"/api/documents/{info['doc_id']}/file")
    assert f.status_code == 200 and f.headers["content-type"] == "application/pdf"

    # 5. Read with the template → located values, tagged by source.
    read = client.post(
        f"/api/documents/{info['doc_id']}/read", json={"fields": vendor["mappings"]}
    ).json()
    by_output = {f["output"]: f for f in read["fields"]}
    assert by_output["InvoiceNumber"]["value"] == "2026-0014"
    assert by_output["DueDate"]["value"] == "2026-01-26"
    assert all(f["source"] == "template-label" for f in read["fields"])
    assert len(read["lines"]) == 3


def test_production_extract_uses_detected_template(client, sample_invoice_pdf):
    client.post("/api/vendors", json={
        "identifier": "314188", "name": "Effo", "match_keywords": ["Føroya Handil"],
        "mappings": [{"output": "InvoiceNumber", "strategy": "label", "label": "Fakturanr"}],
    })
    result = client.post(
        "/api/extract", files={"file": ("inv.pdf", sample_invoice_pdf, "application/pdf")}
    ).json()
    assert result["fields"][0]["output"] == "InvoiceNumber"
    assert result["fields"][0]["value"] == "2026-0014"


def test_unknown_document_read_404(client):
    r = client.post("/api/documents/deadbeef/read", json={"fields": []})
    assert r.status_code == 404


def test_output_field_aliases_roundtrip(client):
    client.post("/api/output-fields", json={"key": "Vtal", "aliases": ["Vtal", "V-Tal"]})
    fields = {f["key"]: f for f in client.get("/api/output-fields").json()}
    assert fields["Vtal"]["aliases"] == ["Vtal", "V-Tal"]


def test_read_auto_matches_via_field_aliases(client, sample_invoice_pdf):
    # The field's read-label ("Fakturanr") is taught as an alias; the mapping's
    # own label is wrong, so the value can only be found through the alias.
    client.post("/api/output-fields", json={"key": "InvoiceNumber", "aliases": ["Fakturanr"]})
    info = client.post(
        "/api/documents", files={"file": ("inv.pdf", sample_invoice_pdf, "application/pdf")}
    ).json()
    read = client.post(
        f"/api/documents/{info['doc_id']}/read",
        json={"fields": [{"output": "InvoiceNumber", "strategy": "label", "label": "Nope"}]},
    ).json()
    assert read["fields"][0]["value"] == "2026-0014"


def test_suggest_fields_endpoint(client, sample_invoice_pdf):
    info = client.post(
        "/api/documents", files={"file": ("inv.pdf", sample_invoice_pdf, "application/pdf")}
    ).json()
    res = client.get(f"/api/documents/{info['doc_id']}/suggest-fields").json()
    by_key = {s["suggested_key"]: s for s in res["suggestions"]}
    assert by_key["InvoiceNo"]["value"] == "2026-0014"
    assert by_key["InvoiceNo"]["read_labels"]  # editable synonyms to accept
    assert "VendorName" in by_key


def test_init_db_back_fills_aliases_column(tmp_path):
    # Simulate an OLD database whose output_fields predates the aliases column.
    from sqlalchemy import create_engine
    from app import db

    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with eng.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE output_fields "
            "(id INTEGER PRIMARY KEY, key VARCHAR, display_name VARCHAR, value_type VARCHAR, sort_order INTEGER)"
        )
    db._ensure_columns(eng)
    with eng.begin() as conn:
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(output_fields)")}
    assert "aliases" in cols
