"""The extraction engine — project a parsed PDF onto the canonical vocabulary.

This is the one place that decides *what a document says*: detect the vendor,
apply its saved template if there is one, and fill any gaps from layout
heuristics. It returns a per-field result (value + where it came from), the
mapping that was actually applied, and the line items — enough for a caller to
render output *and* judge how complete/trustworthy the read was.

Both the SaaS export path (`app/saas.py: build_canonical`) and the command-line
tool (`app/cli.py`) build on this, so "smart once" means smart everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from . import repo
from .db_models import Vendor
from .exporters import CanonicalLine
from .extraction import fields as field_extractor
from .extraction import lines as line_extractor
from .extraction import loader
from .extraction import template as templater
from .models import MappingIn, TemplateIn

_CONFIG = field_extractor.load_config()


@dataclass
class FieldResult:
    """One canonical field's located value and how it was found."""

    canonical: str
    value: Optional[str]
    found: bool
    source: str  # template-label | template-region | heuristic | none
    confidence: float = 0.0


@dataclass
class CanonicalExtraction:
    """Everything the engine learned about one document."""

    vendor: Optional[Vendor]
    matched: bool                       # a vendor template was applied
    source: str                         # template | heuristic | none
    fields: Dict[str, FieldResult]
    applied_template: List[dict]        # the mapping used (Result<mapping>)
    lines: List[CanonicalLine]
    suggestions: list = field(default_factory=list)  # raw heuristic suggestions

    def values(self) -> Dict[str, Optional[str]]:
        """The located values, keyed by canonical field (found ones only)."""
        return {k: r.value for k, r in self.fields.items() if r.found}


def lines_to_canonical(line_items) -> List[CanonicalLine]:
    return [
        CanonicalLine(
            description=ln.description.value,
            quantity=ln.quantity.value,
            unit=ln.unit.value,
            unit_price=ln.unit_price.value,
            amount=ln.amount.value,
        )
        for ln in line_items
    ]


def _template_of(vendor: Vendor) -> TemplateIn:
    return TemplateIn(fields=[
        MappingIn(
            output=m.output_key, strategy=m.strategy, label=m.source_label,
            relation=m.relation, value_type=m.value_type, page=m.page, bbox=m.bbox,
        )
        for m in vendor.mappings
    ])


def extract(session: Session, document: loader.Document) -> CanonicalExtraction:
    """Detect the vendor, apply its template, then fill gaps from heuristics."""
    text = templater.document_text(document)
    vendor = repo.detect_vendor(session, text)
    matched = bool(vendor is not None and vendor.mappings)

    fields: Dict[str, FieldResult] = {}
    applied: List[dict] = []

    if matched:
        template = _template_of(vendor)
        applied = [m.model_dump() for m in template.fields]
        for rf in templater.apply_template(document, template):
            fields[rf.output] = FieldResult(
                canonical=rf.output,
                value=rf.value,
                found=rf.found,
                source=rf.source if rf.found else "none",
                confidence=rf.confidence,
            )

    # Heuristic fill: only where the template left a field unfound. This is what
    # lets a never-taught vendor still produce useful output the first time.
    suggestions = templater.field_suggestions(document, _CONFIG)
    for s in suggestions:
        if s.value is None:
            continue
        current = fields.get(s.suggested_key)
        if current is None or not current.found:
            fields[s.suggested_key] = FieldResult(
                canonical=s.suggested_key, value=s.value, found=True,
                source="heuristic", confidence=0.35,
            )

    currency = field_extractor.detect_currency(document)
    if currency and (("Currency" not in fields) or not fields["Currency"].found):
        fields["Currency"] = FieldResult("Currency", currency, True, "heuristic", 0.5)

    lines = lines_to_canonical(line_extractor.extract_line_items(document, _CONFIG))
    source = "template" if matched else ("heuristic" if fields else "none")
    return CanonicalExtraction(
        vendor=vendor, matched=matched, source=source, fields=fields,
        applied_template=applied, lines=lines, suggestions=suggestions,
    )
