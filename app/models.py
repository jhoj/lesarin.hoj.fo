"""API response models.

Every extracted scalar is a :class:`Field` carrying not just the value but
**where** it was found (page + bbox) and how sure we are. That makes the
service usable as a first-pass helper: a frontend can highlight located values
and flag the empty ones for a human to fill in.
"""

from __future__ import annotations

from typing import List, Literal, Optional

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


# --- Template / vendor-config API models -----------------------------------

Strategy = Literal["label", "region"]
Relation = Literal["right", "below"]
ValueType = Literal["string", "date", "number"]


class OutputFieldIn(BaseModel):
    """An expected output field in the customer's setup table."""

    key: str
    display_name: str = ""
    value_type: ValueType = "string"
    sort_order: int = 0


class OutputFieldOut(OutputFieldIn):
    pass


class MappingIn(BaseModel):
    """How to locate one output field's value on a vendor's invoice."""

    output: str  # the OutputField.key this fills
    strategy: Strategy = "label"
    label: Optional[str] = None  # source label text, e.g. "Veitara nr."
    relation: Relation = "right"
    value_type: ValueType = "string"
    page: Optional[int] = None
    bbox: Optional[List[float]] = None  # [x0, top, x1, bottom] in PDF points


class VendorIn(BaseModel):
    identifier: str  # V-tal, e.g. "314188"
    name: str  # friendly name, e.g. "Effo"
    identifier_kind: str = "vtal"
    match_keywords: List[str] = PydField(default_factory=list)
    mappings: List[MappingIn] = PydField(default_factory=list)


class VendorOut(BaseModel):
    id: int
    identifier: str
    name: str
    identifier_kind: str = "vtal"
    match_keywords: List[str] = PydField(default_factory=list)
    mappings: List[MappingIn] = PydField(default_factory=list)


class TemplateIn(BaseModel):
    """The (possibly unsaved) set of mappings the editor wants applied now."""

    fields: List[MappingIn] = PydField(default_factory=list)


class ReadField(Field):
    """A located value for one output field, tagged with how it was found."""

    output: str
    source: str = "none"  # template-label | template-region | none


class Suggestion(BaseModel):
    """A heuristic candidate (value + position) to help the user map a field."""

    kind: str  # invoiceno | date | vendor_name
    field: Field


class PageSize(BaseModel):
    width: float
    height: float


class DetectedVendor(BaseModel):
    id: int
    identifier: str
    name: str


class DocumentInfo(BaseModel):
    doc_id: str
    n_pages: int
    pages: List[PageSize]
    ocr_used: bool = False
    detected_vendor: Optional[DetectedVendor] = None


class ReadResult(BaseModel):
    fields: List[ReadField] = PydField(default_factory=list)
    suggestions: List[Suggestion] = PydField(default_factory=list)
    lines: List[LineItem] = PydField(default_factory=list)
    meta: Meta = PydField(default_factory=Meta)
