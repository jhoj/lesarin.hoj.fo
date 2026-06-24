"""Turn a PDF into positioned words and tables.

The whole point of this module is to keep *coordinates* alongside every piece
of text, so downstream extraction can report **where** each value lives. Both
the digital-text path (pdfplumber) and the scanned-image path (Tesseract)
produce the same ``PageContent`` shape, so the rest of the pipeline doesn't
care which one ran.

All bounding boxes are in PDF points with the origin at the top-left of the
page: ``(x0, top, x1, bottom)``.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import List, Optional

import pdfplumber

# A page with fewer real words than this is treated as scanned and sent to OCR.
_MIN_WORDS_FOR_DIGITAL = 5
# Render DPI for the OCR fallback. 72 PDF points == 1 inch, so the pixel->point
# scale factor is 72 / dpi.
_OCR_DPI = 200


@dataclass
class Word:
    """A single token with its position on the page."""

    text: str
    x0: float
    top: float
    x1: float
    bottom: float
    page: int  # 1-indexed
    confidence: float = 1.0  # 1.0 for digital text; OCR-reported for scans

    @property
    def bbox(self) -> List[float]:
        return [self.x0, self.top, self.x1, self.bottom]

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.top + self.bottom) / 2


@dataclass
class TableCell:
    text: str
    bbox: List[float]
    row: int
    col: int
    page: int


@dataclass
class Table:
    page: int
    bbox: List[float]
    cells: List[TableCell] = field(default_factory=list)
    n_rows: int = 0
    n_cols: int = 0


@dataclass
class PageContent:
    page_number: int  # 1-indexed
    width: float
    height: float
    words: List[Word]
    tables: List[Table]
    ocr_used: bool = False


@dataclass
class Document:
    pages: List[PageContent]

    @property
    def ocr_used(self) -> bool:
        return any(p.ocr_used for p in self.pages)

    @property
    def n_pages(self) -> int:
        return len(self.pages)

    def all_words(self) -> List[Word]:
        words: List[Word] = []
        for page in self.pages:
            words.extend(page.words)
        return words


def load(pdf_bytes: bytes) -> Document:
    """Parse ``pdf_bytes`` into a :class:`Document`."""
    pages: List[PageContent] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for index, page in enumerate(pdf.pages, start=1):
            words = _digital_words(page, index)
            if len(words) >= _MIN_WORDS_FOR_DIGITAL:
                tables = _digital_tables(page, index)
                pages.append(
                    PageContent(index, page.width, page.height, words, tables, ocr_used=False)
                )
            else:
                ocr_words = _ocr_words(pdf_bytes, index, page.width, page.height)
                pages.append(
                    PageContent(index, page.width, page.height, ocr_words, [], ocr_used=True)
                )
    return Document(pages=pages)


def _digital_words(page: "pdfplumber.page.Page", page_number: int) -> List[Word]:
    words: List[Word] = []
    for w in page.extract_words(use_text_flow=False, keep_blank_chars=False):
        words.append(
            Word(
                text=w["text"],
                x0=float(w["x0"]),
                top=float(w["top"]),
                x1=float(w["x1"]),
                bottom=float(w["bottom"]),
                page=page_number,
            )
        )
    return words


def _digital_tables(page: "pdfplumber.page.Page", page_number: int) -> List[Table]:
    tables: List[Table] = []
    try:
        found = page.find_tables()
    except Exception:
        return tables
    for t in found:
        cells: List[TableCell] = []
        rows = t.rows
        for r_idx, row in enumerate(rows):
            for c_idx, cell_bbox in enumerate(row.cells):
                if cell_bbox is None:
                    continue
                x0, top, x1, bottom = cell_bbox
                text = (
                    page.crop((x0, top, x1, bottom)).extract_text(x_tolerance=1, y_tolerance=1)
                    or ""
                ).strip()
                cells.append(
                    TableCell(
                        text=text,
                        bbox=[float(x0), float(top), float(x1), float(bottom)],
                        row=r_idx,
                        col=c_idx,
                        page=page_number,
                    )
                )
        bx0, btop, bx1, bbottom = t.bbox
        tables.append(
            Table(
                page=page_number,
                bbox=[float(bx0), float(btop), float(bx1), float(bbottom)],
                cells=cells,
                n_rows=len(rows),
                n_cols=max((len(r.cells) for r in rows), default=0),
            )
        )
    return tables


def _ocr_words(
    pdf_bytes: bytes, page_number: int, page_width: float, page_height: float
) -> List[Word]:
    """OCR a single page and return words in PDF-point coordinates.

    Imports are local so the digital path works even if poppler/Tesseract are
    not installed; OCR is only required for scanned PDFs.
    """
    try:
        from pdf2image import convert_from_bytes
        import pytesseract
    except ImportError:
        return []

    images = convert_from_bytes(
        pdf_bytes, dpi=_OCR_DPI, first_page=page_number, last_page=page_number
    )
    if not images:
        return []
    image = images[0]

    # Pixel -> PDF point scale. Derive from the actual render size so it stays
    # correct even if poppler rounds dimensions.
    sx = page_width / image.width if image.width else 72.0 / _OCR_DPI
    sy = page_height / image.height if image.height else 72.0 / _OCR_DPI

    data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
    words: List[Word] = []
    for i, text in enumerate(data["text"]):
        text = (text or "").strip()
        if not text:
            continue
        conf_raw = data["conf"][i]
        try:
            conf = float(conf_raw)
        except (TypeError, ValueError):
            conf = -1.0
        if conf < 0:
            continue
        left = data["left"][i]
        top = data["top"][i]
        width = data["width"][i]
        height = data["height"][i]
        words.append(
            Word(
                text=text,
                x0=left * sx,
                top=top * sy,
                x1=(left + width) * sx,
                bottom=(top + height) * sy,
                page=page_number,
                confidence=max(0.0, min(conf / 100.0, 1.0)),
            )
        )
    return words


def ocr_language() -> Optional[str]:
    """Best-effort: prefer Danish OCR data (closest to Faroese) if installed."""
    try:
        import pytesseract

        langs = pytesseract.get_languages(config="")
        if "dan" in langs:
            return "dan"
    except Exception:
        return None
    return None
