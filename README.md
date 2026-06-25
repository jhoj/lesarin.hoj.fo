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

### Windows

There's no `apt`, so set it up with pip and (only for scanned PDFs) two manual
downloads. Digital PDFs and the test suite work with just pip.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

For the OCR fallback (scanned/image PDFs only):

1. **Tesseract** — installer from the UB Mannheim build; tick the **Danish**
   language pack during setup.
2. **Poppler** — download `poppler-windows`, unzip (e.g. to `C:\poppler`).

The installer doesn't add these to PATH, so point the app at them with env vars
(no PATH editing needed):

```powershell
$env:TESSERACT_CMD = "C:\Program Files\Tesseract-OCR\tesseract.exe"
$env:POPPLER_PATH  = "C:\poppler\Library\bin"
```

If `Activate.ps1` is blocked once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

## Run

```bash
uvicorn app.main:app --reload
```

- `GET /health` → service status and detected OCR language.
- `POST /extract` → multipart `file=<pdf>`, returns the structured form.

```bash
curl -F file=@invoice.pdf http://localhost:8000/extract
```

There is no UI — it's a JSON API. Open `http://localhost:8000/docs` for an
interactive page to upload a single PDF, or use the batch client below.

### Test a whole folder of PDFs

With the service running, point the batch client at a folder. It writes a
`<name>.lesarin.json` next to each PDF and prints which fields it located:

```powershell
python scripts\extract_folder.py C:\path\to\invoices
```

```
  ok 2026-0014.pdf: invoiceno, sentdate, paydate, vendor.name; 3 line(s)
  ok scan_old.pdf: invoiceno, vendor.name; 5 line(s) [OCR]
```

Options: `--url` (default `http://127.0.0.1:8000`), `--out <dir>` to collect the
JSON files in one place instead of beside the PDFs.

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

## Vendor templates + web UI

The heuristic reader above guesses across any layout. To make it *just work* for a
given supplier, teach it once: in the **web UI** you name the vendor (identified by its
V-tal / ID), define the output fields you want (e.g. `VendorNumber`), and map each one to
where it sits on that vendor's invoice — by source label (`"Veitara nr."`) or by dragging
a box on the page. The mapping is saved as a **vendor template** in SQLite. Afterwards the
reader auto-detects the vendor by its V-tal and applies the template; reopen the UI only
when a layout changes.

- Persistence: `data/lesarin.db` (SQLite) — an output-format setup table, vendors, and
  per-vendor field mappings. Created automatically on first run.
- API: under `/api` — `documents` (upload / file / read), `vendors` + `output-fields`
  CRUD, and a template-aware `POST /api/extract` for headless production extraction.
- UI: an Angular 22 app in [`frontend/`](frontend/README.md).

### Run the UI

```bash
# 1. backend (also serves a built UI at / if frontend/dist exists)
uvicorn app.main:app --reload

# 2. frontend dev server (separate terminal)
cd frontend && npm install && npm start   # http://localhost:4200
```

For a single-origin production run, `cd frontend && npm run build` then just
`uvicorn app.main:app` and open `http://localhost:8000/`.

### Keyboard-only TUI (alternative layout)

Prefer no mouse? A Textual TUI shares the same SQLite store and templates as the web app —
teach a vendor in one, it works in the other. A terminal can't show the PDF image, so the
centre pane is a text reconstruction of the page (faithful for digital PDFs):

```bash
python -m app.tui invoice.pdf
```

Keys: `e` edit the selected field's source label · `r` read · `s` save vendor · `q` quit.

## Tests

```bash
pytest
```

The tests build a small Faroese invoice in memory (`reportlab`) and assert that
fields, dates, vendor, and line items are extracted with positions, the template
engine (label/region strategies), vendor/output CRUD + V-tal detection, and the
HTTP endpoints (21 tests).

## Roadmap

- Optional local LLM mode (Ollama) as a fallback for layouts the heuristics miss,
  staying fully local and free.
- Amount/total fields and currency detection.
- Richer line-item mapping per vendor (column templates).
