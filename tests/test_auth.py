"""Auth primitives: password hashing and stateless bearer tokens."""

from __future__ import annotations

import base64
import hashlib
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
    token = auth.make_token(42, token_version=3)
    assert auth.parse_token(token) == (42, 3)
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


# --- TOTP --------------------------------------------------------------

def test_totp_rfc6238_vector():
    # RFC 6238 Appendix B test vector (SHA-1, 8-digit truncated to our 6).
    # Secret "12345678901234567890" base32-encoded, at T=59s -> counter 1.
    secret = base64.b32encode(b"12345678901234567890").decode("ascii").rstrip("=")
    code = auth.totp_at(secret, for_time=59)
    assert code == "287082"  # last 6 digits of the RFC's 94287082


def test_totp_rejects_wrong_code():
    secret = auth.generate_totp_secret()
    valid = auth.totp_at(secret)
    wrong = str((int(valid) + 1) % 10**6).zfill(6)
    assert auth.verify_totp(secret, valid, window=0)
    assert not auth.verify_totp(secret, wrong, window=0)
    assert not auth.verify_totp(secret, "not-a-code")


def test_totp_window_tolerates_clock_skew():
    secret = auth.generate_totp_secret()
    now = time.time()
    one_step_ago = auth.totp_at(secret, now - 30)
    assert auth.verify_totp(secret, one_step_ago, window=1)
    far_off = auth.totp_at(secret, now - 300)
    assert not auth.verify_totp(secret, far_off, window=1)


# --- Recovery codes ------------------------------------------------------

def test_recovery_codes_are_one_time():
    from app.db_models import MfaCredential

    codes = auth.generate_recovery_codes(3)
    assert len(codes) == 3
    mfa = MfaCredential(user_id=1, secret="x", recovery_codes=[auth.hash_recovery_code(c) for c in codes])
    assert auth.consume_recovery_code(mfa, codes[0])
    assert not auth.consume_recovery_code(mfa, codes[0])  # already used
    assert auth.consume_recovery_code(mfa, codes[1])


# --- API keys ------------------------------------------------------------

def test_api_key_generation_and_hash():
    full, prefix, hashed = auth.generate_api_key()
    assert full.startswith("lk_")
    assert full.startswith(prefix)
    assert hashed == hashlib.sha256(full.encode("utf-8")).hexdigest()
    assert hashed != full
