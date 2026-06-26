"""Shared test fixtures.

Builds a small but realistic *digital* purchase invoice in memory (Faroese
labels) so tests are reproducible and we don't commit a binary PDF.
"""

from __future__ import annotations

import atexit
import io
import os
import tempfile

# Point the SQLite store at a throwaway temp file BEFORE any app module imports
# app.db (which binds its engine at import time). Keeps tests off data/lesarin.db.
_fd, _tmp_db = tempfile.mkstemp(suffix=".lesarin-test.db")
os.close(_fd)
os.environ["LESARIN_DB"] = _tmp_db
atexit.register(lambda: os.path.exists(_tmp_db) and os.remove(_tmp_db))

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


def _build_borderless_invoice() -> bytes:
    """A line-item table drawn as positioned text with NO ruled borders.

    pdfplumber.find_tables() detects nothing here (like many real invoices), so
    this exercises the header-anchored, word-position line parser. Includes a
    wrapped description row (no amount) and a totals row that must terminate the
    line scan.
    """
    from reportlab.pdfgen import canvas as _canvas

    buf = io.BytesIO()
    c = _canvas.Canvas(buf, pagesize=A4)
    width, height = A4

    def row(top: float, cells, bold: bool = False) -> None:
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 10)
        for x, text in cells:
            c.drawString(x, height - top, text)

    row(120, [(40, "Vøra"), (90, "Lýsing"), (300, "Nøgd"), (380, "Prísur"), (470, "Upphædd")], bold=True)
    row(140, [(40, "1001"), (90, "Kaffi 500g"), (300, "2"), (380, "45,00"), (470, "90,00")])
    row(154, [(90, "Arabica beans")])  # wrapped description, no amount
    row(175, [(40, "1002"), (90, "Mjólk 1L"), (300, "6"), (380, "12,50"), (470, "75,00")])
    row(205, [(90, "Í alt"), (470, "165,00")])  # totals → terminator
    c.showPage()
    c.save()
    return buf.getvalue()


@pytest.fixture(scope="session")
def borderless_invoice_pdf() -> bytes:
    return _build_borderless_invoice()


def _build_multipage_invoice() -> bytes:
    """Two-page borderless invoice where the line table CONTINUES onto page 2
    without re-printing the header, and page 2 carries a footer + totals.

    Exercises: header carry-forward across pages, dynamic total row count, the
    totals terminator, and the page-footer guard (the footer must not become a
    line item).
    """
    from reportlab.pdfgen import canvas as _canvas

    buf = io.BytesIO()
    c = _canvas.Canvas(buf, pagesize=A4)
    width, height = A4

    def row(top: float, cells, bold: bool = False) -> None:
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 10)
        for x, text in cells:
            c.drawString(x, height - top, text)

    # Page 1: header + two items, no totals (table continues).
    row(120, [(40, "Vøra"), (90, "Lýsing"), (300, "Nøgd"), (380, "Prísur"), (470, "Upphædd")], bold=True)
    row(140, [(40, "1001"), (90, "Kaffi 500g"), (300, "2"), (380, "45,00"), (470, "90,00")])
    row(160, [(40, "1002"), (90, "Mjólk 1L"), (300, "6"), (380, "12,50"), (470, "75,00")])
    c.showPage()

    # Page 2: NO repeated header — must carry forward page 1's columns.
    row(120, [(40, "1003"), (90, "Te 1L"), (300, "1"), (380, "30,00"), (470, "30,00")])
    row(140, [(40, "1004"), (90, "Sukur"), (300, "4"), (380, "10,00"), (470, "40,00")])
    row(170, [(90, "Í alt"), (470, "235,00")])  # totals → terminator
    row(800, [(40, "Borg P/F"), (200, "Tel +298 477272"), (430, "V-tal"), (470, "549517")])  # footer
    c.showPage()
    c.save()
    return buf.getvalue()


@pytest.fixture(scope="session")
def multipage_invoice_pdf() -> bytes:
    return _build_multipage_invoice()
