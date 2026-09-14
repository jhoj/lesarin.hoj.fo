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


def test_region_over_an_image_falls_back_to_ocr_crop(document, sample_invoice_pdf, monkeypatch):
    # An empty region — no real words fall inside it, e.g. a box drawn over a
    # rasterised logo/letterhead. With pdf_bytes given, this must retry via
    # loader.ocr_crop rather than coming back empty.
    calls = []

    def fake_ocr_crop(pdf_bytes, page, width, height, bbox):
        calls.append((page, bbox))
        return "9865 - 100.355.5"

    monkeypatch.setattr(template.loader, "ocr_crop", fake_ocr_crop)

    field = template.apply_template(
        document,
        TemplateIn(fields=[MappingIn(output="AccountNo", strategy="region", page=1, bbox=[1, 1, 2, 2])]),
        pdf_bytes=sample_invoice_pdf,
    )[0]
    assert field.value == "9865 - 100.355.5"
    assert field.source == "template-region"
    assert calls == [(1, [1, 1, 2, 2])]


def test_region_without_pdf_bytes_stays_empty_over_blank_space(document):
    # Same empty box, but no pdf_bytes — must not attempt OCR, and must not
    # crash; behaviour is unchanged from before the OCR fallback existed.
    field = _read(document, MappingIn(output="AccountNo", strategy="region", page=1, bbox=[1, 1, 2, 2]))
    assert field.value is None
    assert field.source == "none"


def test_region_with_real_text_never_calls_ocr(document, sample_invoice_pdf, monkeypatch):
    def fail_if_called(*a, **kw):
        raise AssertionError("ocr_crop should not run when real words were found")

    monkeypatch.setattr(template.loader, "ocr_crop", fail_if_called)

    located = _read(document, MappingIn(output="InvoiceNumber", strategy="label", label="Fakturanr"))
    field = template.apply_template(
        document,
        TemplateIn(fields=[
            MappingIn(output="InvoiceNumber", strategy="region", page=located.page, bbox=located.bbox)
        ]),
        pdf_bytes=sample_invoice_pdf,
    )[0]
    assert field.value == "2026-0014"


def test_region_ocr_fallback_skipped_on_an_already_ocrd_page(monkeypatch):
    # A page that went through whole-page OCR already searched OCR-derived
    # words for the box above and found nothing — retrying is redundant work.
    scanned_page = loader.PageContent(1, 595.0, 842.0, [], [], ocr_used=True)
    doc = loader.Document(pages=[scanned_page])

    def fail_if_called(*a, **kw):
        raise AssertionError("ocr_crop should not run on an already-OCR'd page")

    monkeypatch.setattr(template.loader, "ocr_crop", fail_if_called)

    field = template.apply_template(
        doc,
        TemplateIn(fields=[MappingIn(output="AccountNo", strategy="region", page=1, bbox=[1, 1, 2, 2])]),
        pdf_bytes=b"irrelevant",
    )[0]
    assert field.value is None


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
