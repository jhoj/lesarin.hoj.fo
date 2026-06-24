"""Lesarin — HTTP service that reads a purchase-invoice PDF and returns a
structured, located form (invoiceno, vendor.name, sentdate, paydate, lines).

Run:  uvicorn app.main:app --reload
"""

from __future__ import annotations

from fastapi import FastAPI, File, HTTPException, UploadFile

from . import __version__
from .extraction import fields as field_extractor
from .extraction import lines as line_extractor
from .extraction import loader
from .models import InvoiceResult

app = FastAPI(
    title="Lesarin — Invoice Reader",
    version=__version__,
    description=(
        "Receives a purchase-invoice PDF and returns the fields it can locate, "
        "each annotated with its position in the document so a human can verify."
    ),
)

# Reasonable upload ceiling for an invoice (10 MB).
_MAX_BYTES = 10 * 1024 * 1024

# Load the label dictionary once at startup.
_CONFIG = field_extractor.load_config()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": __version__, "ocr_language": loader.ocr_language()}


@app.post("/extract", response_model=InvoiceResult)
async def extract(file: UploadFile = File(...)) -> InvoiceResult:
    if file.content_type not in ("application/pdf", "application/octet-stream", None):
        # Be lenient: some clients send octet-stream. Still require a .pdf name.
        if not (file.filename or "").lower().endswith(".pdf"):
            raise HTTPException(status_code=415, detail="Only PDF files are accepted.")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file.")
    if len(data) > _MAX_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 10 MB).")

    try:
        document = loader.load(data)
    except Exception as exc:  # noqa: BLE001 — surface parse failures to the caller
        raise HTTPException(status_code=422, detail=f"Could not read PDF: {exc}") from exc

    result = field_extractor.extract(document, filename=file.filename, config=_CONFIG)
    result.lines = line_extractor.extract_line_items(document, _CONFIG)
    return result
