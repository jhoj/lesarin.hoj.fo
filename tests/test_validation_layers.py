"""M5 — invoice likelihood, e-invoice structural checks, and duplicate/fraud
detection (layers 2, 3, 4; layer 1 is covered by tests/test_validation.py)."""

from __future__ import annotations

import io
import json

import pytest
from fastapi.testclient import TestClient
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from app import validation
from app.db import Base, SessionLocal, engine, init_db
from app.main import app
from app.validation.structural import vtal_checksum_ok


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    init_db()
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _invoice_with_vtal(vtal="314188") -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4)
    s = getSampleStyleSheet()
    doc.build([
        Paragraph("Effo P/F", s["Title"]),
        Paragraph(f"Vtal: {vtal}", s["Normal"]),
        Spacer(1, 6),
        Paragraph("Fakturanr: 2026-0014", s["Normal"]),
        Paragraph("Fakturadato: 12-01-2026", s["Normal"]),
        Paragraph("Forfaldsdato: 26-01-2026", s["Normal"]),
    ])
    return buf.getvalue()


def _register(client, email="a@b.com", password="password1"):
    r = client.post("/api/auth/register", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


# --- V-tal checksum (structural, layer 1 addition) --------------------------

def test_vtal_checksum_only_applies_to_8_digits():
    assert vtal_checksum_ok("314188") is None  # 6 digits: not applicable
    assert vtal_checksum_ok("31418812") in (True, False)  # 8 digits: a real verdict


# --- Layer 2: invoice likelihood ---------------------------------------------

def test_invoice_likelihood_scores_a_real_invoice_highly():
    from app.extraction import loader

    pdf = _invoice_with_vtal()
    document = loader.load(pdf)
    result = validation.invoice_likelihood.score(
        document, {"InvoiceNo": "2026-0014", "InvoiceDate": "2026-01-12",
                   "VendorName": "Effo P/F", "VendorNo": "314188"},
        has_line_items=False,
    )
    assert result["score"] > 0.5
    assert result["signals"]


def test_invoice_likelihood_scores_an_empty_read_low():
    from app.extraction import loader

    pdf = _invoice_with_vtal()
    document = loader.load(pdf)
    result = validation.invoice_likelihood.score(document, {}, has_line_items=False)
    # No fields located and no line table — even with invoice-ish keywords on
    # the page, the score stays well below a real, fully-located read.
    assert result["score"] < 0.4


# --- Layer 3: e-invoice structural checks -------------------------------------

def test_ubl_structural_validation_catches_missing_elements():
    incomplete = b"<Invoice xmlns='urn:oasis:names:specification:ubl:schema:xsd:Invoice-2'/>"
    result = validation.einvoice.validate_ubl(incomplete)
    assert result["valid"] is False
    assert any("cbc:ID" in p for p in result["problems"])


def test_export_ubl_passes_structural_einvoice_check(client, sample_invoice_pdf):
    h = _register(client)
    # Needs a line-item table to satisfy cac:InvoiceLine — the vendor-less
    # minimal fixture used elsewhere in this file has none.
    r = client.post(
        "/api/me/export?fmt=ubl", headers=h,
        files={"file": ("inv.pdf", sample_invoice_pdf, "application/pdf")},
    )
    assert r.status_code == 200, r.text
    assert r.headers["X-Lesarin-Einvoice-Valid"] == "true"
    assert "X-Lesarin-Likelihood" in r.headers


def test_embedded_einvoice_and_signature_are_heuristically_detected():
    plain = b"%PDF-1.4 no markers here"
    assert validation.einvoice.detect_embedded_einvoice(plain)["embedded"] is False
    assert validation.einvoice.detect_pdf_signature(plain) is False

    carrying_factur_x = plain + b" factur-x.xml "
    assert validation.einvoice.detect_embedded_einvoice(carrying_factur_x)["embedded"] is True

    signed = plain + b" /ByteRange [0 100 200 300] "
    assert validation.einvoice.detect_pdf_signature(signed) is True


# --- Layer 4: duplicate / fraud ------------------------------------------------

def test_duplicate_export_is_flagged_on_second_upload(client):
    h = _register(client)
    pdf = _invoice_with_vtal()
    r1 = client.post("/api/me/export", headers=h, files={"file": ("inv.pdf", pdf, "application/pdf")})
    assert r1.status_code == 200
    assert r1.headers["X-Lesarin-Duplicate"] == "false"

    r2 = client.post("/api/me/export", headers=h, files={"file": ("inv.pdf", pdf, "application/pdf")})
    assert r2.status_code == 200
    assert r2.headers["X-Lesarin-Duplicate"] == "true"


def test_duplicate_check_is_per_user(client):
    pdf = _invoice_with_vtal()
    h1 = _register(client, "one@x.com")
    h2 = _register(client, "two@x.com")
    client.post("/api/me/export", headers=h1, files={"file": ("inv.pdf", pdf, "application/pdf")})
    r2 = client.post("/api/me/export", headers=h2, files={"file": ("inv.pdf", pdf, "application/pdf")})
    assert r2.headers["X-Lesarin-Duplicate"] == "false"  # different tenant, not a duplicate


def test_duplicate_check_is_a_noop_without_identity():
    with SessionLocal() as session:
        assert validation.duplicate.check(session, 1, {}) == {"checked": False}
        assert validation.duplicate.check(session, 1, {"VendorNo": "314188"}) == {"checked": False}


def test_duplicate_check_matches_by_vendor_identifier_or_name():
    from app import auth
    from app.db_models import ExportRecord

    with SessionLocal() as session:
        user = auth.create_user(session, "amt@x.com", "password12")
        session.add(ExportRecord(
            user_id=user.id, vendor_identifier="314188", vendor_name="Effo P/F",
            invoice_no="2026-0014", source="heuristic",
        ))
        session.commit()

        by_identifier = validation.duplicate.check(
            session, user.id, {"VendorNo": "314188", "InvoiceNo": "2026-0014"}
        )
        assert by_identifier == {
            "checked": True, "duplicate": True,
            "detail": by_identifier["detail"],  # timestamp varies; shape is what matters
        }
        assert "already exported" in by_identifier["detail"]

        different_invoice = validation.duplicate.check(
            session, user.id, {"VendorNo": "314188", "InvoiceNo": "2026-0099"}
        )
        assert different_invoice["duplicate"] is False
