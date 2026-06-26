"""Data-access layer over the SQLite store.

Thin CRUD helpers plus :func:`detect_vendor`, which recognises which known
vendor an uploaded PDF belongs to by matching its identifier (V-tal) or
keywords against the document text.
"""

from __future__ import annotations

import re
from typing import Iterable, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db_models import FieldMapping, OutputField, Vendor


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
            )
        )


def create_vendor(
    session: Session,
    identifier: str,
    name: str,
    identifier_kind: str = "vtal",
    match_keywords: Optional[List[str]] = None,
    mappings: Optional[List[dict]] = None,
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
    return vendor


def update_vendor(
    session: Session,
    vendor_id: int,
    *,
    identifier: Optional[str] = None,
    name: Optional[str] = None,
    match_keywords: Optional[List[str]] = None,
    mappings: Optional[List[dict]] = None,
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
    return vendor


def delete_vendor(session: Session, vendor_id: int) -> bool:
    vendor = session.get(Vendor, vendor_id)
    if vendor is None:
        return False
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
