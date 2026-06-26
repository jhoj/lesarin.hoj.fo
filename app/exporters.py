"""Render a canonical invoice record into the customer's chosen format.

The pipeline upstream produces a :class:`CanonicalInvoice` — vendor-template (or
heuristic) values projected onto the shared canonical vocabulary. Here we turn
that into bytes the customer asked for:

* ``json``   — a flat object using *their* field names (drop-in for an API body).
* ``xml``    — the same, as a simple ``<Invoice>`` element tree.
* ``ubl``    — OASIS UBL 2.1 ``Invoice`` (the international standard).
* ``oioubl`` — the Danish OIOUBL customisation of UBL (NemHandel / public sector).

Only the ``json``/``xml`` shapes honour per-field renaming; UBL has fixed element
names by definition, so a profile there selects *which* canonical values flow in.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from xml.etree import ElementTree as ET
from xml.dom import minidom

from . import canonical


@dataclass
class CanonicalLine:
    description: Optional[str] = None
    quantity: Optional[str] = None
    unit: Optional[str] = None
    unit_price: Optional[str] = None
    amount: Optional[str] = None

    def as_dict(self) -> Dict[str, Optional[str]]:
        return {
            "description": self.description,
            "quantity": self.quantity,
            "unit": self.unit,
            "unit_price": self.unit_price,
            "amount": self.amount,
        }


@dataclass
class CanonicalInvoice:
    """Located canonical values for one document (the format-neutral record)."""

    values: Dict[str, Optional[str]] = field(default_factory=dict)  # canonical_key -> value
    lines: List[CanonicalLine] = field(default_factory=list)

    def get(self, key: str) -> Optional[str]:
        return self.values.get(key)

    @property
    def currency(self) -> str:
        return self.values.get("Currency") or "DKK"


# A profile field is a (canonical_key, output_name) rename rule.
ProfileSpec = List[Dict[str, str]]


def _selected(invoice: CanonicalInvoice, profile_fields: Optional[ProfileSpec]):
    """Yield (canonical_key, output_name, value) for the profile, in order.

    No profile → every canonical field under its own name, in canonical order.
    """
    if profile_fields:
        for pf in profile_fields:
            ck = pf["canonical"]
            yield ck, pf.get("output_name") or ck, invoice.get(ck)
    else:
        for ck in canonical.CANONICAL_ORDER:
            if ck in invoice.values:
                yield ck, ck, invoice.get(ck)


# --- JSON ------------------------------------------------------------------

def to_json(invoice: CanonicalInvoice, profile_fields: Optional[ProfileSpec] = None) -> str:
    out: Dict[str, object] = {}
    for _ck, name, value in _selected(invoice, profile_fields):
        out[name] = value
    out["lines"] = [ln.as_dict() for ln in invoice.lines]
    return json.dumps(out, ensure_ascii=False, indent=2)


# --- Generic XML -----------------------------------------------------------

def _xml_tag(name: str) -> str:
    """Make a customer field name safe to use as an XML element name."""
    cleaned = "".join(c if (c.isalnum() or c in "_-.") else "_" for c in name.strip())
    if not cleaned or not (cleaned[0].isalpha() or cleaned[0] == "_"):
        cleaned = "_" + cleaned
    return cleaned


def to_xml(invoice: CanonicalInvoice, profile_fields: Optional[ProfileSpec] = None) -> str:
    root = ET.Element("Invoice")
    for _ck, name, value in _selected(invoice, profile_fields):
        el = ET.SubElement(root, _xml_tag(name))
        el.text = value if value is not None else ""
    lines_el = ET.SubElement(root, "Lines")
    for ln in invoice.lines:
        le = ET.SubElement(lines_el, "Line")
        for k, v in ln.as_dict().items():
            child = ET.SubElement(le, _xml_tag(k))
            child.text = v if v is not None else ""
    return _pretty(root)


# --- UBL 2.1 / OIOUBL ------------------------------------------------------

_UBL_NS = {
    "": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
}
def _q(prefix: str, tag: str) -> str:
    return f"{{{_UBL_NS[prefix]}}}{tag}"


def _sub(parent: ET.Element, prefix: str, tag: str, text: Optional[str] = None,
         attrib: Optional[Dict[str, str]] = None) -> ET.Element:
    el = ET.SubElement(parent, _q(prefix, tag), attrib or {})
    if text is not None:
        el.text = text
    return el


def to_ubl(invoice: CanonicalInvoice, oioubl: bool = False,
           profile_fields: Optional[ProfileSpec] = None) -> str:
    """Build a UBL 2.1 (or OIOUBL-flavoured) Invoice from canonical values.

    ``profile_fields``, when given, restricts which canonical values are emitted
    — a profile can choose to omit, say, the bank account from the UBL output.
    """
    for prefix, uri in _UBL_NS.items():
        ET.register_namespace(prefix, uri)

    allowed = None
    if profile_fields:
        allowed = {pf["canonical"] for pf in profile_fields}

    def val(key: str) -> Optional[str]:
        if allowed is not None and key not in allowed:
            return None
        return invoice.get(key)

    currency = invoice.currency
    root = ET.Element(_q("", "Invoice"))

    if oioubl:
        _sub(root, "cbc", "CustomizationID",
             "OIOUBL-2.02")
        _sub(root, "cbc", "ProfileID",
             "urn:www.nemhandel.dk:profiles:BiiCoreTrdm010:ver1.0",
             {"schemeID": "urn:oioubl:id:profileid-1.5"})

    _sub(root, "cbc", "ID", val("InvoiceNo") or "UNKNOWN")
    if val("InvoiceDate"):
        _sub(root, "cbc", "IssueDate", val("InvoiceDate"))
    if val("DueDate"):
        _sub(root, "cbc", "DueDate", val("DueDate"))
    _sub(root, "cbc", "InvoiceTypeCode", "380")
    _sub(root, "cbc", "DocumentCurrencyCode", currency)

    # Supplier (vendor) party.
    supplier = _sub(root, "cac", "AccountingSupplierParty")
    party = _sub(supplier, "cac", "Party")
    if val("VendorNo"):
        pid = _sub(party, "cac", "PartyIdentification")
        _sub(pid, "cbc", "ID", val("VendorNo"))
    if val("VendorName"):
        pname = _sub(party, "cac", "PartyName")
        _sub(pname, "cbc", "Name", val("VendorName"))

    # Payment means (bank account), when present.
    if val("AccountNo"):
        pm = _sub(root, "cac", "PaymentMeans")
        _sub(pm, "cbc", "PaymentMeansCode", "42")
        acct = _sub(pm, "cac", "PayeeFinancialAccount")
        _sub(acct, "cbc", "ID", val("AccountNo"))

    # Tax total.
    if val("Vat"):
        tax = _sub(root, "cac", "TaxTotal")
        _sub(tax, "cbc", "TaxAmount", val("Vat"), {"currencyID": currency})

    # Monetary totals.
    total = _sub(root, "cac", "LegalMonetaryTotal")
    if val("TotalExclVat"):
        _sub(total, "cbc", "TaxExclusiveAmount", val("TotalExclVat"),
             {"currencyID": currency})
    if val("TotalInclVat"):
        _sub(total, "cbc", "TaxInclusiveAmount", val("TotalInclVat"),
             {"currencyID": currency})
        _sub(total, "cbc", "PayableAmount", val("TotalInclVat"),
             {"currencyID": currency})

    # Invoice lines.
    for idx, ln in enumerate(invoice.lines, start=1):
        line_el = _sub(root, "cac", "InvoiceLine")
        _sub(line_el, "cbc", "ID", str(idx))
        if ln.quantity:
            attrib = {"unitCode": ln.unit} if ln.unit else None
            _sub(line_el, "cbc", "InvoicedQuantity", ln.quantity, attrib)
        if ln.amount:
            _sub(line_el, "cbc", "LineExtensionAmount", ln.amount,
                 {"currencyID": currency})
        item = _sub(line_el, "cac", "Item")
        if ln.description:
            _sub(item, "cbc", "Description", ln.description)
            _sub(item, "cbc", "Name", ln.description)
        if ln.unit_price:
            price = _sub(line_el, "cac", "Price")
            _sub(price, "cbc", "PriceAmount", ln.unit_price,
                 {"currencyID": currency})

    return _pretty(root)


# --- helpers ---------------------------------------------------------------

def _pretty(root: ET.Element) -> str:
    rough = ET.tostring(root, encoding="utf-8")
    return minidom.parseString(rough).toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")


_FORMATS = {
    "json": ("application/json", "json"),
    "xml": ("application/xml", "xml"),
    "ubl": ("application/xml", "xml"),
    "oioubl": ("application/xml", "xml"),
}


def render(invoice: CanonicalInvoice, fmt: str,
           profile_fields: Optional[ProfileSpec] = None) -> "RenderedExport":
    fmt = (fmt or "json").lower()
    if fmt == "json":
        body = to_json(invoice, profile_fields)
    elif fmt == "xml":
        body = to_xml(invoice, profile_fields)
    elif fmt == "ubl":
        body = to_ubl(invoice, oioubl=False, profile_fields=profile_fields)
    elif fmt == "oioubl":
        body = to_ubl(invoice, oioubl=True, profile_fields=profile_fields)
    else:
        raise ValueError(f"Unknown export format: {fmt!r}")
    media_type, ext = _FORMATS[fmt]
    return RenderedExport(body=body, media_type=media_type, extension=ext)


@dataclass
class RenderedExport:
    body: str
    media_type: str
    extension: str
