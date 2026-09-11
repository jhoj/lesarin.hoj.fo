"""Layout fingerprints and label harvesting (docs/brain-sync.md, "Stage B —
harvest locally" and "Matching: one V-tal is not one layout").

On every read, the heuristic extractor's suggestions already carry exactly
the **label-shaped tokens** it recognised (a small, known, multilingual
vocabulary — see ``app/config/labels.yaml``) and their positions — never a
value. This module turns that into two things:

* a **layout fingerprint** — a hash of the normalised, coarsely-quantised
  label positions, so a document's *shape* can be recognised even without a
  vendor identifier, and so a template can be keyed on
  ``vendor identifier + layout fingerprint`` rather than the identifier
  alone (one V-tal sends invoices, credit notes and statements, laid out
  differently),
* a **label observation** — each label plus which canonical field it sat
  beside (when known), which is what lets the shared vocabulary learn
  "Fakturanr generally means invoice number" independent of any one vendor.

Built only from recognised labels, never arbitrary page text, so this can't
leak an invoice number, an amount, or a name even by accident.
"""

from __future__ import annotations

import hashlib
from typing import List, Optional

from .extraction.loader import Document
from .models import FieldSuggestion

# Coarse grid for position quantisation: small layout drift (a millimetre of
# margin, a different renderer) shouldn't produce a different fingerprint.
_GRID = 0.05


def _quantise(frac: float) -> int:
    return round(frac / _GRID)


def build_fingerprint(
    document: Document, suggestions: List[FieldSuggestion], identifier: Optional[str] = None
) -> dict:
    """A ``{"identifier", "layout_fingerprint", "label_set", "positions"}``
    dict — what one document's read harvests, ready to queue locally
    (``app.repo.add_label_observation``) and later push centrally
    (``app.brain``).
    """
    page_by_index = {i + 1: p for i, p in enumerate(document.pages)}

    positions = []
    for s in suggestions:
        if not s.read_labels or not s.bbox or not s.page:
            continue
        page = page_by_index.get(s.page)
        if page is None or not page.width or not page.height:
            continue
        x0, top, x1, bottom = s.bbox
        cx, cy = (x0 + x1) / 2, (top + bottom) / 2
        positions.append({
            "label": s.read_labels[0].strip().lower(),
            "suggested_key": s.suggested_key,
            "x_frac": round(cx / page.width, 4),
            "y_frac": round(cy / page.height, 4),
        })

    label_set = sorted({p["label"] for p in positions})
    return {
        "identifier": identifier,
        "layout_fingerprint": layout_fingerprint(positions, n_pages=document.n_pages),
        "label_set": label_set,
        "positions": positions,
    }


def layout_fingerprint(positions: List[dict], n_pages: int = 1) -> str:
    """Hash the document's *shape*: which labels, roughly where, over how
    many pages — not their values, and not exact pixel positions."""
    shape = sorted(
        (p["label"], _quantise(p["x_frac"]), _quantise(p["y_frac"])) for p in positions
    )
    digest = hashlib.sha256(repr((n_pages, shape)).encode("utf-8")).hexdigest()
    return digest
