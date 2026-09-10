"""Data-access layer over the SQLite store.

Thin CRUD helpers plus :func:`detect_vendor`, which recognises which known
vendor an uploaded PDF belongs to by matching its identifier (V-tal) or
keywords against the document text.
"""

from __future__ import annotations

import logging
import re
from typing import Iterable, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db_models import FieldMapping, LabelObservation, OutputField, Vendor, VendorTemplateVersion

logger = logging.getLogger("lesarin.repo")


# ---- Output fields (the expected-output "setup" table) --------------------

def list_output_fields(session: Session) -> List[OutputField]:
    return list(
        session.scalars(select(OutputField).order_by(OutputField.sort_order, OutputField.id))
    )


def upsert_output_field(
    session: Session,
    key: str,
    display_name: str = "",
    value_type: str = "string",
    sort_order: int = 0,
    aliases: Optional[List[str]] = None,
) -> OutputField:
    field = session.scalar(select(OutputField).where(OutputField.key == key))
    if field is None:
        field = OutputField(key=key)
        session.add(field)
    field.display_name = display_name or field.display_name or key
    field.value_type = value_type
    field.sort_order = sort_order
    if aliases is not None:
        field.aliases = aliases
    session.commit()
    session.refresh(field)
    return field


def delete_output_field(session: Session, key: str) -> bool:
    field = session.scalar(select(OutputField).where(OutputField.key == key))
    if field is None:
        return False
    session.delete(field)
    session.commit()
    return True


# ---- Vendors + their mappings (the template) ------------------------------

def list_vendors(session: Session) -> List[Vendor]:
    return list(session.scalars(select(Vendor).order_by(Vendor.name)))


def get_vendor(session: Session, vendor_id: int) -> Optional[Vendor]:
    return session.get(Vendor, vendor_id)


def get_vendor_by_identifier(
    session: Session, identifier: str, kind: str = "vtal"
) -> Optional[Vendor]:
    return session.scalar(
        select(Vendor).where(Vendor.identifier == identifier, Vendor.identifier_kind == kind)
    )


def _apply_mappings(vendor: Vendor, mappings: Iterable[dict]) -> None:
    """Replace a vendor's mappings wholesale with the provided list of dicts."""
    vendor.mappings.clear()
    for m in mappings:
        bbox = m.get("bbox")
        x0 = top = x1 = bottom = None
        if bbox and len(bbox) == 4:
            x0, top, x1, bottom = (float(v) for v in bbox)
        vendor.mappings.append(
            FieldMapping(
                output_key=m["output"],
                strategy=m.get("strategy", "label"),
                source_label=m.get("label"),
                relation=m.get("relation", "right"),
                value_type=m.get("value_type", "string"),
                page=m.get("page"),
                x0=x0,
                top=top,
                x1=x1,
                bottom=bottom,
                confirmed=bool(m.get("confirmed", False)),
            )
        )


def mappings_of(vendor: Vendor) -> List[dict]:
    """A vendor's mappings in the dict shape create/update take — used for
    snapshots and for putting an old version back."""
    return [
        {
            "output": m.output_key,
            "strategy": m.strategy,
            "label": m.source_label,
            "relation": m.relation,
            "value_type": m.value_type,
            "page": m.page,
            "bbox": m.bbox,
            "confirmed": m.confirmed,
        }
        for m in vendor.mappings
    ]


def confirmed_mappings_of(vendor: Vendor) -> List[dict]:
    """Only the fields a person actually confirmed — what's eligible to push
    to the central brain (docs/brain-sync.md: "the brain only ever receives
    mappings a human confirmed")."""
    return [m for m in mappings_of(vendor) if m["confirmed"]]


def record_version(
    session: Session,
    vendor: Vendor,
    change: str,
    user_id: Optional[int] = None,
) -> VendorTemplateVersion:
    """Snapshot a vendor's template as it now stands.

    Called after every change, so version N is what the template looked like
    once change N had been applied — including the final snapshot taken just
    before a delete.
    """
    latest = session.scalar(
        select(VendorTemplateVersion)
        .where(VendorTemplateVersion.identifier == vendor.identifier)
        .order_by(VendorTemplateVersion.version.desc())
    )
    snapshot = VendorTemplateVersion(
        vendor_id=vendor.id,
        identifier=vendor.identifier,
        identifier_kind=vendor.identifier_kind,
        name=vendor.name,
        version=(latest.version + 1) if latest else 1,
        match_keywords=list(vendor.match_keywords or []),
        mappings=mappings_of(vendor),
        change=change,
        changed_by_user_id=user_id,
    )
    session.add(snapshot)
    session.commit()
    return snapshot


