"""Who may touch the shared vendor knowledge.

Vendor templates are global: one edit changes what every customer's extraction
returns. The Angular app hid the studio behind a route guard, but the API
itself accepted anyone who could reach the host, with no account at all.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.db import Base, SessionLocal, engine, init_db
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


def _token(client, email, staff=False):
    token = client.post(
        "/api/auth/register", json={"email": email, "password": "password1"}
    ).json()["token"]
    if staff:
        with SessionLocal() as session:
            auth.set_staff(session, email)
    return {"Authorization": f"Bearer {token}"}


# Every studio route, as (method, path). Reads included: the vendor list is a
# map of who this system's customers buy from, which is not public either.
STUDIO_ROUTES = [
    ("get", "/api/vendors"),
    ("get", "/api/vendors/1"),
    ("post", "/api/vendors"),
    ("put", "/api/vendors/1"),
    ("delete", "/api/vendors/1"),
    ("get", "/api/output-fields"),
    ("post", "/api/output-fields"),
    ("delete", "/api/output-fields/InvoiceNo"),
    ("post", "/api/documents"),
    ("get", "/api/documents/abc/file"),
    ("post", "/api/documents/abc/read"),
    ("get", "/api/documents/abc/suggest-fields"),
]


@pytest.mark.parametrize("method,path", STUDIO_ROUTES)
def test_anonymous_callers_are_refused(client, method, path):
    assert getattr(client, method)(path).status_code == 401


@pytest.mark.parametrize("method,path", STUDIO_ROUTES)
def test_customers_are_refused(client, method, path):
    headers = _token(client, "customer@firm.fo")
    response = getattr(client, method)(path, headers=headers)
    # 403, not 404: the endpoint isn't a secret, it's just not theirs.
    assert response.status_code == 403
    assert "staff" in response.json()["detail"].lower()


def test_staff_are_let_through(client):
    headers = _token(client, "staff@lesarin.fo", staff=True)
    assert client.get("/api/vendors", headers=headers).status_code == 200
    assert client.get("/api/output-fields", headers=headers).status_code == 200


def test_headless_extract_needs_an_account_but_not_staff(client):
    """/api/extract applies saved templates for production callers — any
    signed-in account may use it; it just may no longer be anonymous."""
    assert client.post("/api/extract").status_code == 401

    headers = _token(client, "customer@firm.fo")
    # 422 for the missing file, i.e. it got past the auth check.
    assert client.post("/api/extract", headers=headers).status_code == 422


def test_promotion_is_not_self_service(client):
    """There must be no way to grant yourself staff over HTTP."""
    headers = _token(client, "customer@firm.fo")
    for path in ("/api/me", "/api/me/staff", "/api/staff"):
        for method in ("post", "put", "patch"):
            status = getattr(client, method)(path, headers=headers, json={"is_staff": True}).status_code
            assert status in (401, 403, 404, 405), f"{method} {path} unexpectedly returned {status}"

    with SessionLocal() as session:
        assert auth.get_user_by_email(session, "customer@firm.fo").is_staff is False
