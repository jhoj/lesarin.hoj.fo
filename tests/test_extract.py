"""End-to-end extraction tests against a generated digital invoice."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.extraction import fields as field_extractor
from app.extraction import lines as line_extractor
from app.extraction import loader
from app.main import app


def _result(pdf_bytes: bytes):
    document = loader.load(pdf_bytes)
    config = field_extractor.load_config()
    result = field_extractor.extract(document, filename="sample.pdf", config=config)
    result.lines = line_extractor.extract_line_items(document, config)
    return result, document


def test_invoice_number_found_with_location(sample_invoice_pdf):
    result, _ = _result(sample_invoice_pdf)
    assert result.invoiceno.value == "2026-0014"
    assert result.invoiceno.page == 1
    assert result.invoiceno.bbox is not None and len(result.invoiceno.bbox) == 4
    assert result.invoiceno.source_label.lower().startswith("faktura")


def test_dates_normalised_to_iso(sample_invoice_pdf):
    result, _ = _result(sample_invoice_pdf)
    assert result.sentdate.value == "2026-01-12"
    assert result.sentdate.raw == "12-01-2026"
    assert result.paydate.value == "2026-01-26"
    # sent and pay dates must be distinct values located separately
    assert result.sentdate.bbox != result.paydate.bbox


def test_vendor_is_sender_not_buyer(sample_invoice_pdf):
    result, _ = _result(sample_invoice_pdf)
    assert result.vendor.name.value is not None
    assert "Føroya Handil" in result.vendor.name.value
    assert "Keypari" not in result.vendor.name.value
    assert result.vendor.name.bbox is not None


def test_line_items_extracted_with_positions(sample_invoice_pdf):
    result, _ = _result(sample_invoice_pdf)
    assert len(result.lines) >= 3
    first = result.lines[0]
    assert first.description.value is not None
    assert first.amount.value is not None
    assert first.description.bbox is not None


def test_meta_counts(sample_invoice_pdf):
    result, document = _result(sample_invoice_pdf)
    assert result.meta.pages == document.n_pages
    assert result.meta.ocr_used is False
    assert result.meta.fields_found == 4
    assert result.meta.fields_total == 4


def test_api_extract_endpoint(sample_invoice_pdf):
    client = TestClient(app)
    response = client.post(
        "/extract",
        files={"file": ("sample.pdf", sample_invoice_pdf, "application/pdf")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["invoiceno"]["value"] == "2026-0014"
    assert body["vendor"]["name"]["value"]
    assert body["meta"]["fields_found"] == 4


def test_api_rejects_non_pdf():
    client = TestClient(app)
    response = client.post(
        "/extract",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 415


def test_health():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
