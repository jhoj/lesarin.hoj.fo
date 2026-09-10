"""The SaaS surface: accounts, output profiles, and one-shot export.

This is the customer-facing half of the product. The promise is simple — *log
in, upload an invoice, get the data in the shape you want* — and the magic that
makes it "just work" lives in :func:`build_canonical`:

1. The vendor is recognised from the document (V-tal / keywords).
2. If anyone has ever taught that vendor, its **central** template is applied.
3. Whatever the template doesn't cover is filled from layout heuristics, so a
   brand-new vendor still produces useful output the first time.
4. If the vendor was new, what we just learned is saved **centrally** — so the
   next customer to upload that vendor's invoice gets a clean hit. The customer
   is never asked to manage any of this.

The customer only ever configures an :class:`OutputProfile`: which canonical
fields they want, renamed to their keys, in json / xml / ubl / oioubl.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
import re

from pydantic import BaseModel, Field as PydField, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import auth, canonical, engine, exporters, rate_limit, repo, validation
from .db import get_session
from .db_models import ApiKey, ExportRecord, MfaCredential, OutputProfile, ProfileField, User
from .exporters import CanonicalInvoice
from .extraction import loader

router = APIRouter(prefix="/api")
logger = logging.getLogger("lesarin.saas")

_MAX_BYTES = 10 * 1024 * 1024

# Uploads per account per minute. Generous enough for the batch client to work
# through a folder, low enough that a runaway loop can't monopolise the worker.
_EXPORT_RATE_PER_MINUTE = int(os.environ.get("LESARIN_EXPORT_RATE_PER_MINUTE", "60"))
export_limiter = rate_limit.RateLimiter(_EXPORT_RATE_PER_MINUTE)


# --- Schemas ---------------------------------------------------------------

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class Credentials(BaseModel):
    email: str
    password: str = PydField(min_length=8)

    @field_validator("email")
    @classmethod
    def _valid_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not _EMAIL_RE.match(v):
            raise ValueError("Enter a valid email address.")
        return v


class LoginIn(Credentials):
    totp: Optional[str] = None
    recovery_code: Optional[str] = None


class TokenOut(BaseModel):
    token: str
    email: str


class MeOut(BaseModel):
    id: int
    email: str
    is_staff: bool
    mfa_enabled: bool


class ApiKeyOut(BaseModel):
    id: int
    name: str
    prefix: str
    created_at: str
    last_used_at: Optional[str]
    revoked_at: Optional[str]


class ApiKeyCreatedOut(ApiKeyOut):
    key: str  # plaintext — only ever returned once, at creation


class ApiKeyIn(BaseModel):
    name: str = PydField(min_length=1, max_length=128)


class MfaEnrollOut(BaseModel):
    otpauth_url: str
    secret: str
    recovery_codes: List[str]


class MfaCodeIn(BaseModel):
    code: str


class MfaDisableIn(BaseModel):
    password: str


class LogoutAllOut(BaseModel):
    token_version: int


class ProfileFieldIn(BaseModel):
    canonical: str
    output_name: str = ""


class ProfileIn(BaseModel):
    name: str = "Default"
    fmt: str = "json"
    is_default: bool = False
    fields: List[ProfileFieldIn] = PydField(default_factory=list)


class ProfileOut(BaseModel):
    id: int
    name: str
    fmt: str
    is_default: bool
    fields: List[ProfileFieldIn]


class CanonicalFieldOut(BaseModel):
    key: str
    display_name: str
    value_type: str


class ExportRecordOut(BaseModel):
    id: int
    created_at: str
    filename: Optional[str]
    fmt: str
    source: str
    vendor_name: Optional[str]
    invoice_no: Optional[str]
    located: int
    requested: int
    missing: List[str]
    valid: bool
    problems: int
    ocr_used: bool
    duration_ms: int


_VALID_FORMATS = {"json", "xml", "ubl", "oioubl"}


def _profile_out(p: OutputProfile) -> ProfileOut:
    return ProfileOut(
        id=p.id,
        name=p.name,
        fmt=p.fmt,
        is_default=p.is_default,
        fields=[ProfileFieldIn(canonical=f.canonical, output_name=f.output_name) for f in p.fields],
    )


# --- Auth ------------------------------------------------------------------

@router.post("/auth/register", response_model=TokenOut)
def register(body: Credentials, session: Session = Depends(get_session)) -> TokenOut:
    if auth.get_user_by_email(session, body.email):
        raise HTTPException(409, "An account with that email already exists.")
    user = auth.create_user(session, body.email, body.password)
    _create_default_profile(session, user)
    return TokenOut(token=auth.make_token(user.id, user.token_version), email=user.email)


@router.post("/auth/login", response_model=TokenOut)
def login(body: LoginIn, session: Session = Depends(get_session)) -> TokenOut:
    user = auth.get_user_by_email(session, body.email)
    if user is not None and auth.is_locked(user):
        raise HTTPException(423, "Too many failed attempts. Try again in a few minutes.")
    if user is None or not auth.verify_password(body.password, user.password_hash):
        if user is not None:
            auth.record_login_failure(session, user)
        raise HTTPException(401, "Wrong email or password.")

    mfa = session.scalar(
        select(MfaCredential).where(MfaCredential.user_id == user.id, MfaCredential.confirmed_at.isnot(None))
    )
    if mfa is not None:
        ok = bool(body.totp) and auth.verify_totp(mfa.secret, body.totp)
        if not ok and body.recovery_code and auth.consume_recovery_code(mfa, body.recovery_code):
            ok = True
            session.commit()
        if not ok:
            auth.record_login_failure(session, user)
            detail = "MFA code required." if not (body.totp or body.recovery_code) else "Invalid MFA code."
            raise HTTPException(401, detail)

    auth.record_login_success(session, user)
    return TokenOut(token=auth.make_token(user.id, user.token_version), email=user.email)


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(auth.current_user), session: Session = Depends(get_session)) -> MeOut:
    mfa = session.scalar(
        select(MfaCredential).where(MfaCredential.user_id == user.id, MfaCredential.confirmed_at.isnot(None))
    )
    return MeOut(
        id=user.id, email=user.email, is_staff=user.is_staff, mfa_enabled=mfa is not None
    )


@router.post("/me/logout-all", response_model=LogoutAllOut)
def logout_all(
    user: User = Depends(auth.current_user), session: Session = Depends(get_session)
) -> LogoutAllOut:
    """Invalidate every outstanding session token (API keys are untouched —
    they're a separate credential, revoked individually)."""
    user.token_version += 1
    session.commit()
    return LogoutAllOut(token_version=user.token_version)


# --- API keys ----------------------------------------------------------

def _api_key_out(k: ApiKey, cls=ApiKeyOut, **extra) -> ApiKeyOut:
    return cls(
        id=k.id,
        name=k.name,
        prefix=k.prefix,
        created_at=k.created_at.isoformat(),
        last_used_at=k.last_used_at.isoformat() if k.last_used_at else None,
        revoked_at=k.revoked_at.isoformat() if k.revoked_at else None,
        **extra,
    )


@router.get("/me/api-keys", response_model=List[ApiKeyOut])
def list_api_keys(
    user: User = Depends(auth.current_user), session: Session = Depends(get_session)
) -> List[ApiKeyOut]:
    keys = session.scalars(select(ApiKey).where(ApiKey.user_id == user.id).order_by(ApiKey.id))
    return [_api_key_out(k) for k in keys]


@router.post("/me/api-keys", response_model=ApiKeyCreatedOut)
def create_api_key(
    body: ApiKeyIn,
    user: User = Depends(auth.current_user),
    session: Session = Depends(get_session),
) -> ApiKeyCreatedOut:
    full, prefix, hashed = auth.generate_api_key()
    key = ApiKey(user_id=user.id, name=body.name.strip(), prefix=prefix, hashed_key=hashed)
    session.add(key)
    session.commit()
    session.refresh(key)
    return _api_key_out(key, cls=ApiKeyCreatedOut, key=full)


@router.delete("/me/api-keys/{key_id}")
def revoke_api_key(
    key_id: int, user: User = Depends(auth.current_user), session: Session = Depends(get_session)
) -> dict:
    key = session.get(ApiKey, key_id)
    if key is None or key.user_id != user.id:
        raise HTTPException(404, "API key not found.")
    if key.revoked_at is None:
        key.revoked_at = datetime.now(timezone.utc)
        session.commit()
    return {"revoked": key_id}


# --- MFA -----------------------------------------------------------------

@router.post("/me/mfa/enroll", response_model=MfaEnrollOut)
def mfa_enroll(
    user: User = Depends(auth.current_user), session: Session = Depends(get_session)
) -> MfaEnrollOut:
    """Start (or restart) enrollment: a fresh, unconfirmed secret + recovery
    codes. Nothing is required at login until /mfa/verify confirms it."""
    existing = session.scalar(select(MfaCredential).where(MfaCredential.user_id == user.id))
    secret = auth.generate_totp_secret()
    codes = auth.generate_recovery_codes()
    hashed_codes = [auth.hash_recovery_code(c) for c in codes]
    if existing is not None:
        existing.secret = secret
        existing.confirmed_at = None
        existing.recovery_codes = hashed_codes
    else:
        session.add(MfaCredential(user_id=user.id, secret=secret, recovery_codes=hashed_codes))
    session.commit()
    otpauth = f"otpauth://totp/Lesarin:{user.email}?secret={secret}&issuer=Lesarin"
    return MfaEnrollOut(otpauth_url=otpauth, secret=secret, recovery_codes=codes)


@router.post("/me/mfa/verify")
def mfa_verify(
    body: MfaCodeIn,
    user: User = Depends(auth.current_user),
    session: Session = Depends(get_session),
) -> dict:
    mfa = session.scalar(select(MfaCredential).where(MfaCredential.user_id == user.id))
    if mfa is None:
        raise HTTPException(404, "No pending MFA enrollment. Call /me/mfa/enroll first.")
    if not auth.verify_totp(mfa.secret, body.code):
        raise HTTPException(401, "Invalid code.")
    mfa.confirmed_at = datetime.now(timezone.utc)
    session.commit()
    return {"enabled": True}


@router.delete("/me/mfa")
def mfa_disable(
    body: MfaDisableIn,
    user: User = Depends(auth.current_user),
    session: Session = Depends(get_session),
) -> dict:
    if not auth.verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Wrong password.")
    mfa = session.scalar(select(MfaCredential).where(MfaCredential.user_id == user.id))
    if mfa is not None:
        session.delete(mfa)
        session.commit()
    return {"enabled": False}


# --- Export history --------------------------------------------------------

@router.get("/me/exports", response_model=List[ExportRecordOut])
def list_exports(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(auth.current_user),
    session: Session = Depends(get_session),
) -> List[ExportRecordOut]:
    """What this account has put through the service, most recent first."""
    records = session.scalars(
        select(ExportRecord)
        .where(ExportRecord.user_id == user.id)
        .order_by(ExportRecord.created_at.desc(), ExportRecord.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return [
        ExportRecordOut(
            id=r.id,
            created_at=r.created_at.isoformat(),
            filename=r.filename,
            fmt=r.fmt,
            source=r.source,
            vendor_name=r.vendor_name,
            invoice_no=r.invoice_no,
            located=r.located,
            requested=r.requested,
            missing=list(r.missing or []),
            valid=r.valid,
            problems=r.problems,
            ocr_used=r.ocr_used,
            duration_ms=r.duration_ms,
        )
        for r in records
    ]


# --- Canonical vocabulary (for building a profile in the UI) ---------------

@router.get("/canonical-fields", response_model=List[CanonicalFieldOut])
def canonical_fields() -> List[CanonicalFieldOut]:
    return [
        CanonicalFieldOut(key=k, display_name=v["display_name"], value_type=v["value_type"])
        for k, v in canonical.CANONICAL_FIELDS.items()
    ]


# --- Output profiles -------------------------------------------------------

def _create_default_profile(session: Session, user: User) -> OutputProfile:
    """A ready-to-use profile so a new account exports something immediately."""
    profile = OutputProfile(user_id=user.id, name="Default", fmt="json", is_default=True)
    for order, key in enumerate(canonical.CANONICAL_ORDER):
        profile.fields.append(
            ProfileField(canonical=key, output_name=key, sort_order=order)
        )
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return profile


def _validate_profile(body: ProfileIn) -> None:
    if body.fmt not in _VALID_FORMATS:
        raise HTTPException(422, f"Unknown format {body.fmt!r}. Use one of {sorted(_VALID_FORMATS)}.")
    for f in body.fields:
        if not canonical.is_canonical(f.canonical):
            raise HTTPException(422, f"Unknown canonical field {f.canonical!r}.")


def _owned_profile(session: Session, user: User, profile_id: int) -> OutputProfile:
    profile = session.get(OutputProfile, profile_id)
    if profile is None or profile.user_id != user.id:
        raise HTTPException(404, "Profile not found.")
    return profile


@router.get("/me/profiles", response_model=List[ProfileOut])
def list_profiles(
    user: User = Depends(auth.current_user), session: Session = Depends(get_session)
) -> List[ProfileOut]:
    profiles = session.scalars(
        select(OutputProfile).where(OutputProfile.user_id == user.id).order_by(OutputProfile.id)
    )
    return [_profile_out(p) for p in profiles]


@router.post("/me/profiles", response_model=ProfileOut)
def create_profile(
    body: ProfileIn,
    user: User = Depends(auth.current_user),
    session: Session = Depends(get_session),
) -> ProfileOut:
    _validate_profile(body)
    profile = OutputProfile(user_id=user.id, name=body.name, fmt=body.fmt)
    _set_profile_fields(profile, body)
    session.add(profile)
    _apply_default_flag(session, user, profile, body.is_default)
    session.commit()
    session.refresh(profile)
    return _profile_out(profile)


@router.put("/me/profiles/{profile_id}", response_model=ProfileOut)
def update_profile(
    profile_id: int,
    body: ProfileIn,
    user: User = Depends(auth.current_user),
    session: Session = Depends(get_session),
) -> ProfileOut:
    _validate_profile(body)
    profile = _owned_profile(session, user, profile_id)
    profile.name = body.name
    profile.fmt = body.fmt
    profile.fields.clear()
    _set_profile_fields(profile, body)
    _apply_default_flag(session, user, profile, body.is_default)
    session.commit()
    session.refresh(profile)
    return _profile_out(profile)


@router.delete("/me/profiles/{profile_id}")
def delete_profile(
    profile_id: int,
    user: User = Depends(auth.current_user),
    session: Session = Depends(get_session),
) -> dict:
    profile = _owned_profile(session, user, profile_id)
    session.delete(profile)
    session.commit()
    return {"deleted": profile_id}


def _set_profile_fields(profile: OutputProfile, body: ProfileIn) -> None:
    for order, f in enumerate(body.fields):
        profile.fields.append(
            ProfileField(canonical=f.canonical, output_name=f.output_name or f.canonical, sort_order=order)
        )


def _apply_default_flag(session: Session, user: User, profile: OutputProfile, is_default: bool) -> None:
    """At most one default per user; setting one clears the others."""
    profile.is_default = is_default
    if is_default:
        session.flush()  # ensure profile has an id to exclude
        others = session.scalars(
            select(OutputProfile).where(
                OutputProfile.user_id == user.id, OutputProfile.id != profile.id
            )
        )
        for other in others:
            other.is_default = False


# --- Export pipeline -------------------------------------------------------

def _suggestions_to_mappings(suggestions) -> List[dict]:
    """Turn first-pass heuristic field suggestions into vendor template mappings."""
    mappings: List[dict] = []
    for s in suggestions:
        if s.read_labels:
            mappings.append({
                "output": s.suggested_key,
                "strategy": "label",
                "label": s.read_labels[0],
                "relation": "right",
                "value_type": s.value_type,
            })
        elif s.bbox and s.page:
            mappings.append({
                "output": s.suggested_key,
                "strategy": "region",
                "value_type": s.value_type,
                "page": s.page,
                "bbox": s.bbox,
            })
    return mappings


def build_canonical(
    session: Session, document: loader.Document, learn_as_user: Optional[int] = None
) -> tuple[CanonicalInvoice, "engine.CanonicalExtraction"]:
    """Project a parsed document onto the canonical vocabulary, then — if the
    vendor was previously unknown but identifiable — learn it centrally for next
    time. The projection itself lives in :mod:`app.engine`, shared with the CLI.

    Returns the format-neutral invoice plus the raw extraction (so callers can
    report how the read went: template vs heuristic, per-field confidence).
    """
    ext = engine.extract(session, document)
    values = ext.values()
    values.setdefault("Currency", None)  # keep the key present even when unknown

    # Auto-learn: store a central template for a vendor we could identify but
    # hadn't seen before, so the next customer's upload is an instant hit.
    if ext.vendor is None:
        _maybe_learn_vendor(session, values, ext.suggestions, learn_as_user)

    return CanonicalInvoice(values=values, lines=ext.lines), ext


def _maybe_learn_vendor(session: Session, values: dict, suggestions, learn_as_user) -> None:
    identifier = values.get("VendorNo")
    if not identifier:
        return  # no stable identifier → detection wouldn't work next time
    existing = repo.get_vendor_by_identifier(session, str(identifier))
    if existing is not None:
        return
    name = values.get("VendorName") or f"Vendor {identifier}"
    mappings = _suggestions_to_mappings(suggestions)
    if not mappings:
        return
    vendor = repo.create_vendor(
        session, identifier=str(identifier), name=str(name), mappings=mappings
    )
    if learn_as_user is not None:
        vendor.created_by_user_id = learn_as_user
        session.commit()


def _resolve_profile(
    session: Session, user: User, profile_id: Optional[int]
) -> Optional[OutputProfile]:
    if profile_id is not None:
        return _owned_profile(session, user, profile_id)
    return session.scalar(
        select(OutputProfile).where(
            OutputProfile.user_id == user.id, OutputProfile.is_default.is_(True)
        )
    )


@router.post("/me/export")
async def export_invoice(
    file: UploadFile = File(...),
    profile_id: Optional[int] = Query(default=None),
    fmt: Optional[str] = Query(default=None, description="Override the profile's format."),
    user: User = Depends(auth.current_user),
    session: Session = Depends(get_session),
) -> Response:
    """Upload a PDF, get it back in the customer's chosen shape and format."""
    # Checked before anything else: a rejected upload should cost nothing.
    retry_after = export_limiter.take(f"user:{user.id}")
    if retry_after is not None:
        raise HTTPException(
            429,
            f"Too many uploads — the limit is {_EXPORT_RATE_PER_MINUTE} per minute. "
            f"Try again in {retry_after:.0f}s.",
            headers={"Retry-After": str(max(1, int(retry_after + 0.999)))},
        )
    started = time.perf_counter()
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file.")
    if len(data) > _MAX_BYTES:
        raise HTTPException(413, "File too large (max 10 MB).")
    try:
        # Parsing (and OCR on scans) is CPU-bound and can run for seconds. The
        # service runs a single uvicorn worker, so doing this on the event loop
        # would stall every other customer's request until it finished.
        document = await run_in_threadpool(loader.load, data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("export user=%s file=%r could not read PDF: %s", user.id, file.filename, exc)
        raise HTTPException(422, f"Could not read PDF: {exc}") from exc

    invoice, extraction = build_canonical(session, document, learn_as_user=user.id)

    profile = _resolve_profile(session, user, profile_id)
    profile_fields = (
        [{"canonical": f.canonical, "output_name": f.output_name} for f in profile.fields]
        if profile else None
    )
    out_fmt = (fmt or (profile.fmt if profile else "json")).lower()
    if out_fmt not in _VALID_FORMATS:
        raise HTTPException(422, f"Unknown format {out_fmt!r}.")

    rendered = exporters.render(invoice, out_fmt, profile_fields)
    stem = (file.filename or "invoice").rsplit(".", 1)[0]
    # Machine-readable read quality, so automation callers can branch without
    # parsing the body: how the read was made and whether the numbers held up.
    check = validation.validate(extraction.values(), extraction.lines)
    # Measured against what the customer actually asked for, not against what
    # the engine happened to attempt — a field the reader never even tried for
    # is still an empty value in their file, and they need to know about it.
    requested = (
        [f["canonical"] for f in profile_fields] if profile_fields else list(canonical.CANONICAL_ORDER)
    )
    found = extraction.values()
    missing = [key for key in requested if found.get(key) in (None, "")]
    headers = {
        "Content-Disposition": f'attachment; filename="{stem}.{rendered.extension}"',
        "X-Lesarin-Source": extraction.source,          # template | heuristic | none
        "X-Lesarin-Valid": "true" if check["valid"] else "false",
        "X-Lesarin-Problems": str(len(check["problems"])),
        # How much of the requested output was located, and precisely what was
        # not — so a caller (or the UI) can say "check these two fields"
        # instead of silently handing over a form with empty values.
        "X-Lesarin-Located": f"{len(requested) - len(missing)}/{len(requested)}",
        "X-Lesarin-Missing": ",".join(missing),
        "X-Lesarin-Vendor": extraction.vendor.name if extraction.vendor else "",
    }
    # The line to reach for when a customer asks why an export looked wrong:
    # which vendor was recognised, whether a template or the heuristics did the
    # reading, how much was found, and whether the numbers held together.
    located = sum(1 for f in extraction.fields.values() if f.found)
    logger.info(
        "export user=%s file=%r vendor=%s source=%s located=%d/%d ocr=%s fmt=%s "
        "valid=%s problems=%d ms=%d",
        user.id,
        file.filename,
        extraction.vendor.identifier if extraction.vendor else None,
        extraction.source,
        located,
        len(extraction.fields),
        document.ocr_used,
        out_fmt,
        check["valid"],
        len(check["problems"]),
        (time.perf_counter() - started) * 1000,
    )

    _record_export(
        session,
        user=user,
        filename=file.filename,
        fmt=out_fmt,
        extraction=extraction,
        check=check,
        profile_fields=profile_fields,
        ocr_used=document.ocr_used,
        duration_ms=int((time.perf_counter() - started) * 1000),
    )
    return Response(content=rendered.body, media_type=rendered.media_type, headers=headers)


def _record_export(
    session: Session,
    *,
    user: User,
    filename: Optional[str],
    fmt: str,
    extraction,
    check: dict,
    profile_fields,
    ocr_used: bool,
    duration_ms: int,
) -> None:
    """Leave a trace of the read. Never fails the request: the customer has
    their data, and losing a log line is not worth turning that into a 500."""
    values = extraction.values()
    requested = (
        [f["canonical"] for f in profile_fields]
        if profile_fields
        else list(canonical.CANONICAL_ORDER)
    )
    missing = [key for key in requested if values.get(key) in (None, "")]
    try:
        session.add(
            ExportRecord(
                user_id=user.id,
                filename=filename,
                fmt=fmt,
                source=extraction.source,
                vendor_identifier=extraction.vendor.identifier if extraction.vendor else None,
                vendor_name=extraction.vendor.name if extraction.vendor else values.get("VendorName"),
                invoice_no=values.get("InvoiceNo"),
                located=len(requested) - len(missing),
                requested=len(requested),
                missing=missing,
                valid=bool(check["valid"]),
                problems=len(check["problems"]),
                ocr_used=ocr_used,
                duration_ms=duration_ms,
            )
        )
        session.commit()
    except Exception:  # noqa: BLE001
        logger.exception("could not record export for user=%s", user.id)
        session.rollback()
