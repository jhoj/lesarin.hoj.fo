"""Authentication — accounts, password hashing, and bearer tokens.

Deliberately dependency-free: password hashing uses :func:`hashlib.pbkdf2_hmac`
and tokens are HMAC-signed with the stdlib, so there's no native-build or
crypto-package friction in any environment. Tokens are stateless (a signed
``user_id`` + issued/expiry timestamps), so there's no session table to evict.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from typing import Optional

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import DB_PATH, get_session
from .db_models import User

# --- Secret used to sign tokens -------------------------------------------

_PBKDF2_ITERATIONS = 200_000
_TOKEN_TTL_SECONDS = 30 * 24 * 3600  # 30 days


def _load_secret() -> bytes:
    """Signing secret: env override, else a persisted random file beside the DB.

    Persisting it means tokens survive a restart without forcing every customer
    to log in again; generating it means a fresh deployment is secure by default.
    """
    env = os.environ.get("LESARIN_SECRET")
    if env:
        return env.encode("utf-8")
    secret_file = Path(DB_PATH).parent / ".lesarin-secret"
    try:
        if secret_file.exists():
            return secret_file.read_bytes()
        secret_file.parent.mkdir(parents=True, exist_ok=True)
        value = secrets.token_bytes(32)
        secret_file.write_bytes(value)
        return value
    except OSError:
        # Read-only FS (e.g. some test sandboxes): fall back to a process secret.
        return secrets.token_bytes(32)


_SECRET = _load_secret()


# --- Password hashing ------------------------------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, digest_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iters)
        )
        return hmac.compare_digest(expected, actual)
    except (ValueError, AttributeError):
        return False


# --- Tokens ----------------------------------------------------------------

def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def make_token(user_id: int) -> str:
    payload = {"uid": user_id, "iat": int(time.time())}
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = _b64e(hmac.new(_SECRET, body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{sig}"


def parse_token(token: str) -> Optional[int]:
    """Return the user_id if the token is well-formed, unexpired, and signed."""
    try:
        body, sig = token.split(".")
    except ValueError:
        return None
    expected = _b64e(hmac.new(_SECRET, body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(_b64d(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if int(time.time()) - int(payload.get("iat", 0)) > _TOKEN_TTL_SECONDS:
        return None
    uid = payload.get("uid")
    return int(uid) if isinstance(uid, int) else None


# --- User repository -------------------------------------------------------

def get_user_by_email(session: Session, email: str) -> Optional[User]:
    return session.scalar(select(User).where(User.email == email.strip().lower()))


def create_user(session: Session, email: str, password: str) -> User:
    user = User(email=email.strip().lower(), password_hash=hash_password(password))
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def authenticate(session: Session, email: str, password: str) -> Optional[User]:
    user = get_user_by_email(session, email)
    if user is None or not verify_password(password, user.password_hash):
        return None
    return user


# --- FastAPI dependencies --------------------------------------------------

def _bearer(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def current_user(
    authorization: Optional[str] = Header(default=None),
    session: Session = Depends(get_session),
) -> User:
    token = _bearer(authorization)
    uid = parse_token(token) if token else None
    user = session.get(User, uid) if uid else None
    if user is None:
        raise HTTPException(401, "Not authenticated.")
    return user


def optional_user(
    authorization: Optional[str] = Header(default=None),
    session: Session = Depends(get_session),
) -> Optional[User]:
    token = _bearer(authorization)
    uid = parse_token(token) if token else None
    return session.get(User, uid) if uid else None
