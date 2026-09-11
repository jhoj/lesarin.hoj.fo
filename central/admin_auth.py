"""Central's back-office accounts (M4 "team accounts (humans with MFA)").

A different product from the per-tenant SaaS, so its accounts live in their
own table (``Admin``) — but there's no reason to reinvent password hashing,
stateless tokens, or TOTP, so the low-level primitives in ``app.auth`` (none
of which are tied to the SaaS ``User`` model) are reused directly.

Creating a new admin account requires ``CENTRAL_ADMIN_BOOTSTRAP_TOKEN`` (a
shared secret set by whoever operates central) — there's no self-serve
registration, since these accounts can review and publish shared knowledge
every site pulls from.
"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import auth as human_auth

from .db import get_session
from .models import Admin


def bootstrap_token_ok(candidate: Optional[str]) -> bool:
    expected = os.environ.get("CENTRAL_ADMIN_BOOTSTRAP_TOKEN")
    return bool(expected) and candidate == expected


def create_admin(session: Session, email: str, password: str) -> Admin:
    admin = Admin(email=email.strip().lower(), password_hash=human_auth.hash_password(password))
    session.add(admin)
    session.commit()
    session.refresh(admin)
    return admin


def get_admin_by_email(session: Session, email: str) -> Optional[Admin]:
    return session.scalar(select(Admin).where(Admin.email == email.strip().lower()))


def authenticate_admin(
    session: Session, email: str, password: str, totp: Optional[str] = None
) -> Optional[Admin]:
    admin = get_admin_by_email(session, email)
    if admin is None or not human_auth.verify_password(password, admin.password_hash):
        return None
    if admin.mfa_confirmed_at is not None:
        if not totp or not human_auth.verify_totp(admin.mfa_secret, totp):
            return None
    return admin


def _bearer(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    return parts[1].strip() if len(parts) == 2 and parts[0].lower() == "bearer" else None


def current_admin(
    authorization: Optional[str] = Header(default=None),
    session: Session = Depends(get_session),
) -> Admin:
    token = _bearer(authorization)
    claims = human_auth.parse_token(token) if token else None
    admin = session.get(Admin, claims[0]) if claims else None
    if admin is None or claims[1] != admin.token_version:
        raise HTTPException(401, "Not authenticated.")
    return admin
