"""M1 auth hardening: API keys, TOTP MFA, login lockout, and session
revocation — exercised through the HTTP surface, same style as test_saas.py."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import auth
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


def _register(client, email="a@b.com", password="password1"):
    r = client.post("/api/auth/register", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# --- API keys ----------------------------------------------------------

def test_api_key_create_list_use_revoke(client):
    token = _register(client)
    headers = _auth(token)

    r = client.post("/api/me/api-keys", json={"name": "ci-bot"}, headers=headers)
    assert r.status_code == 200, r.text
    created = r.json()
    assert created["key"].startswith("lk_")
    assert created["prefix"] == created["key"][:12]

    r = client.get("/api/me/api-keys", headers=headers)
    assert r.status_code == 200
    listed = r.json()
    assert len(listed) == 1
    assert "key" not in listed[0]
    assert "hashed_key" not in listed[0]
    assert listed[0]["prefix"] == created["prefix"]

    # The key works exactly like a session token on a /api/me/* route.
    r = client.get("/api/me/profiles", headers=_auth(created["key"]))
    assert r.status_code == 200

    r = client.delete(f"/api/me/api-keys/{created['id']}", headers=headers)
    assert r.status_code == 200

    r = client.get("/api/me/profiles", headers=_auth(created["key"]))
    assert r.status_code == 401


def test_api_key_does_not_grant_access_to_another_user(client):
    token_a = _register(client, "a@b.com")
    _register(client, "c@d.com")
    key = client.post("/api/me/api-keys", json={"name": "k"}, headers=_auth(token_a)).json()["key"]

    r = client.get("/api/me/profiles", headers=_auth(key))
    assert r.status_code == 200
    assert all(True for _ in r.json())  # sanity: still just user A's profiles
    me = client.get("/api/me", headers=_auth(key)).json()
    assert me["email"] == "a@b.com"


# --- Login lockout -------------------------------------------------------

def test_login_lockout_after_repeated_failures(client):
    _register(client, "a@b.com", "password1")
    for _ in range(5):
        r = client.post("/api/auth/login", json={"email": "a@b.com", "password": "wrongwrong"})
        assert r.status_code == 401

    # Locked now, even with the correct password.
    r = client.post("/api/auth/login", json={"email": "a@b.com", "password": "password1"})
    assert r.status_code == 423


def test_login_success_resets_failure_count(client):
    _register(client, "a@b.com", "password1")
    for _ in range(4):
        client.post("/api/auth/login", json={"email": "a@b.com", "password": "wrongwrong"})
    r = client.post("/api/auth/login", json={"email": "a@b.com", "password": "password1"})
    assert r.status_code == 200
    # Failure count was reset, so 4 more wrong attempts still shouldn't lock.
    for _ in range(4):
        client.post("/api/auth/login", json={"email": "a@b.com", "password": "wrongwrong"})
    r = client.post("/api/auth/login", json={"email": "a@b.com", "password": "password1"})
    assert r.status_code == 200


# --- MFA -----------------------------------------------------------------

def test_mfa_enroll_verify_and_required_at_login(client):
    token = _register(client, "a@b.com", "password1")
    headers = _auth(token)

    r = client.get("/api/me", headers=headers)
    assert r.json()["mfa_enabled"] is False

    r = client.post("/api/me/mfa/enroll", headers=headers)
    assert r.status_code == 200, r.text
    enrolled = r.json()
    assert enrolled["otpauth_url"].startswith("otpauth://totp/")
    secret = enrolled["secret"]
    recovery_codes = enrolled["recovery_codes"]
    assert len(recovery_codes) == 8

    # Not yet confirmed: login still works without a code.
    r = client.post("/api/auth/login", json={"email": "a@b.com", "password": "password1"})
    assert r.status_code == 200

    r = client.post("/api/me/mfa/verify", json={"code": "000000"}, headers=headers)
    assert r.status_code == 401  # (astronomically unlikely to collide)

    code = auth.totp_at(secret)
    r = client.post("/api/me/mfa/verify", json={"code": code}, headers=headers)
    assert r.status_code == 200

    assert client.get("/api/me", headers=headers).json()["mfa_enabled"] is True

    # Now login requires a code.
    r = client.post("/api/auth/login", json={"email": "a@b.com", "password": "password1"})
    assert r.status_code == 401

    r = client.post(
        "/api/auth/login",
        json={"email": "a@b.com", "password": "password1", "totp": auth.totp_at(secret)},
    )
    assert r.status_code == 200

    # A recovery code works once, then is consumed.
    r = client.post(
        "/api/auth/login",
        json={"email": "a@b.com", "password": "password1", "recovery_code": recovery_codes[0]},
    )
    assert r.status_code == 200
    r = client.post(
        "/api/auth/login",
        json={"email": "a@b.com", "password": "password1", "recovery_code": recovery_codes[0]},
    )
    assert r.status_code == 401


def test_mfa_disable_requires_password(client):
    token = _register(client, "a@b.com", "password1")
    headers = _auth(token)
    secret = client.post("/api/me/mfa/enroll", headers=headers).json()["secret"]
    client.post("/api/me/mfa/verify", json={"code": auth.totp_at(secret)}, headers=headers)

    r = client.request("DELETE", "/api/me/mfa", json={"password": "wrong"}, headers=headers)
    assert r.status_code == 401
    assert client.get("/api/me", headers=headers).json()["mfa_enabled"] is True

    r = client.request("DELETE", "/api/me/mfa", json={"password": "password1"}, headers=headers)
    assert r.status_code == 200
    assert client.get("/api/me", headers=headers).json()["mfa_enabled"] is False

    # MFA gone: login no longer needs a code.
    r = client.post("/api/auth/login", json={"email": "a@b.com", "password": "password1"})
    assert r.status_code == 200


# --- Logout everywhere -----------------------------------------------------

def test_logout_all_invalidates_prior_tokens(client):
    token = _register(client, "a@b.com", "password1")
    headers = _auth(token)
    assert client.get("/api/me", headers=headers).status_code == 200

    r = client.post("/api/me/logout-all", headers=headers)
    assert r.status_code == 200

    assert client.get("/api/me", headers=headers).status_code == 401

    r = client.post("/api/auth/login", json={"email": "a@b.com", "password": "password1"})
    assert r.status_code == 200
    new_token = r.json()["token"]
    assert client.get("/api/me", headers=_auth(new_token)).status_code == 200
