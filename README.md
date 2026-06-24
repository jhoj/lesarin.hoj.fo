# Lesarin

Lesarin kann móttaka skjøl og geva upplýsingar aftur.

*Lesarin receives documents and gives information back.* The first reader takes a
**purchase-invoice PDF** and returns a structured form — `invoiceno`,
`vendor.name`, `sentdate`, `paydate`, `lines` — where **every value carries its
location** (page + bounding box) so it can be highlighted and verified.

It's a first-pass helper: it extracts what it can find and points at where each
value sits. Fields it can't locate come back empty rather than guessed.

## Why no OCR-only / no LLM service?

- **Tesseract (OCR) is a fallback, not the core.** Most invoices are digital PDFs
  with an embedded text layer — read directly with `pdfplumber`, which is faster
  and more accurate than OCR. Tesseract only runs on pages that are scanned
  images. OCR turns pixels into characters; it does nothing about *structure*.
- **No paid/hosted service.** Everything runs locally and free: positions from
  `pdfplumber`, OCR from Tesseract, structuring from a layout-aware heuristic
  (label + position), dates via `dateparser`. No API keys, no per-document cost.
- **No per-vendor templates.** Extraction keys off a shared, multilingual label
  vocabulary (Faroese / Danish / English), so it generalises across layouts.

## How it works

1. **Text + positions** (`app/extraction/loader.py`) — `pdfplumber` yields words
   with bounding boxes and tables. A page with no text layer is rendered and
   OCR'd with Tesseract (`pytesseract`), producing the same positioned-word shape.
2. **Field extraction** (`app/extraction/fields.py`) — finds a label among the
   positioned words and reads the value beside/below it. Labels live in
   `app/config/labels.yaml` — extend them without touching code.
3. **Line items** (`app/extraction/lines.py`) — detects the line-item table and
   maps columns by header keywords, keeping each cell's position.

## Setup

```bash
bash scripts/setup.sh        # installs OCR system packages + Python deps
# or, Python deps only (digital PDFs work without OCR):
pip install -r requirements.txt
```

System packages (for the scanned-PDF OCR path): `tesseract-ocr`,
`tesseract-ocr-dan`, `poppler-utils`.

> Tip: to run `scripts/setup.sh` automatically at the start of every Claude Code
> web session, add a `SessionStart` hook to `.claude/settings.json`.

## Run

```bash
uvicorn app.main:app --reload
```

- `GET /health` → service status and detected OCR language.
- `POST /extract` → multipart `file=<pdf>`, returns the structured form.

```bash
curl -F file=@invoice.pdf http://localhost:8000/extract
```

### Response shape

```jsonc
{
  "invoiceno": { "value": "2026-0014", "page": 1, "bbox": [x0,top,x1,bottom],
                 "confidence": 0.9, "source_label": "fakturanr" },
  "vendor":    { "name": { "value": "Føroya Handil P/F", "page": 1, "bbox": [...] } },
  "sentdate":  { "value": "2026-01-12", "raw": "12-01-2026", "page": 1, "bbox": [...] },
  "paydate":   { "value": "2026-01-26", "raw": "26-01-2026", "page": 1, "bbox": [...] },
  "lines":     [ { "description": {...}, "quantity": {...},
                   "unit_price": {...}, "amount": {...} } ],
  "meta":      { "pages": 1, "ocr_used": false, "fields_found": 4, "fields_total": 4 }
}
```

Dates are normalised to ISO `YYYY-MM-DD`; `raw` keeps the original text. A field
with `value: null` and `bbox: null` means it wasn't located.

## Tests

```bash
pytest
```

The tests build a small Faroese invoice in memory (`reportlab`) and assert that
fields, dates, vendor, and line items are extracted with positions, plus the
HTTP endpoints.

## Roadmap

- Optional local LLM mode (Ollama) as a fallback for layouts the heuristics miss,
  staying fully local and free.
- Amount/total fields and currency detection.
- A review UI that renders the PDF and highlights each located value.
