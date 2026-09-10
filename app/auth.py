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
import struct
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import DB_PATH, get_session
from .db_models import ApiKey, MfaCredential, User

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


def make_token(user_id: int, token_version: int = 0) -> str:
    payload = {"uid": user_id, "tv": token_version, "iat": int(time.time())}
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = _b64e(hmac.new(_SECRET, body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{sig}"


def parse_token(token: str) -> Optional[Tuple[int, int]]:
    """Return (user_id, token_version) if the token is well-formed, unexpired,
    and signed. Stays DB-free/pure — a caller checks token_version against the
    user's current one to honour "log out everywhere"."""
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
    if not isinstance(uid, int):
        return None
    tv = payload.get("tv", 0)
    return uid, int(tv) if isinstance(tv, int) else 0


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


# --- Login lockout -----------------------------------------------------

_MAX_LOGIN_FAILURES = 5
_LOCKOUT_MINUTES = 15


def is_locked(user: User) -> bool:
    if user.locked_until is None:
        return False
    # SQLite round-trips DateTime columns as naive (UTC, since that's all we
    # ever store) even though the value was tz-aware when it was written.
    locked_until = user.locked_until
    if locked_until.tzinfo is None:
        locked_until = locked_until.replace(tzinfo=timezone.utc)
    return locked_until > datetime.now(timezone.utc)


def record_login_failure(session: Session, user: User) -> None:
    """Count a failed attempt; lock the account for a cooldown after too many."""
    user.failed_login_count += 1
    if user.failed_login_count >= _MAX_LOGIN_FAILURES:
        user.locked_until = datetime.now(timezone.utc) + timedelta(minutes=_LOCKOUT_MINUTES)
        user.failed_login_count = 0
    session.commit()


def record_login_success(session: Session, user: User) -> None:
    user.failed_login_count = 0
    user.locked_until = None
    session.commit()


# --- TOTP (RFC 6238) --------------------------------------------------------
#
# Implemented on hashlib/hmac/struct rather than a dependency, matching this
# module's existing stdlib-only design.

_TOTP_STEP_SECONDS = 30
_TOTP_DIGITS = 6


def generate_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _b32decode(secret: str) -> bytes:
    padded = secret + "=" * (-len(secret) % 8)
    return base64.b32decode(padded.upper())


def totp_at(secret: str, for_time: Optional[float] = None) -> str:
    counter = int((for_time if for_time is not None else time.time()) // _TOTP_STEP_SECONDS)
    key = _b32decode(secret)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % 10**_TOTP_DIGITS
    return str(code).zfill(_TOTP_DIGITS)


def verify_totp(secret: str, code: str, window: int = 1) -> bool:
    code = (code or "").strip()
    if not code:
        return False
    now = time.time()
    for step in range(-window, window + 1):
        candidate = totp_at(secret, now + step * _TOTP_STEP_SECONDS)
        if hmac.compare_digest(candidate, code):
            return True
    return False


# --- Recovery codes ----------------------------------------------------

_RECOVERY_CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"  # no 0/O/1/I


def _one_recovery_code() -> str:
    chars = "".join(secrets.choice(_RECOVERY_CODE_ALPHABET) for _ in range(10))
    return f"{chars[:5]}-{chars[5:]}"


def generate_recovery_codes(n: int = 8) -> List[str]:
    return [_one_recovery_code() for _ in range(n)]


def hash_recovery_code(code: str) -> str:
    return hashlib.sha256(code.strip().upper().encode("utf-8")).hexdigest()


def consume_recovery_code(mfa: MfaCredential, code: str) -> bool:
    """Check the code against the stored hashes and remove it if it matches
    (one-time use). Mutates ``mfa.recovery_codes``; caller commits."""
    if not code:
        return False
    hashed = hash_recovery_code(code)
    hashes = list(mfa.recovery_codes or [])
    for stored in hashes:
        if hmac.compare_digest(stored, hashed):
            hashes.remove(stored)
            mfa.recovery_codes = hashes
            return True
    return False


# --- API keys ------------------------------------------------------------

_API_KEY_PREFIX = "lk_"


def generate_api_key() -> Tuple[str, str, str]:
    """Return (full_key, display_prefix, hashed_key)."""
    full = _API_KEY_PREFIX + secrets.token_urlsafe(18)
    prefix = full[:12]
    hashed = hashlib.sha256(full.encode("utf-8")).hexdigest()
    return full, prefix, hashed


def lookup_api_key(session: Session, key: str) -> Optional[ApiKey]:
    hashed = hashlib.sha256(key.encode("utf-8")).hexdigest()
    prefix = key[:12]
    for candidate in session.scalars(
        select(ApiKey).where(ApiKey.prefix == prefix, ApiKey.revoked_at.is_(None))
    ):
        if hmac.compare_digest(candidate.hashed_key, hashed):
            candidate.last_used_at = datetime.now(timezone.utc)
            session.commit()
            return candidate
    return None


# --- FastAPI dependencies --------------------------------------------------

def _bearer(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def api_key_or_user(
    authorization: Optional[str] = Header(default=None),
    session: Session = Depends(get_session),
) -> User:
    """Resolve the caller from either an API key (``lk_...``, for automation)
    or a session token (issued by /auth/login), so both work on every
    /api/me/* route."""
    token = _bearer(authorization)
    if token:
        if token.startswith(_API_KEY_PREFIX):
            api_key = lookup_api_key(session, token)
            if api_key is not None:
                return api_key.user
        else:
            parsed = parse_token(token)
            if parsed is not None:
                uid, tv = parsed
                user = session.get(User, uid)
                if user is not None and user.token_version == tv:
                    return user
    raise HTTPException(401, "Not authenticated.")


# Alias kept so existing imports of `current_user` keep working; API keys
# should be accepted anywhere a session token is, so this isn't a narrower path.
current_user = api_key_or_user


def optional_user(
    authorization: Optional[str] = Header(default=None),
    session: Session = Depends(get_session),
) -> Optional[User]:
    try:
        return api_key_or_user(authorization, session)
    except HTTPException:
        return None
