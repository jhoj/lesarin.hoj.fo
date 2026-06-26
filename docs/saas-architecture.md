# SaaS architecture — "map once, centrally"

Lesarin started as a single-tenant invoice reader. This note describes the
multi-tenant layer added on top and, in particular, the design that lets an
upload "just work" without the customer ever touching a mapping.

## The three layers

```
            shared, central                         per customer
  ┌───────────────────────────────────┐   ┌──────────────────────────────┐
  │  Canonical vocabulary             │   │  Output profile              │
  │  (app/canonical.py)               │   │  (output_profiles /          │
  │  InvoiceNo, InvoiceDate, DueDate, │   │   profile_fields)            │
  │  VendorName, VendorNo, Currency,  │   │  canonical → your key,       │
  │  TotalExclVat, Vat, TotalInclVat, │   │  + format (json/xml/ubl/...) │
  │  AccountNo                        │   └──────────────────────────────┘
  │                                   │
  │  Vendor templates                 │
  │  (vendors / field_mappings)       │
  │  layout → canonical, per supplier │
  └───────────────────────────────────┘
```

* **Canonical vocabulary** is a fixed, small set of semantic fields. It is the
  contract the other two layers agree on. Keeping it fixed is what lets a
  template authored by one customer be consumed by another.
* **Vendor templates** map a supplier's specific layout onto canonical fields.
  They live in the shared store and are keyed by the supplier's identifier
  (V-tal) — never by customer. One template per supplier, globally.
* **Output profiles** are the only thing a customer configures: which canonical
  fields they want, renamed to their keys, and the export format.

## The request path (`POST /api/me/export`)

1. Parse the PDF (`extraction/loader`).
2. Detect the supplier from the document text (`repo.detect_vendor`).
3. If a **central** template exists, apply it → canonical values.
4. Fill any gaps from layout heuristics (`template.field_suggestions`). This is
   why a never-seen supplier still yields useful output the first time.
5. **Auto-learn**: if the supplier was identifiable (has a V-tal) but unknown,
   persist a new central template derived from the heuristic locations — tagged
   with the contributing account for provenance only. The next customer to
   upload that supplier gets a clean template hit.
6. Project the canonical values through the customer's output profile and render
   in their format (`exporters.render`).

See [`app/saas.py`](../app/saas.py) `build_canonical` for the implementation.

## Why provenance, not ownership

`vendors.created_by_user_id` records who first taught a template, for audit. It
is **never** used for access control: the store is deliberately shared so that
every customer's first upload improves the service for the next. Customers can't
see or edit templates through the SaaS surface at all — they only see their own
profiles and their own exported data.

## Auth

Dependency-free by design (`app/auth.py`): PBKDF2 password hashing and
HMAC-signed, stateless bearer tokens, both from the standard library. No native
crypto build and no session table to evict. Pin `LESARIN_SECRET` in production;
otherwise a random secret is generated and persisted beside the SQLite file.
