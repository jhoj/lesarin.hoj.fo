"""The upload endpoint is the expensive one — it must not be monopolisable."""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph

from app import saas
from app.db import Base, engine, init_db
from app.main import app
from app.rate_limit import RateLimiter


# --- The limiter itself ----------------------------------------------------

def test_bucket_allows_capacity_then_refuses():
    limiter = RateLimiter(per_minute=3)
    assert [limiter.take("a") for _ in range(3)] == [None, None, None]
    retry_after = limiter.take("a")
    assert retry_after is not None and retry_after > 0


def test_keys_are_independent():
    limiter = RateLimiter(per_minute=1)
    assert limiter.take("a") is None
    assert limiter.take("b") is None  # b has its own allowance
    assert limiter.take("a") is not None


def test_tokens_refill_over_time(monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr("app.rate_limit.time.monotonic", lambda: clock["now"])

    limiter = RateLimiter(per_minute=60)  # one token per second
    for _ in range(60):
        assert limiter.take("a") is None
    assert limiter.take("a") is not None

    clock["now"] += 2.0  # two seconds buys two more
    assert limiter.take("a") is None
    assert limiter.take("a") is None
    assert limiter.take("a") is not None


# --- Wired into the export endpoint ----------------------------------------

@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    init_db()
    saas.export_limiter.reset()
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _pdf() -> bytes:
    buf = io.BytesIO()
    s = getSampleStyleSheet()
    SimpleDocTemplate(buf, pagesize=A4, topMargin=20 * mm).build(
        [Paragraph("Fakturanr: 2026-0014", s["Normal"])]
    )
    return buf.getvalue()


def _register(client, email):
    return client.post(
        "/api/auth/register", json={"email": email, "password": "password1"}
    ).json()["token"]


def test_export_is_rate_limited_per_account(client, monkeypatch):
    monkeypatch.setattr(saas, "export_limiter", RateLimiter(per_minute=2))
    monkeypatch.setattr(saas, "_EXPORT_RATE_PER_MINUTE", 2)

    token = _register(client, "rate@test.com")
    other = _register(client, "other@test.com")
    pdf = _pdf()

    def export(tok):
        return client.post(
            "/api/me/export",
            headers={"Authorization": f"Bearer {tok}"},
            files={"file": ("i.pdf", pdf, "application/pdf")},
        )

    assert export(token).status_code == 200
    assert export(token).status_code == 200

    limited = export(token)
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) >= 1
    assert "per minute" in limited.json()["detail"]

    # One account's limit must not spend another's allowance.
    assert export(other).status_code == 200
