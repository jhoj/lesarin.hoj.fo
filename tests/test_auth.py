"""Auth primitives: password hashing and stateless bearer tokens."""

from __future__ import annotations

import time

from app import auth


def test_password_hash_roundtrip():
    stored = auth.hash_password("correct horse battery")
    assert stored.startswith("pbkdf2_sha256$")
    assert auth.verify_password("correct horse battery", stored)
    assert not auth.verify_password("wrong", stored)


def test_password_hash_is_salted():
    a = auth.hash_password("same")
    b = auth.hash_password("same")
    assert a != b  # random salt per hash
    assert auth.verify_password("same", a) and auth.verify_password("same", b)


def test_token_roundtrip_and_tamper():
    token = auth.make_token(42)
    assert auth.parse_token(token) == 42
    body, sig = token.split(".")
    assert auth.parse_token(f"{body}.{sig}x") is None  # bad signature
    assert auth.parse_token("garbage") is None
    assert auth.parse_token(f"{body}xyz.{sig}") is None  # tampered body


def test_token_expires(monkeypatch):
    token = auth.make_token(7)
    # Jump past the TTL and the token must stop validating.
    monkeypatch.setattr(time, "time", lambda: 10**12)
    assert auth.parse_token(token) is None


def test_verify_rejects_malformed_stored():
    assert not auth.verify_password("x", "not-a-valid-hash")
    assert not auth.verify_password("x", "")
