"""The export response must say how the read went, not just hand back a file.

Empty fields are the normal outcome of a first-time supplier; the caller needs
to be told which ones so a human can check them.
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

from app.db import Base, engine, init_db
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


def _partial_invoice() -> bytes:
    """An invoice with an number and dates but no totals — so some canonical
    fields are genuinely missing and must be reported as such."""
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


def _export(client):
    token = client.post(
        "/api/auth/register", json={"email": "q@test.com", "password": "password1"}
    ).json()["token"]
    return client.post(
        "/api/me/export",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("invoice.pdf", _partial_invoice(), "application/pdf")},
    )


def test_export_reports_what_was_located(client):
    r = _export(client)
    assert r.status_code == 200

    located, total = r.headers["X-Lesarin-Located"].split("/")
    assert int(total) > 0
    assert 0 < int(located) <= int(total)

    # This invoice has no totals, so something must be reported missing, and
    # the counts have to agree with the list.
    missing = [m for m in r.headers["X-Lesarin-Missing"].split(",") if m]
    assert missing
    assert int(located) + len(missing) == int(total)
    assert "TotalInclVat" in missing


def test_export_reports_how_it_was_read(client):
    r = _export(client)
    # No template has been taught yet, so this is the best-effort path.
    assert r.headers["X-Lesarin-Source"] == "heuristic"
    assert r.headers["X-Lesarin-Valid"] in ("true", "false")
    assert r.headers["X-Lesarin-Problems"].isdigit()
