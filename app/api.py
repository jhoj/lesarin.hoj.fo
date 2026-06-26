"""The `/api` surface for the vendor-template UI (web app and TUI share it).

Flow it supports:
1. Upload a PDF once  → it's parsed, cached under a ``doc_id``, and the vendor is
   auto-detected (by V-tal / keywords).
2. Read repeatedly     → apply the editor's current template to the cached doc and
   return located values + heuristic suggestions, with no re-parse and no save.
3. Persist             → CRUD for the expected-output setup table and for vendors
   (a vendor's field mappings are its template).
"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from sqlalchemy.orm import Session

from .db import get_session
from .db_models import Vendor
from .document_store import store
from .extraction import fields as field_extractor
from .extraction import lines as line_extractor
from .extraction import loader
from .extraction import template as templater
from .models import (
    DetectedVendor,
    DocumentInfo,
    MappingIn,
    Meta,
    OutputFieldIn,
    OutputFieldOut,
    PageSize,
    ReadResult,
    SuggestFieldsResult,
    TemplateIn,
    VendorIn,
    VendorOut,
)
from . import repo

router = APIRouter(prefix="/api")

_MAX_BYTES = 10 * 1024 * 1024
_CONFIG = field_extractor.load_config()


def _vendor_out(v: Vendor) -> VendorOut:
    return VendorOut(
        id=v.id,
        identifier=v.identifier,
        name=v.name,
        identifier_kind=v.identifier_kind,
        match_keywords=list(v.match_keywords or []),
        mappings=[
            MappingIn(
                output=m.output_key,
                strategy=m.strategy,
                label=m.source_label,
                relation=m.relation,
                value_type=m.value_type,
                page=m.page,
                bbox=m.bbox,
            )
            for m in v.mappings
        ],
    )


def _parse_upload(data: bytes) -> loader.Document:
    if not data:
        raise HTTPException(400, "Empty file.")
    if len(data) > _MAX_BYTES:
        raise HTTPException(413, "File too large (max 10 MB).")
    try:
        return loader.load(data)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, f"Could not read PDF: {exc}") from exc


# ---- Documents ------------------------------------------------------------

@router.post("/documents", response_model=DocumentInfo)
async def upload_document(
    file: UploadFile = File(...), session: Session = Depends(get_session)
) -> DocumentInfo:
    data = await file.read()
    document = _parse_upload(data)
    doc_id = store.put(data, document)

    detected = None
    vendor = repo.detect_vendor(session, templater.document_text(document))
    if vendor is not None:
        detected = DetectedVendor(id=vendor.id, identifier=vendor.identifier, name=vendor.name)

    return DocumentInfo(
        doc_id=doc_id,
        n_pages=document.n_pages,
        pages=[PageSize(width=p.width, height=p.height) for p in document.pages],
        ocr_used=document.ocr_used,
        detected_vendor=detected,
    )


@router.get("/documents/{doc_id}/file")
def get_document_file(doc_id: str) -> Response:
    entry = store.get(doc_id)
    if entry is None:
        raise HTTPException(404, "Document expired or not found — re-upload.")
    return Response(content=entry.pdf_bytes, media_type="application/pdf")


@router.post("/documents/{doc_id}/read", response_model=ReadResult)
def read_document(
    doc_id: str, template: TemplateIn, session: Session = Depends(get_session)
) -> ReadResult:
    entry = store.get(doc_id)
    if entry is None:
        raise HTTPException(404, "Document expired or not found — re-upload.")
    document = entry.document

    # Augment each field's label search with its taught read-labels (aliases), so
    # e.g. an output field named "Vtal" still auto-locates a "V-tal" label.
    aliases = {f.key: list(f.aliases or []) for f in repo.list_output_fields(session)}
    located = templater.apply_template(document, template, aliases)
    suggestions = templater.suggestions(document, _CONFIG)
    lines = line_extractor.extract_line_items(document, _CONFIG)
    found = sum(1 for f in located if f.found)
    return ReadResult(
        fields=located,
        suggestions=suggestions,
        lines=lines,
        meta=Meta(
            pages=document.n_pages,
            ocr_used=document.ocr_used,
            fields_found=found,
            fields_total=len(located),
            currency=field_extractor.detect_currency(document),
        ),
    )


@router.get("/documents/{doc_id}/suggest-fields", response_model=SuggestFieldsResult)
def suggest_fields(doc_id: str) -> SuggestFieldsResult:
    """Propose output fields detected on the document (for first-time setup)."""
    entry = store.get(doc_id)
    if entry is None:
        raise HTTPException(404, "Document expired or not found — re-upload.")
    return SuggestFieldsResult(suggestions=templater.field_suggestions(entry.document, _CONFIG))


# ---- Output-field setup table ---------------------------------------------

def _output_field_out(f) -> OutputFieldOut:
    return OutputFieldOut(
        key=f.key,
        display_name=f.display_name,
        value_type=f.value_type,
        sort_order=f.sort_order,
        aliases=list(f.aliases or []),
    )


@router.get("/output-fields", response_model=List[OutputFieldOut])
def list_output_fields(session: Session = Depends(get_session)) -> List[OutputFieldOut]:
    return [_output_field_out(f) for f in repo.list_output_fields(session)]


@router.post("/output-fields", response_model=OutputFieldOut)
def upsert_output_field(
    field: OutputFieldIn, session: Session = Depends(get_session)
) -> OutputFieldOut:
    f = repo.upsert_output_field(
        session, field.key, field.display_name, field.value_type, field.sort_order, field.aliases
    )
    return _output_field_out(f)


@router.delete("/output-fields/{key}")
def delete_output_field(key: str, session: Session = Depends(get_session)) -> dict:
    if not repo.delete_output_field(session, key):
        raise HTTPException(404, "Output field not found.")
    return {"deleted": key}


# ---- Vendors (templates) --------------------------------------------------

@router.get("/vendors", response_model=List[VendorOut])
def list_vendors(session: Session = Depends(get_session)) -> List[VendorOut]:
    return [_vendor_out(v) for v in repo.list_vendors(session)]


@router.get("/vendors/{vendor_id}", response_model=VendorOut)
def get_vendor(vendor_id: int, session: Session = Depends(get_session)) -> VendorOut:
    v = repo.get_vendor(session, vendor_id)
    if v is None:
        raise HTTPException(404, "Vendor not found.")
    return _vendor_out(v)


@router.post("/vendors", response_model=VendorOut)
def create_vendor(body: VendorIn, session: Session = Depends(get_session)) -> VendorOut:
    v = repo.create_vendor(
        session,
        identifier=body.identifier,
        name=body.name,
        identifier_kind=body.identifier_kind,
        match_keywords=body.match_keywords,
        mappings=[m.model_dump() for m in body.mappings],
    )
    return _vendor_out(v)


@router.put("/vendors/{vendor_id}", response_model=VendorOut)
def update_vendor(
    vendor_id: int, body: VendorIn, session: Session = Depends(get_session)
) -> VendorOut:
    v = repo.update_vendor(
        session,
        vendor_id,
        identifier=body.identifier,
        name=body.name,
        match_keywords=body.match_keywords,
        mappings=[m.model_dump() for m in body.mappings],
    )
    if v is None:
        raise HTTPException(404, "Vendor not found.")
    return _vendor_out(v)


@router.delete("/vendors/{vendor_id}")
def delete_vendor(vendor_id: int, session: Session = Depends(get_session)) -> dict:
    if not repo.delete_vendor(session, vendor_id):
        raise HTTPException(404, "Vendor not found.")
    return {"deleted": vendor_id}


# ---- Production extraction (template-aware) -------------------------------

@router.post("/extract", response_model=ReadResult)
async def extract_with_template(
    file: UploadFile = File(...), session: Session = Depends(get_session)
) -> ReadResult:
    """Headless production path: detect the vendor and apply its saved template."""
    data = await file.read()
    document = _parse_upload(data)

    vendor = repo.detect_vendor(session, templater.document_text(document))
    located = []
    if vendor is not None and vendor.mappings:
        tmpl = TemplateIn(
            fields=[
                MappingIn(
                    output=m.output_key, strategy=m.strategy, label=m.source_label,
                    relation=m.relation, value_type=m.value_type, page=m.page, bbox=m.bbox,
                )
                for m in vendor.mappings
            ]
        )
        located = templater.apply_template(document, tmpl)

    lines = line_extractor.extract_line_items(document, _CONFIG)
    found = sum(1 for f in located if f.found)
    return ReadResult(
        fields=located,
        suggestions=templater.suggestions(document, _CONFIG) if not located else [],
        lines=lines,
        meta=Meta(
            filename=file.filename,
            pages=document.n_pages,
            ocr_used=document.ocr_used,
            fields_found=found,
            fields_total=len(located),
        ),
    )
