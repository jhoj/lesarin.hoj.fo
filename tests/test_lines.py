"""Header-anchored line-item parsing (works on borderless invoices)."""

from __future__ import annotations

import pytest

from app.extraction import fields, lines, loader


@pytest.fixture()
def cfg():
    return fields.load_config()


def _items(pdf_bytes, cfg):
    return lines.extract_line_items(loader.load(pdf_bytes), cfg)


def test_borderless_table_is_parsed(borderless_invoice_pdf, cfg):
    items = _items(borderless_invoice_pdf, cfg)
    assert len(items) == 2  # two products; totals row excluded
    assert [it.amount.value for it in items] == ["90,00", "75,00"]


def test_wrapped_description_merges_into_item(borderless_invoice_pdf, cfg):
    items = _items(borderless_invoice_pdf, cfg)
    # The amount-less "Arabica beans" row folds into the first item's description.
    assert "Kaffi" in items[0].description.value
    assert "Arabica" in items[0].description.value
    assert "Mjólk" in items[1].description.value


def test_columns_and_positions(borderless_invoice_pdf, cfg):
    items = _items(borderless_invoice_pdf, cfg)
    assert items[0].quantity.value == "2"
    assert items[0].unit_price.value == "45,00"
    assert items[0].description.bbox is not None
    assert items[0].amount.bbox is not None


def test_totals_row_is_not_a_line_item(borderless_invoice_pdf, cfg):
    items = _items(borderless_invoice_pdf, cfg)
    assert all(it.amount.value != "165,00" for it in items)


def test_multipage_continuation_accumulates(multipage_invoice_pdf, cfg):
    items = _items(multipage_invoice_pdf, cfg)
    # 2 items on page 1 + 2 on page 2 (whose header is NOT reprinted).
    assert [it.amount.value for it in items] == ["90,00", "75,00", "30,00", "40,00"]


def test_multipage_totals_and_footer_excluded(multipage_invoice_pdf, cfg):
    items = _items(multipage_invoice_pdf, cfg)
    amounts = {it.amount.value for it in items}
    assert "235,00" not in amounts  # totals terminator
    assert "549517" not in amounts  # page footer (V-tal), not a line item


def test_gridded_sample_still_yields_three(sample_invoice_pdf, cfg):
    items = _items(sample_invoice_pdf, cfg)
    assert len(items) == 3
    assert items[0].description.value is not None
    assert items[0].amount.value is not None
    assert items[0].description.bbox is not None
