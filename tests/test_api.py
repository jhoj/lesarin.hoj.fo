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
    assert client.post("/api/output-fields", json={"key": "VendorNumber"}).status_code == 200
    fields = client.get("/api/output-fields").json()
    assert [f["key"] for f in fields] == ["VendorNumber"]
    assert client.delete("/api/output-fields/VendorNumber").status_code == 200
    assert client.get("/api/output-fields").json() == []


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
