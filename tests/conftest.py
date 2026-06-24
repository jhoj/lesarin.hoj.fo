"""Shared test fixtures.

Builds a small but realistic *digital* purchase invoice in memory (Faroese
labels) so tests are reproducible and we don't commit a binary PDF.
"""

from __future__ import annotations

import io

import pytest
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet


def _build_sample_invoice() -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=20 * mm)
    styles = getSampleStyleSheet()
    story = []

    # Vendor / sender block at the top.
    story.append(Paragraph("Føroya Handil P/F", styles["Title"]))
    story.append(Paragraph("Niels Finsens gøta 1, 100 Tórshavn", styles["Normal"]))
    story.append(Spacer(1, 8 * mm))

    # Buyer block — must NOT be picked as the vendor.
    story.append(Paragraph("Keypari: Testfyritøka P/F", styles["Normal"]))
    story.append(Spacer(1, 6 * mm))

    # Field labels with values to the right.
    story.append(Paragraph("Fakturanr: 2026-0014", styles["Normal"]))
    story.append(Paragraph("Fakturadato: 12-01-2026", styles["Normal"]))
    story.append(Paragraph("Forfaldsdato: 26-01-2026", styles["Normal"]))
    story.append(Spacer(1, 8 * mm))

    # Line-item table with a visible grid so pdfplumber detects it.
    table_data = [
        ["Lýsing", "Nøgd", "Prísur", "Upphædd"],
        ["Kaffi 500g", "2", "45,00", "90,00"],
        ["Mjólk 1L", "6", "12,50", "75,00"],
        ["Breyð", "3", "20,00", "60,00"],
    ]
    table = Table(table_data, colWidths=[70 * mm, 25 * mm, 30 * mm, 30 * mm])
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
            ]
        )
    )
    story.append(table)

    doc.build(story)
    return buffer.getvalue()


@pytest.fixture(scope="session")
def sample_invoice_pdf() -> bytes:
    return _build_sample_invoice()
