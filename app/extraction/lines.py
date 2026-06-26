"""Line-item parsing from word positions (header-anchored).

pdfplumber's ruled-table detection (``find_tables``) returns nothing on the many
invoices that draw no cell borders. Instead we locate the column *header* by its
keywords, turn the header word x-positions into column **bands**, then read every
data row beneath it — across pages, for however many rows exist — until a totals
*terminator*. Rows that carry no amount (wrapped descriptions, sub-codes) fold
into the nearest item. Each value keeps its bbox so the frontend can point at it.

Line counts vary per invoice and can run for pages, so nothing here is mapped to
a fixed region: the global column dictionary in ``labels.yaml`` drives detection
the same way for every vendor.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Dict, List, Optional

from .fields import Line, group_lines, _bbox_of, _norm
from .loader import Document, Word
from ..models import Field, LineItem

_CANON = ("description", "quantity", "unit", "unit_price", "amount")
_DEFAULT_TERMINATORS = ("i alt", "tilsamans", "samanlagt", "subtotal", "total", "moms", "mvg")


def _has_digit(text: str) -> bool:
    return any(ch.isdigit() for ch in text)


def _center(w: Word) -> float:
    return (w.x0 + w.x1) / 2.0


def _column_canon(text: str, columns_cfg: dict) -> Optional[str]:
    """Map a header word to a canonical column name via keyword match, or None."""
    nw = _norm(text)
    if not nw:
        return None
    for canon in _CANON:
        for kw in columns_cfg.get(canon, []):
            k = _norm(kw)
            if k and (k == nw or k in nw or nw in k):
                return canon
    return None


def _header_columns(line: Line, columns_cfg: dict) -> Optional[List[tuple]]:
    """Return ``[(x_center, canon|None), ...]`` (x-sorted) if ``line`` is the header.

    Every header word is kept — even unmapped ones like "Eind"/"Avsláttur" — so
    the column bands line up; we just require at least two *mapped* columns.
    """
    cols = [(_center(w), _column_canon(w.text, columns_cfg)) for w in line.words]
    if sum(1 for _, c in cols if c) < 2:
        return None
    cols.sort(key=lambda c: c[0])
    return cols


def _band_index(x: float, centers: List[float]) -> int:
    """Which column band ``x`` falls in (boundaries at midpoints between centers)."""
    for i in range(len(centers) - 1):
        if x < (centers[i] + centers[i + 1]) / 2.0:
            return i
    return len(centers) - 1


@dataclass
class _Item:
    page: int = 1
    desc_words: List[Word] = dc_field(default_factory=list)
    qty: Optional[Word] = None
    unit: Optional[Word] = None
    unit_price: Optional[Word] = None
    amount: Optional[Word] = None

    def _desc_field(self) -> Field:
        if not self.desc_words:
            return Field.empty()
        ws = sorted(self.desc_words, key=lambda w: (round(w.top, 1), w.x0))
        return Field(value=" ".join(w.text for w in ws), page=self.page, bbox=_bbox_of(ws), confidence=0.7)

    @staticmethod
    def _one(w: Optional[Word], page: int) -> Field:
        return Field(value=w.text, page=page, bbox=list(w.bbox), confidence=0.7) if w else Field.empty()

    def to_lineitem(self) -> LineItem:
        return LineItem(
            description=self._desc_field(),
            quantity=self._one(self.qty, self.page),
            unit=self._one(self.unit, self.page),
            unit_price=self._one(self.unit_price, self.page),
            amount=self._one(self.amount, self.page),
        )


def _pick(buckets: Dict[int, List[Word]], canon: List[Optional[str]], centers: List[float], target: str) -> Optional[Word]:
    """Best word in the band(s) for ``target``: prefer numeric, nearest band center."""
    cand = [(i, w) for i, c in enumerate(canon) if c == target for w in buckets.get(i, [])]
    numeric = [(i, w) for i, w in cand if _has_digit(w.text)]
    use = numeric or cand
    if not use:
        return None
    return min(use, key=lambda iw: abs(_center(iw[1]) - centers[iw[0]]))[1]


def extract_line_items(document: Document, config: dict) -> List[LineItem]:
    columns_cfg = config.get("lines", {}).get("columns", {})
    term_cfg = config.get("lines", {}).get("terminators")
    terminators = [_norm(t) for t in term_cfg] if term_cfg else list(_DEFAULT_TERMINATORS)

    out: List[LineItem] = []
    carried_cols: Optional[List[tuple]] = None
    for page in document.pages:
        plines = group_lines(page.words)

        cols = None
        start = 0
        for i, ln in enumerate(plines):
            found = _header_columns(ln, columns_cfg)
            if found:
                cols, start = found, i + 1
                break
        if cols is None:
            # Continuation page: the table runs on without re-printing its
            # header. Reuse the previous page's columns so its rows aren't lost.
            if carried_cols is None:
                continue
            cols, start = carried_cols, 0
        else:
            carried_cols = cols
        centers = [c[0] for c in cols]
        canon = [c[1] for c in cols]
        footer_top = page.height * 0.92  # ignore the page-footer band

        anchors: List[_Item] = []
        anchor_cys: List[float] = []
        continuations: List[Line] = []

        for ln in plines[start:]:
            if ln.top > footer_top:
                break  # into the page footer
            ntext = _norm(ln.text)
            if any(t and t in ntext for t in terminators):
                break  # reached the totals/summary block
            buckets: Dict[int, List[Word]] = {}
            for w in ln.words:
                buckets.setdefault(_band_index(_center(w), centers), []).append(w)

            amount = _pick(buckets, canon, centers, "amount")
            if amount is not None and _has_digit(amount.text):
                item = _Item(page=page.page_number, amount=amount)
                item.qty = _pick(buckets, canon, centers, "quantity")
                item.unit = _pick(buckets, canon, centers, "unit")
                item.unit_price = _pick(buckets, canon, centers, "unit_price")
                item.desc_words = [w for idx, ws in buckets.items() if canon[idx] == "description" for w in ws]
                anchors.append(item)
                anchor_cys.append(ln.cy)
            elif anchors:
                # Amount-less row below an item = wrapped description. Rows above
                # the first item are header captions/units (e.g. "(DKK)") — skip.
                continuations.append(ln)

        # Fold amount-less rows (wrapped descriptions) into the nearest item.
        for ln in continuations:
            if not anchors:
                continue
            j = min(range(len(anchors)), key=lambda k: abs(anchor_cys[k] - ln.cy))
            anchors[j].desc_words.extend(ln.words)

        out.extend(item.to_lineitem() for item in anchors)

    return out
