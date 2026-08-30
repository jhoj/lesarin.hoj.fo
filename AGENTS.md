# AGENTS.md

## Commands

- Backend deps: `python -m pip install -r requirements.txt`; `bash scripts/setup.sh` also tries to install OCR system packages (`tesseract-ocr`, `tesseract-ocr-dan`, `poppler-utils`).
- Backend dev server: `uvicorn app.main:app --reload`.
- Backend tests: `pytest -q`; in this workspace the venv binary may be needed: `.venv/bin/pytest -q`.
- Focused backend test: `.venv/bin/pytest tests/test_template.py -q` or `.venv/bin/pytest tests/test_api.py::test_full_teach_and_read_flow -q`.
- Frontend requires Node 24 (`frontend/.nvmrc`) and npm. CI uses `cd frontend && npm ci && npm run build`.
- Frontend dev: run backend on `localhost:8000`, then `cd frontend && npm install && npm start`; `frontend/proxy.conf.json` proxies `/api` to the backend.
- Production-style local UI: `cd frontend && npm run build`, then `uvicorn app.main:app`; FastAPI serves `frontend/dist/lesarin/browser` at `/` when present.

## Architecture Notes

- `app/main.py` is the FastAPI entrypoint. It initializes SQLite in lifespan, includes `app/api.py` and `app/saas.py`, and still exposes legacy root `POST /extract`.
- Do not confuse extraction endpoints: root `POST /extract` returns the old `InvoiceResult` shape but overlays compatible saved template fields; `POST /api/extract` returns the template-oriented `ReadResult` shape.
- Vendor-template studio API lives in `app/api.py`: upload/cache PDFs, output-field CRUD, vendor CRUD, template read/retry.
- SaaS API lives in `app/saas.py`: auth, output profiles, `/api/me/export`; `build_canonical()` applies central vendor templates, fills gaps with heuristics, and may auto-learn a vendor template.
- Extraction boundaries: `app/extraction/loader.py` parses PDF/OCR into positioned words, `fields.py` reads scalar heuristic fields, `lines.py` reads line items, `template.py` applies taught label/region mappings.
- SQLite default is `data/lesarin.db`; override with `LESARIN_DB`. There is no migration tool: additive schema changes are handled in `app/db.py::_ensure_columns()` and canonical fields are seeded by `init_db()`.
- Auth is stdlib-only. `LESARIN_SECRET` pins token signing; otherwise a random secret is persisted as `data/.lesarin-secret` beside the DB.

## Frontend Notes

- Angular routes are `/login`, `/app` for customer export, and `/studio` for vendor template editing.
- PDF boxes are stored in PDF points with a top-left origin to match backend bboxes; `frontend/src/app/pdf-viewer.ts` maps them to canvas pixels by scale.
- `pdfjs-dist` worker is copied to `/pdf.worker.min.mjs` by `frontend/angular.json`; keep that asset rule if changing the PDF viewer/build.
- No frontend spec files currently exist; `npm run build` is the useful frontend verification step.

## Testing Gotchas

- `tests/conftest.py` sets `LESARIN_DB` to a temp SQLite file before app imports; avoid importing `app.db` earlier in new test tooling.
- Tests generate digital sample invoices in memory with ReportLab; they do not require committed PDF fixtures or OCR system packages for the normal suite.
- If testing scanned-PDF/OCR behavior manually, Windows can use `TESSERACT_CMD` and `POPPLER_PATH` instead of editing `PATH`.

## Deploy

- `.github/workflows/deploy.yml` runs on pushes to `main`: build Angular with Node 24, install Python deps and `pytest -q`, rsync excluding `data`, then run `deploy/deploy.sh` on the VPS.
- The systemd service uses one uvicorn worker (`deploy/lesarin.service`) to avoid SQLite write contention; move off SQLite or revisit workers before scaling concurrency.
