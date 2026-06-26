"""End-to-end SaaS flow: register → profile → upload → export, plus the
central "map once, everyone benefits" auto-learning."""

from __future__ import annotations

import io
import json

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
    init_db()  # seeds the canonical output-field vocabulary
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _invoice_with_vtal() -> bytes:
    """A digital invoice that announces a V-tal, so the vendor is identifiable."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=20 * mm)
    s = getSampleStyleSheet()
    story = [
        Paragraph("Effo P/F", s["Title"]),
        Paragraph("Vtal: 314188", s["Normal"]),
        Spacer(1, 6 * mm),
        Paragraph("Fakturanr: 2026-0014", s["Normal"]),
        Paragraph("Fakturadato: 12-01-2026", s["Normal"]),
        Paragraph("Forfaldsdato: 26-01-2026", s["Normal"]),
    ]
    doc.build(story)
    return buf.getvalue()


def _register(client, email="a@b.com", password="password1"):
    r = client.post("/api/auth/register", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


# --- Auth ------------------------------------------------------------------

def test_register_login_me(client):
    h = _register(client)
    assert client.get("/api/me", headers=h).json()["email"] == "a@b.com"
    # Duplicate registration is rejected.
    assert client.post("/api/auth/register", json={"email": "a@b.com", "password": "password1"}).status_code == 409
    # Login returns a working token.
    tok = client.post("/api/auth/login", json={"email": "a@b.com", "password": "password1"}).json()["token"]
    assert client.get("/api/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 200
    # Wrong password rejected.
    assert client.post("/api/auth/login", json={"email": "a@b.com", "password": "nope12345"}).status_code == 401


def test_me_requires_auth(client):
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/me/profiles").status_code == 401


def test_short_password_rejected(client):
    assert client.post("/api/auth/register", json={"email": "x@y.com", "password": "short"}).status_code == 422


# --- Profiles --------------------------------------------------------------

def test_register_creates_default_profile(client):
    h = _register(client)
    profiles = client.get("/api/me/profiles", headers=h).json()
    assert len(profiles) == 1
    assert profiles[0]["is_default"] and profiles[0]["fmt"] == "json"


def test_profile_crud_and_single_default(client):
    h = _register(client)
    # New default profile must demote the previous default.
    created = client.post("/api/me/profiles", headers=h, json={
        "name": "API JSON", "fmt": "json", "is_default": True,
        "fields": [{"canonical": "InvoiceNo", "output_name": "invoice_id"}],
    }).json()
    profiles = client.get("/api/me/profiles", headers=h).json()
    defaults = [p for p in profiles if p["is_default"]]
    assert len(defaults) == 1 and defaults[0]["id"] == created["id"]

    # Unknown canonical field is rejected.
    assert client.post("/api/me/profiles", headers=h, json={
        "name": "bad", "fields": [{"canonical": "Nonsense", "output_name": "x"}],
    }).status_code == 422

    # Delete.
    assert client.delete(f"/api/me/profiles/{created['id']}", headers=h).status_code == 200


def test_profiles_are_isolated_per_user(client):
    h1 = _register(client, "one@x.com")
    h2 = _register(client, "two@x.com")
    pid = client.get("/api/me/profiles", headers=h1).json()[0]["id"]
    # User two cannot see or edit user one's profile.
    assert client.put(f"/api/me/profiles/{pid}", headers=h2, json={"name": "hax", "fields": []}).status_code in (404, 422)
    assert client.delete(f"/api/me/profiles/{pid}", headers=h2).status_code == 404


# --- Export ----------------------------------------------------------------

def test_export_json_honours_custom_names(client):
    h = _register(client)
    client.post("/api/me/profiles", headers=h, json={
        "name": "API", "fmt": "json", "is_default": True,
        "fields": [
            {"canonical": "InvoiceNo", "output_name": "invoice_id"},
            {"canonical": "DueDate", "output_name": "pay_by"},
        ],
    })
    r = client.post("/api/me/export", headers=h,
                    files={"file": ("inv.pdf", _invoice_with_vtal(), "application/pdf")})
    assert r.status_code == 200, r.text
    body = json.loads(r.text)
    assert body["invoice_id"] == "2026-0014"
    assert body["pay_by"] == "2026-01-26"
    assert "lines" in body


def test_export_format_override(client):
    h = _register(client)
    r = client.post("/api/me/export?fmt=oioubl", headers=h,
                    files={"file": ("inv.pdf", _invoice_with_vtal(), "application/pdf")})
    assert r.status_code == 200
    assert "OIOUBL" in r.text and r.headers["content-type"].startswith("application/xml")


# --- The central magic: map once, everyone benefits ------------------------

def test_first_upload_learns_vendor_centrally(client):
    h = _register(client)
    assert client.get("/api/vendors").json() == []  # nothing taught yet
    client.post("/api/me/export", headers=h,
                files={"file": ("inv.pdf", _invoice_with_vtal(), "application/pdf")})
    vendors = client.get("/api/vendors").json()
    assert len(vendors) == 1
    assert vendors[0]["identifier"] == "314188"
    assert vendors[0]["mappings"]  # a template was stored centrally


def test_second_user_benefits_from_first_users_mapping(client):
    pdf = _invoice_with_vtal()
    # User one uploads first → vendor learned centrally.
    h1 = _register(client, "first@x.com")
    client.post("/api/me/export", headers=h1, files={"file": ("inv.pdf", pdf, "application/pdf")})
    learned = client.get("/api/vendors").json()
    assert len(learned) == 1

    # User two uploads the same vendor → no new vendor is created (the central
    # template is detected and applied), and they still get clean output.
    h2 = _register(client, "second@x.com")
    r = client.post("/api/me/export", headers=h2,
                    files={"file": ("inv.pdf", pdf, "application/pdf")})
    assert r.status_code == 200
    assert json.loads(r.text)["InvoiceNo"] == "2026-0014"
    assert len(client.get("/api/vendors").json()) == 1  # still just the one


def test_saas_ui_is_served(client):
    r = client.get("/app/")
    assert r.status_code == 200 and "Lesarin" in r.text