def restore_version(
    session: Session, version_id: int, user_id: Optional[int] = None
) -> Optional[Vendor]:
    """Put an old snapshot back, recreating the vendor if it was deleted.

    The restore is itself recorded as a new version, so going back is as
    reversible as the edit that made it necessary.
    """
    snapshot = session.get(VendorTemplateVersion, version_id)
    if snapshot is None:
        return None

    vendor = get_vendor_by_identifier(session, snapshot.identifier, snapshot.identifier_kind)
    if vendor is None:
        return create_vendor(
            session,
            identifier=snapshot.identifier,
            name=snapshot.name,
            identifier_kind=snapshot.identifier_kind,
            match_keywords=list(snapshot.match_keywords or []),
            mappings=list(snapshot.mappings or []),
            change="restored",
            created_by_user_id=user_id,
        )
    return update_vendor(
        session,
        vendor.id,
        name=snapshot.name,
        match_keywords=list(snapshot.match_keywords or []),
        mappings=list(snapshot.mappings or []),
        change="restored",
        changed_by_user_id=user_id,
    )


def list_versions(session: Session, identifier: str) -> List[VendorTemplateVersion]:
    return list(
        session.scalars(
            select(VendorTemplateVersion)
            .where(VendorTemplateVersion.identifier == identifier)
            .order_by(VendorTemplateVersion.version.desc())
        )
    )


def create_vendor(
    session: Session,
    identifier: str,
    name: str,
    identifier_kind: str = "vtal",
    match_keywords: Optional[List[str]] = None,
    mappings: Optional[List[dict]] = None,
    change: str = "created",
    created_by_user_id: Optional[int] = None,
) -> Vendor:
    vendor = Vendor(
        identifier=identifier.strip(),
        identifier_kind=identifier_kind,
        name=name.strip(),
        match_keywords=match_keywords or [],
    )
    _apply_mappings(vendor, mappings or [])
    session.add(vendor)
    session.commit()
    session.refresh(vendor)
    logger.info(
        "vendor created id=%s identifier=%s name=%r mappings=%d",
        vendor.id, vendor.identifier, vendor.name, len(vendor.mappings),
    )
    record_version(session, vendor, change=change, user_id=created_by_user_id)
    return vendor


def update_vendor(
    session: Session,
    vendor_id: int,
    *,
    identifier: Optional[str] = None,
    name: Optional[str] = None,
    match_keywords: Optional[List[str]] = None,
    mappings: Optional[List[dict]] = None,
    change: str = "updated",
    changed_by_user_id: Optional[int] = None,
) -> Optional[Vendor]:
    vendor = session.get(Vendor, vendor_id)
    if vendor is None:
        return None
    if identifier is not None:
        vendor.identifier = identifier.strip()
    if name is not None:
        vendor.name = name.strip()
    if match_keywords is not None:
        vendor.match_keywords = match_keywords
    if mappings is not None:
        _apply_mappings(vendor, mappings)
    session.commit()
    session.refresh(vendor)
    logger.info(
        "vendor updated id=%s identifier=%s mappings=%d%s",
        vendor.id, vendor.identifier, len(vendor.mappings),
        " (mappings replaced)" if mappings is not None else "",
    )
    record_version(session, vendor, change=change, user_id=changed_by_user_id)
    return vendor


def delete_vendor(session: Session, vendor_id: int, deleted_by_user_id: Optional[int] = None) -> bool:
    vendor = session.get(Vendor, vendor_id)
    if vendor is None:
        return False
    # Warning, not info: templates are shared centrally, so a delete costs every
    # customer that vendor's mappings.
    logger.warning(
        "vendor deleted id=%s identifier=%s name=%r", vendor.id, vendor.identifier, vendor.name
    )
    # Snapshot before it goes — this is the version most worth being able to put
    # back. The row survives the delete (vendor_id goes null) and stays findable
    # by identifier, so a mistaken deletion is recoverable.
    record_version(session, vendor, change="deleted", user_id=deleted_by_user_id)
    session.delete(vendor)
    session.commit()
    return True


# ---- Vendor recognition ---------------------------------------------------

def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def detect_vendor(session: Session, text: str) -> Optional[Vendor]:
    """Best-effort: which known vendor does this document text belong to?

    Matches the vendor's identifier as a digit run anywhere in the text (so
    "Vtal: 314188" / "V-tal 314 188" both hit "314188"), then falls back to a
    case-insensitive keyword match. Returns the first vendor that matches.
    """
    if not text:
        return None
    text_digits = _digits(text)
    lowered = text.lower()
    for vendor in list_vendors(session):
        ident_digits = _digits(vendor.identifier)
        if ident_digits and ident_digits in text_digits:
            return vendor
        for kw in vendor.match_keywords or []:
            if kw and kw.lower() in lowered:
                return vendor
    return None


# ---- Label observations (queued for the next central push) ----------------

def add_label_observation(
    session: Session,
    identifier: Optional[str],
    layout_fingerprint: str,
    label_set: List[str],
    positions: List[dict],
) -> LabelObservation:
    obs = LabelObservation(
        identifier=identifier, layout_fingerprint=layout_fingerprint,
        label_set=label_set, positions=positions,
    )
    session.add(obs)
    session.commit()
    return obs


def list_label_observations(session: Session) -> List[LabelObservation]:
    return list(session.scalars(select(LabelObservation)))


def clear_label_observations(session: Session) -> int:
    observations = list_label_observations(session)
    for obs in observations:
        session.delete(obs)
    session.commit()
    return len(observations)
