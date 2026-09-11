"""Layer 2 — "is this even an invoice?"

Extraction already tells us what it *located*; this asks the coarser
question first: does the document look like an invoice at all, before we
trust anything it found? A heuristic score (0–1) from three signals:

* **keyword density** — how many invoice-ish words (multilingual: Faroese,
  Danish, English) appear on the first page,
* **field coverage** — the fraction of canonical fields extraction located,
  reusing the same ``values``/``meta`` the rest of the pipeline already has,
* **a line-item table** — invoices almost always have one.

No ML model, no external service — the heuristics already locate most fields
label-free, so their success rate is itself a strong signal.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from ..extraction.loader import Document

# A deliberately small, multilingual set — enough to separate "an invoice" from
# "some other kind of document", not a full label dictionary (that's
# app/config/labels.yaml, used for field location, not document typing).
_INVOICE_KEYWORDS = {
    "faktura", "fakturanr", "fakturadato", "invoice", "invoiceno", "invoice-no",
    "rechnung", "regning", "bill", "faktúra",
}


def _keyword_hits(document: Document, max_pages: int = 1) -> int:
    hits = 0
    for page in document.pages[:max_pages]:
        for word in page.words:
            if word.text.strip(".:,").lower() in _INVOICE_KEYWORDS:
                hits += 1
    return hits


def score(
    document: Document,
    values: Dict[str, Optional[str]],
    has_line_items: bool,
) -> Dict[str, object]:
    """Return ``{"score": 0..1, "signals": [...]}``."""
    signals: List[str] = []

    keyword_hits = _keyword_hits(document)
    keyword_signal = min(keyword_hits / 2, 1.0)  # 2+ hits already maxes this out
    if keyword_hits:
        signals.append(f"{keyword_hits} invoice-like keyword(s) on page 1")

    key_fields = ["InvoiceNo", "InvoiceDate", "VendorName", "VendorNo"]
    found = sum(1 for k in key_fields if values.get(k))
    coverage_signal = found / len(key_fields)
    if found:
        signals.append(f"{found}/{len(key_fields)} key field(s) located")

    lines_signal = 1.0 if has_line_items else 0.0
    if has_line_items:
        signals.append("a line-item table was found")

    # Weighted blend: field coverage is the strongest signal (extraction
    # already validated these look like real values), keywords next, a line
    # table last (present on most invoices, but plenty of valid ones lack one).
    total = 0.5 * coverage_signal + 0.35 * keyword_signal + 0.15 * lines_signal
    return {"score": round(total, 2), "signals": signals}
