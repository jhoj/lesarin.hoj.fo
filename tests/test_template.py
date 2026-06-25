"""Template-driven extraction: label & region strategies, date normalisation."""

from __future__ import annotations

import pytest

from app.extraction import loader, template
from app.models import MappingIn, TemplateIn


@pytest.fixture()
def document(sample_invoice_pdf):
    return loader.load(sample_invoice_pdf)


def _read(document, mapping: MappingIn):
    return template.apply_template(document, TemplateIn(fields=[mapping]))[0]


def test_label_strategy_reads_value(document):
    field = _read(document, MappingIn(output="InvoiceNumber", strategy="label", label="Fakturanr"))
    assert field.value == "2026-0014"
    assert field.source == "template-label"
    assert field.bbox is not None and len(field.bbox) == 4


def test_label_strategy_normalises_date(document):
    field = _read(
        document,
        MappingIn(output="DueDate", strategy="label", label="Forfaldsdato", value_type="date"),
    )
    assert field.value == "2026-01-26"  # ISO from "26-01-2026"
    assert field.raw == "26-01-2026"
    assert field.source == "template-label"


def test_region_strategy_reads_box(document):
    # Locate the invoice number by label first, then re-read the same box by region.
    located = _read(document, MappingIn(output="InvoiceNumber", strategy="label", label="Fakturanr"))
    by_region = _read(
        document,
        MappingIn(output="InvoiceNumber", strategy="region", page=located.page, bbox=located.bbox),
    )
    assert by_region.value == "2026-0014"
    assert by_region.source == "template-region"


def test_missing_label_returns_empty(document):
    field = _read(document, MappingIn(output="Nope", strategy="label", label="DoesNotExist"))
    assert field.value is None
    assert field.found is False
    assert field.source == "none"


def test_suggestions_help_the_user(document):
    kinds = {s.kind for s in template.suggestions(document)}
    assert "invoiceno" in kinds
    assert "vendor_name" in kinds
