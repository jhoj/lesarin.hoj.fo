"""Invoice validation — does the extracted data hold together as an invoice?

Extraction answers *"what does the document say?"*; this module answers *"do
those values make sense together?"*. Checks are arithmetic and structural:

* totals reconcile (net + VAT = gross),
* line amounts sum to the net (or gross) total,
* the due date isn't before the issue date,
* an invoice number and a vendor identity were found,
* currency and V-tal shapes look sane.

Each check only runs when the data it needs is present — a missing total is a
*completeness* problem (the CLI's status already covers that), not an
*arithmetic* one. The result is a report, not a verdict on extraction quality:
``valid`` means "no check that could run has failed".

Amounts on invoices are rounded per line, so comparisons use a small absolute
tolerance rather than exact equality.
"""

from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional

from ..exporters import CanonicalLine
from ..extraction.template import _normalise_number

# Rounding slack: per-line øre/cent rounding accumulates, so allow a few cents.
_TOLERANCE = 0.05

_ISO_CURRENCIES = {"DKK", "ISK", "EUR", "USD", "NOK", "SEK", "GBP"}

# Modulus-11 check digit weights for an 8-digit Danish CVR / Faroese V-tal —
# the Faroe Islands historically shares Denmark's business-registry numbering.
# Best-effort: undocumented publicly for V-tal specifically, so this is a soft
# check (only run, never fails a "complete" status on its own).
_CVR_WEIGHTS = [2, 7, 6, 5, 4, 3, 2, 1]


def vtal_checksum_ok(digits: str) -> Optional[bool]:
    """Modulus-11 check on an 8-digit V-tal/CVR. None if not applicable."""
    if len(digits) != 8 or not digits.isdigit():
        return None
    total = sum(int(d) * w for d, w in zip(digits, _CVR_WEIGHTS))
    return total % 11 == 0


def _num(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    normalised = _normalise_number(str(value))
    if normalised is None:
        return None
    try:
        return float(normalised)
    except ValueError:
        return None


def _date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _check(checks: List[dict], name: str, ok: bool, detail: str) -> None:
    checks.append({"check": name, "ok": ok, "detail": detail})


def validate(values: Dict[str, Optional[str]], lines: List[CanonicalLine]) -> dict:
    """Run every applicable check against the canonical values + line items.

    Returns ``{"valid": bool, "checks": [...], "problems": [...]}`` where
    ``checks`` lists each check that ran and ``problems`` the failing details.
    """
    checks: List[dict] = []

    net = _num(values.get("TotalExclVat"))
    vat = _num(values.get("Vat"))
    gross = _num(values.get("TotalInclVat"))

    # -- identity ------------------------------------------------------------
    _check(checks, "invoice_number_present", bool(values.get("InvoiceNo")),
           "invoice number located" if values.get("InvoiceNo") else "no invoice number found")
    vendor_known = bool(values.get("VendorNo") or values.get("VendorName"))
    _check(checks, "vendor_identified", vendor_known,
           "vendor located" if vendor_known else "neither vendor number nor name found")

    vendor_no = values.get("VendorNo")
    if vendor_no is not None:
        digits = "".join(c for c in str(vendor_no) if c.isdigit())
        ok = 4 <= len(digits) <= 12
        _check(checks, "vendor_number_shape", ok,
               f"vendor number '{vendor_no}' has {len(digits)} digits"
               + ("" if ok else " (expected 4–12)"))
        checksum = vtal_checksum_ok(digits)
        if checksum is not None:
            # Informational only (undocumented algorithm for V-tal specifically) —
            # never demotes a "complete" status on its own.
            checks.append({"check": "vtal_checksum", "ok": checksum, "detail":
                            f"CVR/V-tal modulus-11 check {'passed' if checksum else 'failed'}",
                            "informational": True})

    # -- arithmetic ----------------------------------------------------------
    if net is not None and vat is not None and gross is not None:
        ok = abs((net + vat) - gross) <= _TOLERANCE
        _check(checks, "totals_reconcile", ok,
               f"net {net:.2f} + VAT {vat:.2f} = {net + vat:.2f} vs gross {gross:.2f}")

    line_amounts = [_num(ln.amount) for ln in lines]
    known = [a for a in line_amounts if a is not None]
    target = net if net is not None else gross
    if known and target is not None and len(known) == len(line_amounts):
        total = sum(known)
        ok = abs(total - target) <= _TOLERANCE + 0.01 * len(known)
        _check(checks, "lines_sum_to_total", ok,
               f"{len(known)} line(s) sum to {total:.2f} vs total {target:.2f}")

    # -- dates ---------------------------------------------------------------
    issued = _date(values.get("InvoiceDate"))
    due = _date(values.get("DueDate"))
    if issued and due:
        _check(checks, "due_not_before_issue", due >= issued,
               f"issued {issued.isoformat()}, due {due.isoformat()}")

    # -- shapes --------------------------------------------------------------
    currency = values.get("Currency")
    if currency:
        code = str(currency).strip().upper()
        ok = code in _ISO_CURRENCIES or (len(code) == 3 and code.isalpha())
        _check(checks, "currency_code", ok, f"currency '{currency}'")

    problems = [c["detail"] for c in checks if not c["ok"] and not c.get("informational")]
    return {"valid": not problems, "checks": checks, "problems": problems}
