"""Headless TUI checks: page reconstruction + keyboard map-and-read flow."""

from __future__ import annotations

import asyncio

from app.db import SessionLocal, init_db
from app.extraction import loader
from app.tui import LesarinTui, reconstruct_page
from app import repo


def test_reconstruct_page_preserves_text(sample_invoice_pdf):
    doc = loader.load(sample_invoice_pdf)
    text = reconstruct_page(doc.pages[0])
    assert "Fakturanr" in text
    assert "2026-0014" in text


def test_tui_maps_and_reads(sample_invoice_pdf):
    init_db()
    s = SessionLocal()
    repo.upsert_output_field(s, "InvoiceNumber", "Invoice number", "string", 0)
    s.close()

    async def run():
        app = LesarinTui(pdf_bytes=sample_invoice_pdf)
        async with app.run_test() as pilot:
            await pilot.pause()
            # Map the field to its source label and re-read (what pressing 'e' + typing does).
            app.set_label_for("InvoiceNumber", "Fakturanr")
            await pilot.pause()
            return app.results.get("InvoiceNumber")

    result = asyncio.run(run())
    assert result is not None
    assert result[0] == "2026-0014"  # (value, confidence, source)
