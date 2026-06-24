"""API response models.

Every extracted scalar is a :class:`Field` carrying not just the value but
**where** it was found (page + bbox) and how sure we are. That makes the
service usable as a first-pass helper: a frontend can highlight located values
and flag the empty ones for a human to fill in.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field as PydField


class Field(BaseModel):
    value: Optional[str] = None
    raw: Optional[str] = None  # original text before normalisation (e.g. dates)
    page: Optional[int] = None  # 1-indexed
    bbox: Optional[List[float]] = None  # [x0, top, x1, bottom] in PDF points
    confidence: float = 0.0
    source_label: Optional[str] = None  # the label phrase that located the value

    @classmethod
    def empty(cls) -> "Field":
        return cls()

    @property
    def found(self) -> bool:
        return self.value is not None


class Vendor(BaseModel):
    name: Field = PydField(default_factory=Field.empty)


class LineItem(BaseModel):
    description: Field = PydField(default_factory=Field.empty)
    quantity: Field = PydField(default_factory=Field.empty)
    unit_price: Field = PydField(default_factory=Field.empty)
    amount: Field = PydField(default_factory=Field.empty)


class Meta(BaseModel):
    filename: Optional[str] = None
    pages: int = 0
    ocr_used: bool = False
    fields_found: int = 0
    fields_total: int = 0


class InvoiceResult(BaseModel):
    invoiceno: Field = PydField(default_factory=Field.empty)
    vendor: Vendor = PydField(default_factory=Vendor)
    sentdate: Field = PydField(default_factory=Field.empty)
    paydate: Field = PydField(default_factory=Field.empty)
    lines: List[LineItem] = PydField(default_factory=list)
    meta: Meta = PydField(default_factory=Meta)
