"""Layer 4 — duplicate detection: has this (tenant, vendor, invoice number)
already been exported?

Reads ``app.db_models.ExportRecord`` — already written by
``app.saas._record_export`` after every export — rather than keeping a
separate table; every export already leaves a trace there. Important once
extraction feeds a payment flow: a re-submitted invoice (by accident or on
purpose) shouldn't silently be paid twice.

Amount is not tracked in ``export_records`` (deliberately — see its
docstring: metadata only), so this layer doesn't attempt amount-anomaly
detection, only exact duplicate invoice-number detection.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..db_models import ExportRecord


def vendor_key(values: dict) -> Optional[str]:
    return values.get("VendorNo") or values.get("VendorName")


def check(session: Session, user_id: int, values: dict) -> dict:
    """``{"checked": False}`` when identity is unknown; otherwise whether this
    (vendor, invoice number) has already been exported by this account.

    Matches identifier-to-identifier or name-to-name (never identifier
    against name) — a vendor might be recorded either way across two reads
    (e.g. the first read of a never-seen vendor has no identifier yet, only a
    name), so either column agreeing is enough.
    """
    identifier, name = values.get("VendorNo"), values.get("VendorName")
    invoice_no = values.get("InvoiceNo")
    if not (identifier or name) or not invoice_no:
        return {"checked": False}

    conditions = []
    if identifier:
        conditions.append(ExportRecord.vendor_identifier == identifier)
    if name:
        conditions.append(ExportRecord.vendor_name == name)

    existing = session.scalar(
        select(ExportRecord)
        .where(ExportRecord.user_id == user_id, ExportRecord.invoice_no == invoice_no, or_(*conditions))
        .order_by(ExportRecord.created_at.desc())
    )
    vkey = identifier or name
    if existing is None:
        return {"checked": True, "duplicate": False, "detail": "first time seeing this invoice"}
    return {
        "checked": True,
        "duplicate": True,
        "detail": f"invoice {invoice_no!r} from {vkey!r} was already exported "
                  f"on {existing.created_at.date().isoformat()}",
    }
