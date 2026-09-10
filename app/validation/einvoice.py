"""E-invoice layer: does a rendered UBL/OIOUBL document carry the elements a
receiver requires, and does the source PDF carry a recognisable embedded
e-invoice or a digital signature?

This is a **structural** check against the required elements — not a full
PEPPOL/OIOUBL Schematron run (those rule sets are large, versioned, and not
vendored here). It catches the common failure ("the export is missing a
mandatory element") without claiming full network-conformance validation.

Embedded-e-invoice and signature detection are byte-level heuristics on the
raw PDF (look for the well-known embedded filenames / the ``/ByteRange``
signature marker) rather than a full PDF object-tree walk, which would need a
PDF-editing library this project doesn't otherwise carry.
"""

from __future__ import annotations

from typing import Dict, List

from lxml import etree

_UBL_NS = {
    "ubl": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
}

# (xpath, human label) — leaf elements that need non-empty text content.
_REQUIRED_TEXT = [
    ("/ubl:Invoice/cbc:ID", "invoice ID (cbc:ID)"),
    ("/ubl:Invoice/cbc:IssueDate", "issue date (cbc:IssueDate)"),
    ("/ubl:Invoice/cbc:InvoiceTypeCode", "invoice type code (cbc:InvoiceTypeCode)"),
    ("/ubl:Invoice/cbc:DocumentCurrencyCode", "currency code (cbc:DocumentCurrencyCode)"),
]

# (xpath, human label) — structural containers that just need to be present
# (they legitimately carry no text of their own, only children).
_REQUIRED_PRESENT = [
    ("/ubl:Invoice/cac:AccountingSupplierParty", "supplier party (cac:AccountingSupplierParty)"),
    ("/ubl:Invoice/cac:LegalMonetaryTotal", "monetary total (cac:LegalMonetaryTotal)"),
    ("/ubl:Invoice/cac:InvoiceLine", "at least one invoice line (cac:InvoiceLine)"),
]

_XMLDSIG_NS = "http://www.w3.org/2000/09/xmldsig#"


def validate_ubl(xml_bytes: bytes) -> Dict[str, object]:
    """Structural check: is every mandatory UBL/OIOUBL element present?"""
    problems: List[str] = []
    try:
        root = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError as exc:
        return {"valid": False, "problems": [f"not well-formed XML: {exc}"], "signed": False}

    tree = etree.ElementTree(root)
    for xpath, label in _REQUIRED_TEXT:
        nodes = tree.xpath(xpath, namespaces=_UBL_NS)
        if not nodes or not (nodes[0].text or "").strip():
            problems.append(f"missing {label}")
    for xpath, label in _REQUIRED_PRESENT:
        if not tree.xpath(xpath, namespaces=_UBL_NS):
            problems.append(f"missing {label}")

    signed = bool(tree.xpath(f"//*[local-name()='Signature' and namespace-uri()='{_XMLDSIG_NS}']"))
    return {"valid": not problems, "problems": problems, "signed": signed}


_EMBEDDED_MARKERS = (b"factur-x.xml", b"zugferd-invoice.xml", b"xrechnung.xml", b"/EmbeddedFiles")


def detect_embedded_einvoice(pdf_bytes: bytes) -> Dict[str, object]:
    """Heuristic: does this PDF carry an embedded Factur-X/ZUGFeRD/XRechnung
    XML (PDF/A-3 hybrid invoice)?"""
    found = [m.decode("ascii") for m in _EMBEDDED_MARKERS if m in pdf_bytes]
    return {"embedded": bool(found), "markers": found}


def detect_pdf_signature(pdf_bytes: bytes) -> bool:
    """Heuristic: does this PDF carry a digital signature (``/ByteRange``,
    the standard marker for a PDF signature dictionary)?"""
    return b"/ByteRange" in pdf_bytes
