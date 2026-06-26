"""Heuristic, layout-aware extraction of scalar invoice fields.

Strategy: invoices vary wildly in layout but share a small vocabulary of
*labels* ("Fakturanr", "Forfaldsdato", ...). We find a label among the
positioned words, then read the value that sits next to it (to the right, or on
the line below). Because we work from word positions, every result carries its
bounding box — so the service can point at where it found things.

This generalises across unknown layouts without per-vendor templates, at the
cost of being best-effort: when a label isn't found the field comes back empty
rather than guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from . import dates
from .loader import Document, Word
from ..models import Field, InvoiceResult, Meta, Vendor

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "labels.yaml"

# Value token must look like an invoice number: digits, optionally with
# separators and a letter prefix/suffix (e.g. "2026-0014", "INV0014").
_INVOICENO_RE = re.compile(r"^[A-Za-z]{0,4}[-/]?\d[\w\-/]*$")


@dataclass
class Line:
    """Words sharing a horizontal band, ordered left-to-right."""

    words: List[Word]
    top: float
    bottom: float

    @property
    def cy(self) -> float:
        return (self.top + self.bottom) / 2

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


def load_config(path: Optional[Path] = None) -> dict:
    with open(path or _CONFIG_PATH, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _norm(text: str) -> str:
    """Lower-case and collapse non-alphanumeric runs to single spaces."""
    return re.sub(r"[^0-9a-zà-ÿ]+", " ", text.lower(), flags=re.IGNORECASE).strip()


def group_lines(words: List[Word]) -> List[Line]:
    """Cluster words into visual lines by their vertical centre."""
    if not words:
        return []
    ordered = sorted(words, key=lambda w: (round(w.top, 1), w.x0))
    heights = sorted(w.bottom - w.top for w in ordered)
    median_h = heights[len(heights) // 2] or 8.0
    tol = max(median_h * 0.6, 3.0)

    lines: List[Line] = []
    for w in ordered:
        placed = False
        for line in lines:
            if abs(w.cy - line.cy) <= tol:
                line.words.append(w)
                line.top = min(line.top, w.top)
                line.bottom = max(line.bottom, w.bottom)
                placed = True
                break
        if not placed:
            lines.append(Line(words=[w], top=w.top, bottom=w.bottom))
    for line in lines:
        line.words.sort(key=lambda w: w.x0)
    lines.sort(key=lambda ln: ln.top)
    return lines


def _bbox_of(words: List[Word]) -> List[float]:
    return [
        min(w.x0 for w in words),
        min(w.top for w in words),
        max(w.x1 for w in words),
        max(w.bottom for w in words),
    ]


def _match_label_on_line(line: Line, labels: List[str]) -> Optional[Tuple[int, int, str]]:
    """Find a label phrase as a window of consecutive words on ``line``.

    Returns ``(start_idx, end_idx_exclusive, matched_label)`` or ``None``.
    Longer labels are tried first so "faktura nr" wins over "faktura".
    """
    norm_words = [_norm(w.text) for w in line.words]
    # Separator-free form so a label like "vtal" matches "V-tal" (which _norm
    # turns into the two-token string "v tal").
    squished = [w.replace(" ", "") for w in norm_words]
    for label in sorted(labels, key=len, reverse=True):
        nlabel = _norm(label)
        ltokens = nlabel.split()
        lsquish = nlabel.replace(" ", "")
        n = len(ltokens)
        for start in range(0, len(norm_words) - n + 1):
            window = norm_words[start : start + n]
            if window == ltokens:
                return start, start + n, label
            # Handle "Fakturanr:12345" glued into one token.
            if n == 1 and norm_words[start].startswith(ltokens[0]) and len(norm_words[start]) > len(
                ltokens[0]
            ):
                return start, start + 1, label
            # Handle a separator variant ("vtal" ↔ "V-tal" → "v tal").
            if squished[start] == lsquish and lsquish:
                return start, start + 1, label
    return None


def _value_to_right(
    line: Line, label_end_idx: int, want_date: bool
) -> Optional[Tuple[str, List[float], float]]:
    rest = line.words[label_end_idx:]
    if not rest:
        return None
    if want_date:
        joined = " ".join(w.text for w in rest)
        date_text = dates.find_date_text(joined)
        if not date_text:
            return None
        # Tokens overlapping the date text form the value bbox.
        used = [w for w in rest if w.text in date_text.split() or w.text in date_text]
        used = used or [rest[0]]
        conf = min((w.confidence for w in used), default=1.0)
        return date_text, _bbox_of(used), conf
    # token value: first token that looks like a value, not another word label
    for w in rest:
        token = w.text.strip(":#.,")
        if token and _INVOICENO_RE.match(token):
            return token, list(w.bbox), w.confidence
    # fallback: first non-empty token
    first = rest[0]
    token = first.text.strip(":#.,")
    if token:
        return token, list(first.bbox), first.confidence
    return None


def _value_below(
    lines: List[Line], line_idx: int, label_x0: float, label_x1: float, want_date: bool
) -> Optional[Tuple[str, List[float], float]]:
    if line_idx + 1 >= len(lines):
        return None
    below = lines[line_idx + 1]
    # words roughly under the label column
    under = [w for w in below.words if w.x1 >= label_x0 - 5 and w.x0 <= label_x1 + 60]
    if not under:
        return None
    if want_date:
        joined = " ".join(w.text for w in under)
        date_text = dates.find_date_text(joined)
        if not date_text:
            return None
        return date_text, _bbox_of(under), min((w.confidence for w in under), default=1.0)
    first = under[0]
    token = first.text.strip(":#.,")
    if token:
        return token, list(first.bbox), first.confidence
    return None


def _extract_scalar(
    pages_lines: List[Tuple[int, List[Line]]], cfg: dict
) -> Field:
    labels = cfg["labels"]
    direction = cfg.get("direction", "right")
    want_date = cfg.get("type") == "date"

    for page_no, lines in pages_lines:
        for idx, line in enumerate(lines):
            match = _match_label_on_line(line, labels)
            if not match:
                continue
            start, end, matched_label = match
            value = None
            base_conf = 0.0
            if direction in ("right", "any"):
                value = _value_to_right(line, end, want_date)
                base_conf = 0.9
            if value is None and direction in ("below", "any"):
                label_words = line.words[start:end]
                value = _value_below(
                    lines, idx, min(w.x0 for w in label_words), max(w.x1 for w in label_words), want_date
                )
                base_conf = 0.6
            if value is None:
                continue
            raw, bbox, word_conf = value
            norm_value = dates.normalise(raw) if want_date else raw
            if want_date and norm_value is None:
                continue
            return Field(
                value=norm_value,
                raw=raw if want_date else None,
                page=page_no,
                bbox=bbox,
                confidence=round(base_conf * (word_conf if word_conf > 0 else 1.0), 3),
                source_label=matched_label,
            )
    return Field.empty()


_CURRENCY_CODES = {"dkk", "isk", "eur", "usd", "nok", "sek", "gbp"}
_CURRENCY_SYMBOLS = {"€": "EUR", "$": "USD", "£": "GBP"}


def detect_currency(document: Document) -> Optional[str]:
    """Best-effort document currency from an ISO code, a symbol, or a "kr" token.

    Often printed as a column caption like "(DKK)" or beside the totals. In a
    Faroese/Danish context a bare "kr" means DKK.
    """
    for page in document.pages:
        for w in page.words:
            token = _norm(w.text)  # strips punctuation like the "(DKK)" parens
            if token in _CURRENCY_CODES:
                return token.upper()
            if token == "kr":
                return "DKK"
            for symbol, code in _CURRENCY_SYMBOLS.items():
                if symbol in w.text:
                    return code
    return None


def _extract_vendor(document: Document, cfg: dict) -> Vendor:
    vendor_cfg = cfg.get("vendor", {})
    buyer_keywords = [_norm(k) for k in vendor_cfg.get("buyer_keywords", [])]

    if not document.pages:
        return Vendor()
    first = document.pages[0]
    lines = group_lines(first.words)
    if not lines:
        return Vendor()

    def is_buyerish(text: str) -> bool:
        nt = _norm(text)
        return any(k in nt for k in buyer_keywords)

    # Heuristic: the supplier name is usually the first prominent text line at
    # the top of page 1 that has alphabetic content and isn't the buyer block.
    top_region = [ln for ln in lines if ln.top <= first.height * 0.35]
    for line in top_region:
        text = line.text.strip()
        letters = sum(c.isalpha() for c in text)
        if letters < 3:
            continue
        if is_buyerish(text):
            continue
        if dates.looks_like_date(text):
            continue
        conf = min((w.confidence for w in line.words), default=1.0)
        return Vendor(
            name=Field(
                value=text,
                page=first.page_number,
                bbox=_bbox_of(line.words),
                confidence=round(0.35 * (conf if conf > 0 else 1.0), 3),
                source_label="top-of-page heuristic",
            )
        )
    return Vendor()


def extract(document: Document, filename: Optional[str] = None, config: Optional[dict] = None) -> InvoiceResult:
    cfg = config or load_config()
    pages_lines: List[Tuple[int, List[Line]]] = [
        (page.page_number, group_lines(page.words)) for page in document.pages
    ]

    fields_cfg = cfg["fields"]
    invoiceno = _extract_scalar(pages_lines, fields_cfg["invoiceno"])
    sentdate = _extract_scalar(pages_lines, fields_cfg["sentdate"])
    paydate = _extract_scalar(pages_lines, fields_cfg["paydate"])
    vendor = _extract_vendor(document, cfg)

    # Avoid sentdate and paydate collapsing onto the same value/label.
    if (
        sentdate.found
        and paydate.found
        and sentdate.value == paydate.value
        and sentdate.source_label == paydate.source_label
    ):
        paydate = Field.empty()

    found = sum(
        1 for f in (invoiceno, sentdate, paydate, vendor.name) if f.found
    )
    meta = Meta(
        filename=filename,
        pages=document.n_pages,
        ocr_used=document.ocr_used,
        fields_found=found,
        fields_total=4,
    )

    return InvoiceResult(
        invoiceno=invoiceno,
        vendor=vendor,
        sentdate=sentdate,
        paydate=paydate,
        lines=[],  # populated by lines.extract_line_items in the service layer
        meta=meta,
    )
