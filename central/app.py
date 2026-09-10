"""The central knowledge service's HTTP surface — ``docs/brain-sync.md``.

Two audiences:

* **Sites** (``site_agent``-identified deployments) — ``POST /sites/enroll``
  once, then signed ``POST /sync/push`` / ``GET /sync/pull``. A push
  publishes immediately: no approval step, because only mappings a human
  already confirmed at the site are ever included in one.
* **Admins** (the back-office team) — bootstrap/login, mint enrollment
  tokens, activate/revoke sites, monitor published templates and the shared
  vocabulary, and manually withdraw a template gone bad
  (docs/brain-sync.md "Withdrawing").

Run standalone: ``uvicorn central.app:app``.
"""

from __future__ import annotations

import hashlib
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import Body, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import auth as human_auth

from . import admin_auth, identity_verify, sync as central_sync
from .db import get_session, init_db
from .models import Admin, EnrollmentToken, LabelObservation, Site, VendorTemplate
from .trust import current_site


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Lesarin — Central Knowledge Service", lifespan=lifespan)

# The back-office UI (frontend/src/app/backoffice.ts) calls this service
# cross-origin from wherever the site's Angular app is served.
_extra_origins = [o.strip() for o in os.environ.get("CENTRAL_CORS_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200", "http://127.0.0.1:4200", *_extra_origins],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# --- Schemas -----------------------------------------------------------------

class EnrollIn(BaseModel):
    name: str
    public_key: str  # PEM
    enrollment_token: str


class SiteOut(BaseModel):
    id: int
    name: str
    fingerprint: str
    status: str
    created_at: str
    activated_at: Optional[str] = None


class AdminCredentials(BaseModel):
    email: str
    password: str
    totp: Optional[str] = None
    bootstrap_token: Optional[str] = None


class AdminTokenOut(BaseModel):
    token: str
    email: str


class EnrollmentTokenOut(BaseModel):
    token: str  # plaintext — shown once
    note: str


class TemplateOut(BaseModel):
    id: int
    identifier: str
    identifier_kind: str
    name: str
    layout_fingerprint: str
    version: int
    field_count: int
    withdrawn: bool
    published_at: str
    updated_at: str


class VocabularyOut(BaseModel):
    label: str
    suggested_key: Optional[str] = None
    sites: int
    revealed: bool


def _site_out(s: Site) -> SiteOut:
    return SiteOut(
        id=s.id, name=s.name, fingerprint=s.fingerprint, status=s.status,
        created_at=s.created_at.isoformat(),
        activated_at=s.activated_at.isoformat() if s.activated_at else None,
    )


def _template_out(t: VendorTemplate) -> TemplateOut:
    return TemplateOut(
        id=t.id, identifier=t.identifier, identifier_kind=t.identifier_kind, name=t.name,
        layout_fingerprint=t.layout_fingerprint, version=t.version,
        field_count=len(t.mappings or []), withdrawn=t.withdrawn_at is not None,
        published_at=t.published_at.isoformat(), updated_at=t.updated_at.isoformat(),
    )


# --- Site enrollment (public — gated by a one-time token) -------------------

@app.post("/sites/enroll", response_model=SiteOut)
def enroll_site(body: EnrollIn, session: Session = Depends(get_session)) -> SiteOut:
    token_hash = hashlib.sha256(body.enrollment_token.encode("utf-8")).hexdigest()
    tok = session.scalar(select(EnrollmentToken).where(EnrollmentToken.token_hash == token_hash))
    if tok is None or tok.used_at is not None:
        raise HTTPException(401, "Invalid or already-used enrollment token.")

    try:
        canonical = identity_verify.canonical_pem(body.public_key.encode("ascii"))
    except ValueError as exc:
        raise HTTPException(422, f"Invalid public key: {exc}") from exc
    fp = identity_verify.fingerprint_of_pem(body.public_key.encode("ascii"))
    if session.scalar(select(Site).where(Site.fingerprint == fp)):
        raise HTTPException(409, "This public key is already enrolled.")

    site = Site(name=body.name, public_key=canonical.decode("ascii"), fingerprint=fp, status="pending")
    session.add(site)
    tok.used_at = datetime.now(timezone.utc)
    session.flush()
    tok.used_by_site_id = site.id
    session.commit()
    session.refresh(site)
    return _site_out(site)


# --- Sync (signed by site identity) ------------------------------------------

@app.post("/sync/push")
def sync_push(
    bundle: dict = Body(...),
    site: Site = Depends(current_site),
    session: Session = Depends(get_session),
) -> dict:
    try:
        return central_sync.apply_push(session, site, bundle)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/sync/pull")
def sync_pull(site: Site = Depends(current_site), session: Session = Depends(get_session)) -> dict:
    return central_sync.build_pull_bundle(session)


# --- Admin: accounts ----------------------------------------------------------

@app.post("/admin/bootstrap", response_model=AdminTokenOut)
def bootstrap_admin(body: AdminCredentials, session: Session = Depends(get_session)) -> AdminTokenOut:
    if not admin_auth.bootstrap_token_ok(body.bootstrap_token):
        raise HTTPException(401, "Invalid bootstrap token.")
    if admin_auth.get_admin_by_email(session, body.email):
        raise HTTPException(409, "An admin with that email already exists.")
    admin = admin_auth.create_admin(session, body.email, body.password)
    return AdminTokenOut(token=human_auth.make_token(admin.id, admin.token_version), email=admin.email)


@app.post("/admin/login", response_model=AdminTokenOut)
def admin_login(body: AdminCredentials, session: Session = Depends(get_session)) -> AdminTokenOut:
    admin = admin_auth.authenticate_admin(session, body.email, body.password, body.totp)
    if admin is None:
        raise HTTPException(401, "Wrong credentials or missing/invalid MFA code.")
    return AdminTokenOut(token=human_auth.make_token(admin.id, admin.token_version), email=admin.email)


# --- Admin: sites --------------------------------------------------------------

@app.post("/admin/enrollment-tokens", response_model=EnrollmentTokenOut)
def mint_enrollment_token(
    note: str = Body("", embed=True),
    admin: Admin = Depends(admin_auth.current_admin),
    session: Session = Depends(get_session),
) -> EnrollmentTokenOut:
    raw = secrets.token_urlsafe(24)
    session.add(EnrollmentToken(token_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(), note=note))
    session.commit()
    return EnrollmentTokenOut(token=raw, note=note)


@app.get("/admin/sites", response_model=List[SiteOut])
def list_sites(
    admin: Admin = Depends(admin_auth.current_admin), session: Session = Depends(get_session)
) -> List[SiteOut]:
    return [_site_out(s) for s in session.scalars(select(Site).order_by(Site.id))]


@app.post("/admin/sites/{site_id}/activate", response_model=SiteOut)
def activate_site(
    site_id: int, admin: Admin = Depends(admin_auth.current_admin), session: Session = Depends(get_session)
) -> SiteOut:
    site = session.get(Site, site_id)
    if site is None:
        raise HTTPException(404, "Site not found.")
    site.status = "active"
    site.activated_at = datetime.now(timezone.utc)
    session.commit()
    session.refresh(site)
    return _site_out(site)


@app.post("/admin/sites/{site_id}/revoke", response_model=SiteOut)
def revoke_site(
    site_id: int, admin: Admin = Depends(admin_auth.current_admin), session: Session = Depends(get_session)
) -> SiteOut:
    site = session.get(Site, site_id)
    if site is None:
        raise HTTPException(404, "Site not found.")
    site.status = "revoked"
    session.commit()
    session.refresh(site)
    return _site_out(site)


# --- Admin: monitoring + manual withdrawal ------------------------------------

@app.get("/admin/templates", response_model=List[TemplateOut])
def list_templates(
    identifier: Optional[str] = None,
    admin: Admin = Depends(admin_auth.current_admin),
    session: Session = Depends(get_session),
) -> List[TemplateOut]:
    stmt = select(VendorTemplate).order_by(VendorTemplate.identifier, VendorTemplate.id)
    if identifier:
        stmt = stmt.where(VendorTemplate.identifier == identifier)
    return [_template_out(t) for t in session.scalars(stmt)]


@app.post("/admin/templates/{template_id}/withdraw", response_model=TemplateOut)
def withdraw_template(
    template_id: int,
    admin: Admin = Depends(admin_auth.current_admin),
    session: Session = Depends(get_session),
) -> TemplateOut:
    """Pull a bad template out of circulation (docs/brain-sync.md
    "Withdrawing"). Reversible in spirit: the next confirmed push for the
    same identifier + layout fingerprint replaces it and un-withdraws it."""
    t = session.get(VendorTemplate, template_id)
    if t is None:
        raise HTTPException(404, "Template not found.")
    t.withdrawn_at = datetime.now(timezone.utc)
    session.commit()
    session.refresh(t)
    return _template_out(t)


@app.get("/admin/vocabulary", response_model=List[VocabularyOut])
def list_vocabulary(
    admin: Admin = Depends(admin_auth.current_admin), session: Session = Depends(get_session)
) -> List[VocabularyOut]:
    """Every observed (label, canonical field) pairing and how many distinct
    sites have reported it — including ones still below the k threshold, so
    an admin can see what's building up."""
    rows = session.scalars(select(LabelObservation).order_by(LabelObservation.label))
    return [
        VocabularyOut(
            label=r.label, suggested_key=r.suggested_key,
            sites=len(r.contributing_fingerprints), revealed=r.revealed,
        )
        for r in rows
    ]
