"""Invoice validation, in four layers (``docs/architecture-identity-central.md``
M5), from "do the numbers add up" to "has this exact invoice been seen before":

1. :mod:`.structural` — arithmetic/structural (totals reconcile, dates make
   sense, a V-tal checksum). This is the original, always-on layer — its
   :func:`validate` keeps its exact signature so existing callers
   (``app/cli.py``, ``app/saas.py``) are unaffected.
2. :mod:`.invoice_likelihood` — is this even an invoice?
3. :mod:`.einvoice` — does a UBL/OIOUBL export carry its required elements,
   and does the source PDF carry an embedded e-invoice or a signature?
4. :mod:`.duplicate` — has this (tenant, vendor, invoice number) been seen
   before?

:func:`full_report` runs whichever of layers 2–4 the caller has the inputs
for and returns them alongside layer 1, as one ``validation`` block.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from ..exporters import CanonicalLine
from ..extraction.loader import Document
from . import duplicate, einvoice, invoice_likelihood
from .structural import validate

__all__ = ["validate", "full_report", "duplicate", "einvoice", "invoice_likelihood"]


def full_report(
    values: Dict[str, Optional[str]],
    lines: List[CanonicalLine],
    *,
    document: Optional[Document] = None,
    pdf_bytes: Optional[bytes] = None,
    xml_bytes: Optional[bytes] = None,
    session: Optional[Session] = None,
    user_id: Optional[int] = None,
) -> dict:
    """All four layers, each run only when its inputs are available."""
    report: dict = {"structural": validate(values, lines)}

    if document is not None:
        report["invoice_likelihood"] = invoice_likelihood.score(document, values, bool(lines))

    if xml_bytes is not None:
        e = einvoice.validate_ubl(xml_bytes)
        if pdf_bytes is not None:
            e["embedded_einvoice"] = einvoice.detect_embedded_einvoice(pdf_bytes)
            e["pdf_signed"] = einvoice.detect_pdf_signature(pdf_bytes)
        report["einvoice"] = e
    elif pdf_bytes is not None:
        report["einvoice"] = {
            "embedded_einvoice": einvoice.detect_embedded_einvoice(pdf_bytes),
            "pdf_signed": einvoice.detect_pdf_signature(pdf_bytes),
        }

    if session is not None and user_id is not None:
        report["duplicate"] = duplicate.check(session, user_id, values)

    return report
