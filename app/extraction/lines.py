"""Line-item table detection and column mapping.

We rely on pdfplumber's positioned tables (from the loader). A table qualifies
as the line-item table when its header row matches at least two known column
keywords (e.g. a description column and an amount column). Each cell keeps its
bounding box, so individual line-item values are locatable too.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from .loader import Document, Table, TableCell
from ..models import Field, LineItem

_CANON = ("description", "quantity", "unit_price", "amount")


def _norm(text: str) -> str:
    return re.sub(r"[^0-9a-zà-ÿ]+", " ", (text or "").lower(), flags=re.IGNORECASE).strip()


def _header_mapping(header_cells: List[TableCell], columns_cfg: dict) -> Dict[int, str]:
    """Map column index -> canonical name using header keywords."""
    mapping: Dict[int, str] = {}
    for cell in header_cells:
        norm = _norm(cell.text)
        if not norm:
            continue
        for canon in _CANON:
            keywords = [_norm(k) for k in columns_cfg.get(canon, [])]
            if any(kw and (kw in norm or norm in kw) for kw in keywords):
                mapping.setdefault(cell.col, canon)
                break
    return mapping


def _cells_by_row(table: Table) -> Dict[int, List[TableCell]]:
    rows: Dict[int, List[TableCell]] = {}
    for cell in table.cells:
        rows.setdefault(cell.row, []).append(cell)
    return rows


def _cell_field(cell: Optional[TableCell]) -> Field:
    if cell is None or not cell.text.strip():
        return Field.empty()
    return Field(
        value=cell.text.strip(),
        page=cell.page,
        bbox=list(cell.bbox),
        confidence=0.8,
    )


def extract_line_items(document: Document, config: dict) -> List[LineItem]:
    columns_cfg = config.get("lines", {}).get("columns", {})
    best: List[LineItem] = []

    for page in document.pages:
        for table in page.tables:
            rows = _cells_by_row(table)
            if not rows:
                continue
            header = rows.get(0, [])
            mapping = _header_mapping(header, columns_cfg)
            if len(mapping) < 2:
                continue

            items: List[LineItem] = []
            for r_idx in sorted(rows):
                if r_idx == 0:
                    continue
                by_col = {c.col: c for c in rows[r_idx]}
                picked = {
                    canon: _cell_field(by_col.get(col_idx))
                    for col_idx, canon in mapping.items()
                }
                item = LineItem(
                    description=picked.get("description", Field.empty()),
                    quantity=picked.get("quantity", Field.empty()),
                    unit_price=picked.get("unit_price", Field.empty()),
                    amount=picked.get("amount", Field.empty()),
                )
                if any(
                    f.found for f in (item.description, item.quantity, item.unit_price, item.amount)
                ):
                    items.append(item)

            # Prefer the table that yields the most line items.
            if len(items) > len(best):
                best = items

    return best
