"""Template-driven extraction: apply a vendor's saved mappings to a document.

Where ``fields.py`` *guesses* across any layout, this module *follows* a taught
template — for each output field the vendor config says either:

* **label** — find a source label (e.g. "Veitara nr.") on the page and read the
  value beside/below it. Robust to small vertical shifts.
* **region** — read whatever words fall inside a fixed box on the page.

Both reuse the positioned-word machinery from ``fields.py`` so results carry the
same ``Field`` shape (value + bbox + confidence). Output fields without a mapping
aren't guessed here; instead :func:`suggestions` offers heuristic candidates the
user can turn into mappings.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from . import dates
from . import fields as heuristic
from .fields import group_lines, Line, _bbox_of, _match_label_on_line, _value_below, _value_to_right
from .loader import Document, Word
from ..models import Field, MappingIn, ReadField, Suggestion, TemplateIn


def document_text(document: Document) -> str:
    """Flatten the document to text (for vendor detection)."""
    return "\n".join(
        " ".join(w.text for w in page.words) for page in document.pages
    )


def _normalise_number(text: str) -> Optional[str]:
    """Turn "1.234,50" / "1,234.50" / "90,00" into a plain decimal string."""
    cleaned = re.sub(r"[^0-9.,-]", "", text)
    if not re.search(r"\d", cleaned):
        return None
    if "," in cleaned and "." in cleaned:
        # The rightmost separator is the decimal point.
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    return cleaned


def _coerce(raw: str, value_type: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (value, raw_original) for the given type, or (None, _) if invalid."""
    if value_type == "date":
        date_text = dates.find_date_text(raw) or raw
        norm = dates.normalise(date_text)
        return (norm, date_text) if norm else (None, date_text)
    if value_type == "number":
        return _normalise_number(raw), raw
    return raw.strip() or None, None


def _read_by_label(
    pages_lines: List[Tuple[int, List[Line]]], mapping: MappingIn
) -> Field:
    if not mapping.label:
        return Field.empty()
    want_date = mapping.value_type == "date"
    labels = [mapping.label]
    for page_no, lines in pages_lines:
        if mapping.page and page_no != mapping.page:
            continue
        for idx, line in enumerate(lines):
            match = _match_label_on_line(line, labels)
            if not match:
                continue
            start, end, matched = match
            value = None
            base_conf = 0.0
            if mapping.relation == "right":
                value = _value_to_right(line, end, want_date)
                base_conf = 0.9
            if value is None:  # try below as a fallback (or when relation=below)
                label_words = line.words[start:end]
                value = _value_below(
                    lines, idx,
                    min(w.x0 for w in label_words), max(w.x1 for w in label_words),
                    want_date,
                )
                base_conf = 0.6 if mapping.relation == "right" else 0.85
            if value is None:
                continue
            raw, bbox, word_conf = value
            coerced, orig = _coerce(raw, mapping.value_type)
            if coerced is None:
                continue
            return Field(
                value=coerced,
                raw=orig,
                page=page_no,
                bbox=bbox,
                confidence=round(base_conf * (word_conf if word_conf > 0 else 1.0), 3),
                source_label=matched,
            )
    return Field.empty()


def _words_in_box(document: Document, page: int, bbox: List[float]) -> List[Word]:
    x0, top, x1, bottom = bbox
    for p in document.pages:
        if p.page_number != page:
            continue
        inside = [
            w for w in p.words
            if x0 - 1 <= w.cx <= x1 + 1 and top - 1 <= w.cy <= bottom + 1
        ]
        return sorted(inside, key=lambda w: (round(w.top, 1), w.x0))
    return []


def _read_by_region(document: Document, mapping: MappingIn) -> Field:
    if not mapping.bbox or mapping.page is None:
        return Field.empty()
    words = _words_in_box(document, mapping.page, mapping.bbox)
    if not words:
        return Field.empty()
    raw = " ".join(w.text for w in words)
    coerced, orig = _coerce(raw, mapping.value_type)
    if coerced is None:
        return Field.empty()
    word_conf = min((w.confidence for w in words), default=1.0)
    return Field(
        value=coerced,
        raw=orig,
        page=mapping.page,
        bbox=_bbox_of(words),
        confidence=round(0.85 * (word_conf if word_conf > 0 else 1.0), 3),
        source_label=None,
    )


def read_mapping(
    document: Document, pages_lines: List[Tuple[int, List[Line]]], mapping: MappingIn
) -> ReadField:
    if mapping.strategy == "region":
        field = _read_by_region(document, mapping)
        source = "template-region" if field.found else "none"
    else:
        field = _read_by_label(pages_lines, mapping)
        source = "template-label" if field.found else "none"
    return ReadField(output=mapping.output, source=source, **field.model_dump())


def apply_template(document: Document, template: TemplateIn) -> List[ReadField]:
    pages_lines: List[Tuple[int, List[Line]]] = [
        (p.page_number, group_lines(p.words)) for p in document.pages
    ]
    return [read_mapping(document, pages_lines, m) for m in template.fields]


def suggestions(document: Document, config: Optional[dict] = None) -> List[Suggestion]:
    """Heuristic candidates (value + position) to help the user map fields."""
    guess = heuristic.extract(document, config=config)
    out: List[Suggestion] = []
    if guess.invoiceno.found:
        out.append(Suggestion(kind="invoiceno", field=guess.invoiceno))
    if guess.sentdate.found:
        out.append(Suggestion(kind="date", field=guess.sentdate))
    if guess.paydate.found:
        out.append(Suggestion(kind="date", field=guess.paydate))
    if guess.vendor.name.found:
        out.append(Suggestion(kind="vendor_name", field=guess.vendor.name))
    return out
