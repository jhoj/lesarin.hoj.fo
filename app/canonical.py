"""The canonical invoice vocabulary — the shared semantic field set.

This is the hinge of the SaaS. Three layers meet here:

* **Vendor templates** (shared centrally) map a vendor's invoice layout onto
  these canonical keys. Taught once by anyone, they work for everyone.
* **Output profiles** (per user) rename a subset of these canonical keys to the
  customer's own labels and pick an export format. The customer never sees the
  vendor template — they just pick the fields they want out.
* **Heuristics** (``extraction``) already locate most of these keys label-free,
  which is what lets a never-seen invoice "just work" the first time.

Keeping the vocabulary fixed and small is deliberate: it's the contract both the
central mapping store and every customer profile agree on.
"""

from __future__ import annotations

from typing import Dict, List, Optional

# Canonical key → (display name, value type). The keys match the ``suggested_key``
# values in app/config/labels.yaml so the heuristic suggester already speaks them.
CANONICAL_FIELDS: Dict[str, Dict[str, str]] = {
    "VendorName": {"display_name": "Vendor name", "value_type": "string"},
    "VendorNo": {"display_name": "Vendor number / V-tal", "value_type": "string"},
    "InvoiceNo": {"display_name": "Invoice number", "value_type": "string"},
    "InvoiceDate": {"display_name": "Invoice date", "value_type": "date"},
    "DueDate": {"display_name": "Due date", "value_type": "date"},
    "Currency": {"display_name": "Currency", "value_type": "string"},
    "TotalExclVat": {"display_name": "Total excl. VAT", "value_type": "number"},
    "Vat": {"display_name": "VAT amount", "value_type": "number"},
    "TotalInclVat": {"display_name": "Total incl. VAT", "value_type": "number"},
    "AccountNo": {"display_name": "Bank / account number", "value_type": "string"},
}

# Stable order for outputs and UI.
CANONICAL_ORDER: List[str] = list(CANONICAL_FIELDS.keys())


def value_type(key: str) -> str:
    spec = CANONICAL_FIELDS.get(key)
    return spec["value_type"] if spec else "string"


def display_name(key: str) -> str:
    spec = CANONICAL_FIELDS.get(key)
    return spec["display_name"] if spec else key


def is_canonical(key: str) -> bool:
    return key in CANONICAL_FIELDS


def sort_key(key: str) -> int:
    try:
        return CANONICAL_ORDER.index(key)
    except ValueError:
        return len(CANONICAL_ORDER)
