"""Validation: do the extracted values hold together as an invoice?"""

from __future__ import annotations

from app.exporters import CanonicalLine
from app.validation import validate


def _values(**overrides):
    base = {
        "VendorName": "Effo P/F",
        "VendorNo": "314188",
        "InvoiceNo": "2026-0014",
        "InvoiceDate": "2026-01-12",
        "DueDate": "2026-01-26",
        "Currency": "DKK",
        "TotalExclVat": "132,00",
        "Vat": "33,00",
        "TotalInclVat": "165,00",
    }
    base.update(overrides)
    return base


def _lines():
    return [
        CanonicalLine(description="Kaffi", quantity="2", amount="90,00"),
        CanonicalLine(description="Mjólk", quantity="6", amount="42,00"),
    ]


def test_consistent_invoice_is_valid():
    result = validate(_values(), _lines())
    assert result["valid"], result["problems"]
    ran = {c["check"] for c in result["checks"]}
    assert {"totals_reconcile", "lines_sum_to_total", "due_not_before_issue"} <= ran


def test_totals_that_do_not_reconcile_fail():
    result = validate(_values(TotalInclVat="200,00"), _lines())
    assert not result["valid"]
    assert any("gross" in p for p in result["problems"])


def test_lines_not_summing_to_net_fail():
    result = validate(_values(), [CanonicalLine(description="X", amount="10,00")])
    assert not result["valid"]
    assert any(c["check"] == "lines_sum_to_total" and not c["ok"] for c in result["checks"])


def test_due_before_issue_fails():
    result = validate(_values(DueDate="2026-01-01"), _lines())
    assert any(c["check"] == "due_not_before_issue" and not c["ok"] for c in result["checks"])


def test_missing_totals_skip_arithmetic_but_stay_valid():
    values = _values()
    for key in ("TotalExclVat", "Vat", "TotalInclVat"):
        values.pop(key)
    result = validate(values, [])
    ran = {c["check"] for c in result["checks"]}
    assert "totals_reconcile" not in ran and "lines_sum_to_total" not in ran
    assert result["valid"]


def test_missing_invoice_number_is_a_problem():
    values = _values()
    values.pop("InvoiceNo")
    result = validate(values, _lines())
    assert not result["valid"]
    assert any(c["check"] == "invoice_number_present" and not c["ok"] for c in result["checks"])


def test_tolerates_line_rounding():
    # Two lines of 33,333 rounded to 33,33 each: sum 66,66 vs net 66,67.
    values = _values(TotalExclVat="66,67", Vat="0,00", TotalInclVat="66,67")
    lines = [CanonicalLine(amount="33,33"), CanonicalLine(amount="33,33")]
    result = validate(values, lines)
    assert all(c["ok"] for c in result["checks"] if c["check"] == "lines_sum_to_total")
