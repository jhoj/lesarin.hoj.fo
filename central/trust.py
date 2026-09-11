"""Verify a site's signed request: look up its public key by the JWT's ``kid``
(its fingerprint), then verify the EdDSA signature and that the site is
``active`` (not pending enrollment, not revoked).

This is the asymmetric plane, deliberately separate from the stdlib-only
human-auth plane in ``app/auth.py`` — a site never has a password, and a human
never signs requests with Ed25519.
"""

from __future__ import annotations

from typing import Optional

import jwt
from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_session
from .models import Site


def verify_site_jwt(session: Session, token: str) -> Optional[Site]:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        return None
    kid = header.get("kid")
    if not kid:
        return None
    site = session.scalar(select(Site).where(Site.fingerprint == kid))
    if site is None or site.status != "active":
        return None
    try:
        jwt.decode(token, site.public_key, algorithms=["EdDSA"])
    except jwt.PyJWTError:
        return None
    return site


def _bearer(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def current_site(
    authorization: Optional[str] = Header(default=None),
    session: Session = Depends(get_session),
) -> Site:
    token = _bearer(authorization)
    site = verify_site_jwt(session, token) if token else None
    if site is None:
        raise HTTPException(401, "Invalid, unsigned, or unrecognised site.")
    return site
