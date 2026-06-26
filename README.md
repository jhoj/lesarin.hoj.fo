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

## SaaS: log in, upload, get your data

The same engine runs as a multi-tenant service. A customer **logs in, uploads an
invoice, and gets the data back in the shape they want** — a custom JSON object
keyed to their API, generic XML, UBL 2.1, or Danish OIOUBL.

The thing that makes it feel effortless: **mappings are stored centrally**. The
first time *anyone* uploads a given supplier's invoice, the system learns where
that supplier prints each value and saves a **shared vendor template**. Every
later upload of that supplier — by *any* customer — is an instant hit. Customers
never see or manage templates; they only choose which fields they want out and
what to call them. And because the heuristics already locate most fields
label-free, a brand-new supplier usually produces useful output the very first
time too.

### How the layers fit

* **Canonical vocabulary** (shared) — a fixed set of semantic invoice fields
  (`InvoiceNo`, `InvoiceDate`, `DueDate`, `VendorName`, `VendorNo`, `Currency`,
  `TotalExclVat`, `Vat`, `TotalInclVat`, `AccountNo`). The contract both sides
  agree on. See [`app/canonical.py`](app/canonical.py).
* **Vendor templates** (shared centrally) — map a supplier's layout onto the
  canonical fields. Taught once (often auto-learned on first upload), reused by
  everyone. Stored in the same SQLite store as before.
* **Output profiles** (per customer) — pick canonical fields, rename them to
  your own keys, choose a format. This is the *only* thing a customer configures.

### SaaS API (under `/api`)

| Endpoint | Purpose |
| --- | --- |
| `POST /api/auth/register` · `POST /api/auth/login` | email + password → bearer token |
| `GET /api/me` | the current account |
| `GET /api/canonical-fields` | the fields you can put in a profile |
| `GET/POST/PUT/DELETE /api/me/profiles[...]` | manage output profiles |
| `POST /api/me/export?profile_id=&fmt=` | upload a PDF → data in your format |

Auth is dependency-free: passwords are PBKDF2-hashed and tokens are HMAC-signed
with the standard library (no native crypto build, no session table). Set
`LESARIN_SECRET` in production to pin the token-signing key; otherwise a random
secret is generated and persisted beside the database.

```bash
# 1. register and keep the token
TOKEN=$(curl -s -X POST localhost:8000/api/auth/register \
  -H 'content-type: application/json' \
  -d '{"email":"me@firm.fo","password":"hunter2hunter2"}' | python -c 'import sys,json;print(json.load(sys.stdin)["token"])')

# 2. upload an invoice → get JSON keyed to your default profile
curl -s -H "Authorization: Bearer $TOKEN" \
  -F file=@invoice.pdf 'localhost:8000/api/me/export'

# 3. same invoice as OIOUBL instead
curl -s -H "Authorization: Bearer $TOKEN" \
  -F file=@invoice.pdf 'localhost:8000/api/me/export?fmt=oioubl'
```

### SaaS web app

The Angular app in [`frontend/`](frontend/README.md) is the single UI for both
audiences, as routed views behind a login:

- **`/app`** (customer) — register/log in, build output profiles by ticking
  fields and renaming them, then drag in a PDF and download the result.
- **`/studio`** (mapping) — the vendor-template editor (PDF viewer + drag-box
  wizard) for teaching the central mappings.

```bash
cd frontend && npm install && npm start   # dev: http://localhost:4200
# or, single-origin production:
cd frontend && npm run build && cd .. && uvicorn app.main:app   # http://localhost:8000/
```

## Tests

```bash
pytest
```

The tests build a small Faroese invoice in memory (`reportlab`) and assert that
fields, dates, vendor, and line items are extracted with positions, the template
engine (label/region strategies), vendor/output CRUD + V-tal detection, the HTTP
endpoints, plus the SaaS layer — auth tokens, output profiles, the four
exporters, and the central "map once, everyone benefits" auto-learning.

## Roadmap

- Optional local LLM mode (Ollama) as a fallback for layouts the heuristics miss,
  staying fully local and free.
- Amount/total fields and currency detection.
- Richer line-item mapping per vendor (column templates).
