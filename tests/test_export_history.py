"""Every export leaves a record: what came in, how it was read, how it went."""

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


def _invoice() -> bytes:
    buf = io.BytesIO()
    s = getSampleStyleSheet()
    SimpleDocTemplate(buf, pagesize=A4, topMargin=20 * mm).build([
        Paragraph("Effo P/F", s["Title"]),
        Paragraph("Vtal: 314188", s["Normal"]),
        Spacer(1, 6 * mm),
        Paragraph("Fakturanr: 2026-0014", s["Normal"]),
        Paragraph("Fakturadato: 12-01-2026", s["Normal"]),
    ])
    return buf.getvalue()


def _register(client, email="a@b.com"):
    token = client.post(
        "/api/auth/register", json={"email": email, "password": "password1"}
    ).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _export(client, headers, filename="faktura.pdf"):
    return client.post(
        "/api/me/export",
        headers=headers,
        files={"file": (filename, _invoice(), "application/pdf")},
    )


def test_an_export_is_recorded(client):
    headers = _register(client)
    assert _export(client, headers).status_code == 200

    records = client.get("/api/me/exports", headers=headers).json()
    assert len(records) == 1
    r = records[0]
    assert r["filename"] == "faktura.pdf"
    assert r["fmt"] == "json"
    assert r["source"] in ("template", "heuristic")
    assert r["invoice_no"] == "2026-0014"
    assert r["requested"] > 0
    assert r["located"] + len(r["missing"]) == r["requested"]
    assert r["duration_ms"] >= 0
    assert r["created_at"]


def test_history_is_newest_first_and_pageable(client):
    headers = _register(client)
    for name in ("first.pdf", "second.pdf", "third.pdf"):
        assert _export(client, headers, name).status_code == 200

    records = client.get("/api/me/exports", headers=headers).json()
    assert [r["filename"] for r in records] == ["third.pdf", "second.pdf", "first.pdf"]

    page = client.get("/api/me/exports?limit=2", headers=headers).json()
    assert [r["filename"] for r in page] == ["third.pdf", "second.pdf"]
    rest = client.get("/api/me/exports?limit=2&offset=2", headers=headers).json()
    assert [r["filename"] for r in rest] == ["first.pdf"]


def test_history_is_private_to_the_account(client):
    mine = _register(client, "mine@firm.fo")
    theirs = _register(client, "theirs@firm.fo")
    assert _export(client, mine, "mine.pdf").status_code == 200

    assert len(client.get("/api/me/exports", headers=mine).json()) == 1
    assert client.get("/api/me/exports", headers=theirs).json() == []


def test_history_needs_authentication(client):
    assert client.get("/api/me/exports").status_code == 401


def test_a_failed_record_never_fails_the_export(client, monkeypatch):
    """The rendered data is already in hand by the time the row is written —
    losing the row must not turn a good read into a 500 for the customer."""
    from app import saas

    def unwritable(*_args, **_kwargs):
        raise RuntimeError("history table is unhappy")

    monkeypatch.setattr(saas, "ExportRecord", unwritable)

    headers = _register(client)
    response = _export(client, headers)

    assert response.status_code == 200
    assert response.headers["X-Lesarin-Source"]  # the read itself is unaffected
