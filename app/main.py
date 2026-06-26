"""Lesarin — HTTP service that reads a purchase-invoice PDF and returns a
structured, located form (invoiceno, vendor.name, sentdate, paydate, lines).

Run:  uvicorn app.main:app --reload
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import router as api_router
from .saas import router as saas_router
from .db import init_db
from .extraction import fields as field_extractor
from .extraction import lines as line_extractor
from .extraction import loader
from .models import InvoiceResult


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()  # create SQLite tables if missing
    yield


app = FastAPI(
    title="Lesarin — Invoice Reader",
    version=__version__,
    description=(
        "Receives a purchase-invoice PDF and returns the fields it can locate, "
        "each annotated with its position in the document so a human can verify."
    ),
    lifespan=lifespan,
)

# Allow the Angular dev server (ng serve on :4200) to call the API during dev.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200", "http://127.0.0.1:4200"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Reasonable upload ceiling for an invoice (10 MB).
_MAX_BYTES = 10 * 1024 * 1024

# Load the label dictionary once at startup.
_CONFIG = field_extractor.load_config()

# Mount the /api surface: vendor-template studio (api_router) + the SaaS
# customer surface — accounts, output profiles, one-shot export (saas_router).
app.include_router(api_router)
app.include_router(saas_router)


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


# The SaaS customer app — a dependency-free vanilla-JS UI (login, profiles,
# upload & export). Served same-origin so it needs no CORS or npm build.
_SAAS_WEB = Path(__file__).resolve().parent.parent / "web"
if _SAAS_WEB.is_dir():
    app.mount("/app", StaticFiles(directory=str(_SAAS_WEB), html=True), name="saas")

# Serve the built Angular app (after `ng build`) at the root, if present. Kept
# last so it never shadows /health, /extract, /api, or /app. In dev you instead
# run `ng serve` on :4200 and hit the API cross-origin (CORS is enabled above).
_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist" / "lesarin" / "browser"
if _FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIST), html=True), name="frontend")
