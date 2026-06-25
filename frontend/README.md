# Lesarin — vendor template editor (Angular 22)

The web UI for teaching the reader per vendor: upload an invoice, see the located
values boxed on the page, map each expected output field to a source label (or drag a
box), and save the template for that vendor. Talks to the FastAPI backend under `/api`.

Built with Angular 22 (standalone components, signals, zoneless change detection) and
[`pdfjs-dist`](https://www.npmjs.com/package/pdfjs-dist) `4.x` for rendering.

## Prerequisites

- **Node.js 22.22.3+ or 24.15+** (Angular 22 CLI requires it).
- The backend running on `http://localhost:8000` (see the repo root README).

## Develop

```bash
npm install
npm start          # ng serve on http://localhost:4200
```

`proxy.conf.json` proxies `/api` → `http://localhost:8000`, so run the backend
(`uvicorn app.main:app --reload`) alongside `ng serve`.

## Build for production

```bash
npm run build      # outputs dist/lesarin/browser/
```

The FastAPI app serves that folder at `/` automatically when it exists, so a built UI
and the API run from a single origin (no CORS, no separate server):

```bash
npm run build
cd .. && uvicorn app.main:app   # open http://localhost:8000/
```

## How the pieces map

| File | Role |
|------|------|
| `src/app/api.ts` | typed `HttpClient` wrapper over `/api` |
| `src/app/models.ts` | interfaces mirroring the backend JSON |
| `src/app/pdf-viewer.ts` | renders the PDF to a canvas; draggable/resizable box overlays (boxes in PDF points) |
| `src/app/app.ts` / `app.html` | orchestration: vendors, expected-output setup, mappings, the read/refine loop |

Boxes are stored in **PDF points** (top-left origin) to match the backend loader; the
viewer maps them to/from canvas pixels via `scale = canvasWidthPx / pageWidthPts`.
