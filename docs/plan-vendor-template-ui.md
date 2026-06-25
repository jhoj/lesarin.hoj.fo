# Plan: Vendor template UI (Angular + SQLite) — teach the reader per vendor

## Context

v1 (built and pushed) is a generic heuristic invoice reader exposed as a FastAPI
service: upload a PDF, get located fields each with a page + bbox. It guesses across
any layout — fine as a first pass, but never "just works" for a given vendor.

The user wants a **per-vendor setup tool**. A human teaches the reader once per vendor:
name the vendor (identified by a V-tal/ID such as `Vtal: 314188` → "Effo"), and map the
**source labels on that vendor's invoice** to the **customer's expected output fields**
(e.g. source `"Veitara nr."` → output `"VendorNumber"`), refining each field's box on the
rendered PDF. That mapping is saved as a vendor template in SQLite. Afterwards the reader
auto-detects the vendor and applies the template, "just running" until the vendor changes
their layout — at which point the human reopens the UI, sees what broke, drags a box / fixes
a label, and re-saves.

Stack chosen by the user:
- **Frontend: Angular** (Angular CLI app in `frontend/`).
- **Persistence: SQLite** — vendor configs, a **setup table** for the customer's expected
  output format, and per-vendor field mappings.
- Backend stays **FastAPI** (extends the existing service).

## Data model (SQLite, via SQLAlchemy)

DB file `data/lesarin.db`; tables created on startup.

- `output_fields` — the customer's expected output format (the "setup" table):
  `id, key (e.g. "VendorNumber"), display_name, value_type (string|date|number), sort_order`.
- `vendors` — `id, identifier (V-tal, e.g. "314188"), identifier_kind, name (e.g. "Effo"),
  match_keywords (json), created_at, updated_at`.
- `field_mappings` — per vendor, maps a source label/region on the PDF to an output field:
  `id, vendor_id (fk), output_field_id (fk), source_label (e.g. "Veitara nr."),
  strategy (label|region), page, x0, top, x1, bottom, relation (right|below), value_type`.
- `line_configs` (optional, later) — per-vendor line-item table config.

`app/db.py` (engine/session), `app/db_models.py` (the ORM models above),
`app/repo.py` (CRUD + `detect_vendor(text)` matching identifier/keywords).

## Backend (extend FastAPI, routes under `/api`)

Parse the PDF **once**, then re-run extraction cheaply as the user tweaks the template.

- `app/document_store.py` — in-memory cache `doc_id → {bytes, Document, ts}` (TTL eviction).
- `app/extraction/template.py` — apply a vendor template to a parsed `Document`, producing
  the same located-`Field` results (value, page, bbox, confidence, source) as v1:
  - *label* strategy: find `source_label` words on the page, read the value in the `relation`
    direction within a tolerance band (robust to vertical shifts).
  - *region* strategy: read words whose center falls inside the saved box.
  - Date/number normalization reuses `app/extraction/dates.py` + numeric parsing.
  - Output fields without a mapping fall back to the v1 heuristic extractor, so Auto-read
    still attempts everything; each result is tagged `source`
    (`template-label` / `template-region` / `heuristic`).

Endpoints:
- `POST /api/documents` (multipart) → `{doc_id, n_pages, pages:[{width,height}], detected_vendor}`.
- `GET  /api/documents/{doc_id}/file` → raw PDF bytes (Angular renders with pdfjs-dist).
- `POST /api/documents/{doc_id}/read` (JSON `{template}`) → located fields + line items +
  per-field `source`. Drives Auto-read and every Retry — no re-parse, no save.
- `GET/POST/PUT /api/vendors`, `GET /api/vendors/{id}` → list / create / update (name,
  identifier, mappings).
- `GET/POST/PUT/DELETE /api/output-fields` → manage the expected-output setup table.
- Existing `POST /extract` becomes template-aware (detect vendor → apply template, else
  heuristics) so `scripts/extract_folder.py` benefits once templates exist.
- Enable CORS for the Angular dev server (`http://localhost:4200`).

## Frontend (Angular, `frontend/`)

Angular CLI app (standalone components, Angular 17+), `HttpClient` to `/api`,
`proxy.conf.json` proxying `/api` → `http://localhost:8000` during `ng serve`.
PDF rendered with `pdfjs-dist`; boxes via Angular CDK drag-drop + a resize handle.

- `ApiService` — typed calls to the backend.
- `VendorListComponent` — vendors (`identifier — name`), new/select; highlights the
  auto-detected vendor after upload.
- `OutputFormatComponent` — edit the expected-output setup table (add/rename output fields).
- `PdfViewerComponent` — renders the PDF page(s) to a canvas, hosts box overlays; selecting
  a field selects its box and vice-versa. Boxes stored in **PDF points** (top-left origin,
  matching the loader); map to/from canvas pixels via `scale = canvasWidthPx / pageWidthPts`.
- `MappingPanelComponent` — a row per output field: strategy toggle (label/region),
  `source_label` input, the read value, confidence badge; **Auto-read / Retry** and **Save**.

Workflow: upload → auto-detect vendor → if a template exists, load it and auto-read,
drawing boxes + values; refine labels/boxes → **Retry** (re-reads via cached doc) →
**Save**. Re-uploading a known vendor's invoice is auto-detected and auto-read.

Build: `ng build` emits static files; in production FastAPI serves them via `StaticFiles`.

## Files

- Backend new: `app/db.py`, `app/db_models.py`, `app/repo.py`, `app/document_store.py`,
  `app/extraction/template.py`, `data/.gitkeep`.
- Backend modify: `app/main.py` (new routers, CORS, optional static mount),
  `app/models.py` (pydantic: Vendor, FieldMapping, OutputField, ReadResult, DocumentInfo),
  `requirements.txt` (add `SQLAlchemy`), `.gitignore` (ignore `data/lesarin.db`).
- Frontend new: `frontend/` Angular app (components/services above, `proxy.conf.json`).
- Tests: `tests/test_repo.py` (vendor/output-field/mapping CRUD + V-tal detection),
  `tests/test_template.py` (label & region extraction + date normalization against the
  in-memory sample invoice). Frontend: a smoke `ng test` on `ApiService`.
- `README.md` — Angular dev/build steps, SQLite note, the teach-a-vendor workflow.

## Verification

1. `pytest` — repo CRUD + V-tal detection and template extraction (label finds a value;
   region reads a drawn box; dates normalize) pass.
2. Backend: `uvicorn app.main:app --reload`. Frontend: `cd frontend && npm install &&
   ng serve`. Open `http://localhost:4200`:
   - Define an output field `VendorNumber`; upload the sample; create vendor "Effo"
     (V-tal `314188`); map `VendorNumber` ← source label, drag its box, **Retry**, confirm
     value + box align; **Save**.
   - Re-upload → vendor auto-detected, template applied, fields populated.
3. Box overlays line up with the rendered PDF across pages/zoom (coordinate mapping).
4. `scripts/extract_folder.py` returns template results for known vendors, heuristics for
   unknown ones.

## Note on the "fast/free/local" priority

Angular + SQLite are still fully local and free (no paid services), but Angular adds a
Node/`npm`/Angular-CLI toolchain and a build step — a heavier setup than the v1 service.
Flagging only so it's a deliberate trade-off; proceeding with Angular as requested.
