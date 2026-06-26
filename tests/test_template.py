"""Template-driven extraction: label & region strategies, date normalisation."""

from __future__ import annotations

import pytest

from app.extraction import fields, loader, template
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


def test_label_strategy_uses_aliases(document):
    # The mapping's own label ("InvoiceNo") never appears, but a taught read-label
    # ("Fakturanr") does — the value must still be located via the alias.
    field = template.apply_template(
        document,
        TemplateIn(fields=[MappingIn(output="InvoiceNo", strategy="label", label="InvoiceNo")]),
        aliases={"InvoiceNo": ["Fakturanr"]},
    )[0]
    assert field.value == "2026-0014"
    assert field.source == "template-label"


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


def test_region_strategy_tolerates_a_partial_box(document):
    # A hand-drawn box rarely centres on the text. Cover only the left ~40% of
    # the value horizontally — the old centre-in-box rule would read nothing
    # (box vanishes); the row-overlap rule must still capture the value.
    located = _read(document, MappingIn(output="InvoiceNumber", strategy="label", label="Fakturanr"))
    x0, top, x1, bottom = located.bbox
    partial = [x0 + (x1 - x0) * 0.55, top, x1 + 2, bottom]
    by_region = _read(
        document,
        MappingIn(output="InvoiceNumber", strategy="region", page=located.page, bbox=partial),
    )
    assert by_region.value == "2026-0014"
    assert by_region.source == "template-region"


def test_short_label_prefers_the_line_it_leads(document, sample_invoice_pdf):
    # A short label that also appears as the trailing word of a longer phrase
    # must read the line it *leads*, not the first line that merely contains it.
    from app.extraction.loader import Word, PageContent, Document  # local: test-only fixture
    words = [
        Word(text="Total", x0=10, top=100, x1=40, bottom=110, page=1),
        Word(text="MVG", x0=50, top=100, x1=70, bottom=110, page=1),  # trailing "MVG"
        Word(text="143,13", x0=90, top=100, x1=120, bottom=110, page=1),
        Word(text="MVG", x0=10, top=120, x1=30, bottom=130, page=1),  # leading "MVG"
        Word(text="35,78", x0=50, top=120, x1=80, bottom=130, page=1),
    ]
    doc = Document(pages=[PageContent(1, 200.0, 300.0, words, [])])
    field = _read(doc, MappingIn(output="MVG", strategy="label", label="MVG", value_type="number"))
    assert field.value == "35.78"  # the line MVG leads, not the "Total MVG 143,13" line


def test_missing_label_returns_empty(document):
    field = _read(document, MappingIn(output="Nope", strategy="label", label="DoesNotExist"))
    assert field.value is None
    assert field.found is False
    assert field.source == "none"


def test_field_suggestions_propose_outputs(document):
    sugg = {s.suggested_key: s for s in template.field_suggestions(document, fields.load_config())}
    assert sugg["InvoiceNo"].value == "2026-0014"
    assert sugg["InvoiceNo"].read_labels  # carries the matched read-label
    assert sugg["DueDate"].value == "2026-01-26"
    assert "VendorName" in sugg  # positional, no label


def test_suggestions_help_the_user(document):
    kinds = {s.kind for s in template.suggestions(document)}
    assert "invoiceno" in kinds
    assert "vendor_name" in kinds
