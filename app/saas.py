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

from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
import re

from pydantic import BaseModel, Field as PydField, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import auth, canonical, exporters, repo
from .db import get_session
from .db_models import OutputProfile, ProfileField, User
from .exporters import CanonicalInvoice, CanonicalLine
from .extraction import fields as field_extractor
from .extraction import lines as line_extractor
from .extraction import loader
from .extraction import template as templater
from .models import MappingIn, TemplateIn

router = APIRouter(prefix="/api")

_MAX_BYTES = 10 * 1024 * 1024
_CONFIG = field_extractor.load_config()


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


class TokenOut(BaseModel):
    token: str
    email: str


class MeOut(BaseModel):
    id: int
    email: str


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
    return TokenOut(token=auth.make_token(user.id), email=user.email)


@router.post("/auth/login", response_model=TokenOut)
def login(body: Credentials, session: Session = Depends(get_session)) -> TokenOut:
    user = auth.authenticate(session, body.email, body.password)
    if user is None:
        raise HTTPException(401, "Wrong email or password.")
    return TokenOut(token=auth.make_token(user.id), email=user.email)


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(auth.current_user)) -> MeOut:
    return MeOut(id=user.id, email=user.email)


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

def _lines_to_canonical(line_items) -> List[CanonicalLine]:
    out: List[CanonicalLine] = []
    for ln in line_items:
        out.append(
            CanonicalLine(
                description=ln.description.value,
                quantity=ln.quantity.value,
                unit=ln.unit.value,
                unit_price=ln.unit_price.value,
                amount=ln.amount.value,
            )
        )
    return out


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
) -> CanonicalInvoice:
    """Project a parsed document onto the canonical vocabulary.

    Central template first, heuristics to fill the gaps, then — if the vendor was
    previously unknown but is identifiable — learn it centrally for next time.
    """
    text = templater.document_text(document)
    vendor = repo.detect_vendor(session, text)
    values: dict = {}

    if vendor is not None and vendor.mappings:
        template = TemplateIn(fields=[
            MappingIn(
                output=m.output_key, strategy=m.strategy, label=m.source_label,
                relation=m.relation, value_type=m.value_type, page=m.page, bbox=m.bbox,
            )
            for m in vendor.mappings
        ])
        for rf in templater.apply_template(document, template):
            if rf.found:
                values[rf.output] = rf.value

    # Fill anything the template didn't cover from layout heuristics — this is
    # what lets a never-taught vendor still produce output the first time.
    suggestions = templater.field_suggestions(document, _CONFIG)
    for s in suggestions:
        if s.value is not None:
            values.setdefault(s.suggested_key, s.value)
    values.setdefault("Currency", field_extractor.detect_currency(document))

    lines = _lines_to_canonical(line_extractor.extract_line_items(document, _CONFIG))

    # Auto-learn: store a central template for a vendor we could identify but
    # hadn't seen before, so the next customer's upload is an instant hit.
    if vendor is None:
        _maybe_learn_vendor(session, values, suggestions, learn_as_user)

    return CanonicalInvoice(values=values, lines=lines)


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
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file.")
    if len(data) > _MAX_BYTES:
        raise HTTPException(413, "File too large (max 10 MB).")
    try:
        document = loader.load(data)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, f"Could not read PDF: {exc}") from exc

    invoice = build_canonical(session, document, learn_as_user=user.id)

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
    headers = {"Content-Disposition": f'attachment; filename="{stem}.{rendered.extension}"'}
    return Response(content=rendered.body, media_type=rendered.media_type, headers=headers)
