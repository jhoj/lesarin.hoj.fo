"""Logging: the diagnostic line an export leaves behind, and what must never
appear in it."""

from __future__ import annotations

import io
import logging

import pytest
from fastapi.testclient import TestClient
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

from app import repo
from app.db import Base, SessionLocal, engine, init_db
from app.logging_setup import configure_logging
from app.main import app


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


def _invoice_pdf() -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=20 * mm)
    s = getSampleStyleSheet()
    doc.build([
        Paragraph("Effo P/F", s["Title"]),
        Paragraph("Vtal: 314188", s["Normal"]),
        Spacer(1, 6 * mm),
        Paragraph("Fakturanr: 2026-0014", s["Normal"]),
        Paragraph("Fakturadato: 12-01-2026", s["Normal"]),
    ])
    return buf.getvalue()


def test_configure_logging_is_idempotent():
    configure_logging()
    first = len(logging.getLogger().handlers)
    configure_logging()
    assert len(logging.getLogger().handlers) == first  # not stacked


def test_export_logs_a_diagnostic_line(client, caplog):
    password = "password1234"
    r = client.post("/api/auth/register", json={"email": "log@test.com", "password": password})
    token = r.json()["token"]

    with caplog.at_level(logging.INFO, logger="lesarin.saas"):
        r = client.post(
            "/api/me/export",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("invoice.pdf", _invoice_pdf(), "application/pdf")},
        )
    assert r.status_code == 200

    line = next((m for m in caplog.messages if m.startswith("export ")), None)
    assert line is not None, caplog.messages
    for expected in ("user=", "source=", "located=", "fmt=", "valid=", "ms="):
        assert expected in line

    # Metadata only — never the customer's credentials or their document.
    assert password not in line
    assert token not in line


def test_vendor_template_changes_are_logged(caplog):
    with caplog.at_level(logging.INFO, logger="lesarin.repo"), SessionLocal() as session:
        vendor = repo.create_vendor(
            session,
            identifier="314188",
            name="Effo",
            mappings=[{"output": "InvoiceNo", "strategy": "label", "label": "Fakturanr"}],
        )
        repo.update_vendor(session, vendor.id, name="Effo P/F")
        repo.delete_vendor(session, vendor.id)

    joined = "\n".join(caplog.messages)
    assert "vendor created" in joined
    assert "vendor updated" in joined
    assert "vendor deleted" in joined  # destructive to shared data — logged as a warning
    assert any(r.levelno == logging.WARNING for r in caplog.records)
