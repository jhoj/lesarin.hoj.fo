"""Forgotten passwords.

Before this there was no recovery at all: a customer who lost their password
was locked out permanently, with no admin path either.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app import auth, mailer
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


@pytest.fixture()
def outbox(monkeypatch):
    """Capture what would have been emailed."""
    sent = []

    def fake_send(to, subject, body):
        sent.append({"to": to, "subject": subject, "body": body})
        return True

    monkeypatch.setattr(mailer, "send", fake_send)
    return sent


def _register(client, email="a@b.com", password="password1"):
    return client.post("/api/auth/register", json={"email": email, "password": password})


def _link_token(message) -> str:
    match = re.search(r"reset-password\?token=([A-Za-z0-9_\-]+)", message["body"])
    assert match, message["body"]
    return match.group(1)


def test_a_forgotten_password_can_be_reset(client, outbox):
    _register(client, "a@b.com", "password1")

    assert client.post("/api/auth/forgot-password", json={"email": "a@b.com"}).status_code == 200
    assert len(outbox) == 1
    token = _link_token(outbox[0])

    r = client.post("/api/auth/reset-password", json={"token": token, "password": "brand-new-pw"})
    assert r.status_code == 200
    assert r.json()["email"] == "a@b.com"

    # The new password works and the old one doesn't.
    assert client.post("/api/auth/login", json={"email": "a@b.com", "password": "brand-new-pw"}).status_code == 200
    assert client.post("/api/auth/login", json={"email": "a@b.com", "password": "password1"}).status_code == 401


def test_a_reset_link_works_only_once(client, outbox):
    _register(client, "a@b.com")
    client.post("/api/auth/forgot-password", json={"email": "a@b.com"})
    token = _link_token(outbox[0])

    assert client.post("/api/auth/reset-password", json={"token": token, "password": "first-choice"}).status_code == 200
    second = client.post("/api/auth/reset-password", json={"token": token, "password": "second-try"})
    assert second.status_code == 400
    # And the first reset stands.
    assert client.post("/api/auth/login", json={"email": "a@b.com", "password": "first-choice"}).status_code == 200


def test_asking_again_invalidates_the_earlier_link(client, outbox):
    """An old message sitting in a mailbox must not stay live."""
    _register(client, "a@b.com")
    client.post("/api/auth/forgot-password", json={"email": "a@b.com"})
    client.post("/api/auth/forgot-password", json={"email": "a@b.com"})
    first, second = _link_token(outbox[0]), _link_token(outbox[1])

    assert client.post("/api/auth/reset-password", json={"token": first, "password": "nope-nope"}).status_code == 400
    assert client.post("/api/auth/reset-password", json={"token": second, "password": "yes-please"}).status_code == 200


def test_an_expired_link_is_refused(client, outbox, monkeypatch):
    _register(client, "a@b.com")
    monkeypatch.setattr(auth, "_RESET_TTL_MINUTES", -1)  # already past it
    client.post("/api/auth/forgot-password", json={"email": "a@b.com"})
    token = _link_token(outbox[0])

    assert client.post("/api/auth/reset-password", json={"token": token, "password": "too-late-now"}).status_code == 400


def test_an_unknown_address_is_answered_identically(client, outbox):
    """Otherwise this endpoint tells an attacker who has an account."""
    _register(client, "real@firm.fo")

    known = client.post("/api/auth/forgot-password", json={"email": "real@firm.fo"})
    unknown = client.post("/api/auth/forgot-password", json={"email": "nobody@nowhere.fo"})

    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()
    assert [m["to"] for m in outbox] == ["real@firm.fo"]  # but only one mail went out


def test_a_made_up_token_is_refused(client):
    assert client.post(
        "/api/auth/reset-password", json={"token": "not-a-real-token", "password": "password1"}
    ).status_code == 400


def test_only_the_hash_is_stored(client, outbox):
    """A leaked database must not hand out working reset links."""
    from app.db_models import PasswordResetToken

    _register(client, "a@b.com")
    client.post("/api/auth/forgot-password", json={"email": "a@b.com"})
    token = _link_token(outbox[0])

    with SessionLocal() as session:
        stored = session.query(PasswordResetToken).all()
        assert len(stored) == 1
        assert token not in stored[0].token_hash
        assert stored[0].token_hash == auth._hash_reset_token(token)


def test_the_reset_email_carries_a_usable_link(client, outbox):
    _register(client, "a@b.com")
    client.post("/api/auth/forgot-password", json={"email": "a@b.com"})

    body = outbox[0]["body"]
    assert "/reset-password?token=" in body
    assert "expires in an hour" in body
    assert outbox[0]["subject"] == "Reset your Lesarin password"
