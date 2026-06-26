# Lesarin — web app (Angular 22)

One app, two routed surfaces behind a login:

- **`/app` — customer:** register / log in, build **output profiles** (pick
  canonical fields, rename them to your keys, choose json / xml / ubl / oioubl),
  then upload an invoice and download the result.
- **`/studio` — mapping editor:** upload an invoice, see the located values boxed
  on the page, map each field to a source label (or drag a box), and save the
  vendor template that everyone benefits from.

Talks to the FastAPI backend under `/api`. The bearer token is attached by an
HTTP interceptor (`auth.interceptor.ts`) and `/api/me/*` 401s bounce to `/login`.

Built with Angular 22 (standalone components, signals, the router) and
[`pdfjs-dist`](https://www.npmjs.com/package/pdfjs-dist) `4.x` for rendering.

## Prerequisites

- **Node.js 24** (see [`.nvmrc`](.nvmrc); Angular 22 needs 22.22.3+ or 24.15+).
  With nvm: `nvm install 24 && nvm use`.
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
| `src/app/api.ts` | typed `HttpClient` wrapper over `/api` (studio + SaaS) |
| `src/app/models.ts` | interfaces mirroring the backend JSON |
| `src/app/auth.ts` / `auth.interceptor.ts` / `auth.guard.ts` | token storage, bearer header + 401 handling, route guard |
| `src/app/app.ts` / `app.routes.ts` | root shell (nav + `router-outlet`) and routes |
| `src/app/login.ts` | register / log in |
| `src/app/customer.ts` / `customer.html` | the `/app` surface: output profiles + upload-and-export |
| `src/app/studio.ts` / `studio.html` | the `/studio` surface: vendors, output setup, mappings, the read/refine loop |
| `src/app/pdf-viewer.ts` | renders the PDF to a canvas; draggable/resizable box overlays (boxes in PDF points) |

Boxes are stored in **PDF points** (top-left origin) to match the backend loader; the
viewer maps them to/from canvas pixels via `scale = canvasWidthPx / pageWidthPts`.
