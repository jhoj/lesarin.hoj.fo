# Plan: Hardened identity + a central vendor-knowledge service

## Context

Today auth is **password-only** (`app/auth.py`: PBKDF2-HMAC-SHA256 + stateless
HMAC bearer tokens). The product is meant to become a **must-have in invoice-flow
automation**, and the vendor mappings are meant to live in a **central knowledge
store** that every deployment contributes to and benefits from.

That goal implies three distinct identity planes — humans, automation clients,
and *deployed sites* — and a central service that is a different product from the
per-tenant SaaS. The user's framing ("central holds a public key, deployment
generates a private key, the fingerprint is stored centrally so mappings can be
saved from all sites") is the SSH/mTLS trust model for site→central sync. The
user also wants a human **annotation back-office** at the centre, and a strategy
to **acquire invoices** and judge whether one is **valid**.

### Decisions taken (interactive picker failed — these are defaults, confirm/redirect on review)
- **Topology:** self-hosted sites + a central service (the hosted multi-tenant
  SaaS is just one privileged "site"). This is the only topology that matches the
  keypair description.
- **Auth methods (phased):** API keys + TOTP MFA first; WebAuthn/passkeys and
  OIDC/SSO later.
- **Contribution privacy:** *layout fingerprints only* by default (labels +
  normalised positions, never sensitive values); full invoices only on explicit
  opt-in, later.
- **"Valid invoice":** all four layers, phased — structural/arithmetic →
  is-it-an-invoice → e-invoice schema/signature → duplicate/fraud.

## Architecture overview

```
 Humans ─password+TOTP/passkey/OIDC─┐
 Automation ─API key / OAuth cc─────┤── per-tenant SaaS (a "site")
                                    │        │  signed sync
 Self-hosted site ─Ed25519 keypair──┴────────┴────────────► CENTRAL knowledge service
   (private key on disk)        public-key fingerprint        - vendor templates (versioned, provenance)
                                  registered centrally         - layout fingerprints
                                                               - annotation queue + review/publish
                                                               - validation rules
```

Three credential types, one new central service, four validation layers.

## Milestones

### M1 — Auth hardening (per-tenant SaaS)  [`app/auth.py`, `app/saas.py`, `app/db_models.py`]
- **API keys** for automation: new `ApiKey` model (id, user_id, name, hashed key
  `sha256`, prefix for display, scopes, last_used, revoked_at). New dependency
  `api_key_or_user` that accepts `Authorization: Bearer lk_…` *or* a session
  token. Issue/list/revoke under `/api/me/api-keys`. This is the credential an
  invoice-flow automation uses against `/api/me/export`.
- **TOTP MFA**: `MfaCredential` model (user_id, secret, confirmed_at,
  recovery_codes hashed). Enroll (`/api/me/mfa` returns otpauth URI), verify, and
  require a TOTP step at login when enabled. New deps: `pyotp`, `qrcode`.
- **Login safety**: rate-limit + lockout on repeated failures; token **revocation**
  via a `token_version` column bumped on logout/password-change (verified in
  `parse_token`); shorten access-token TTL and add a refresh path.
- Reuse: existing `hash_password`/`verify_password`, `make_token`/`parse_token`
  (extend payload with `tv` token-version and `mfa` flag).

### M2 — Site identity & central trust  [new `central/` service, `site/` agent]
- **Site keypair**: on first boot a deployment generates an **Ed25519** keypair,
  private key stored at `/var/lib/lesarin/site_key` (never leaves the box). The
  fingerprint = `SHA256(pubkey)`.
- **Enrollment**: site submits its public key + a one-time **enrollment token**
  (minted by you in central admin). Central stores `Site{id, name, public_key,
  fingerprint, status: pending|active|revoked}`; an admin activates it. Revoke =
  flip status (kills all that site's trust instantly).
- **Request auth (site→central)**: site sends a short-lived **EdDSA JWT** signed
  with its private key, `kid` = fingerprint. Central looks up the pubkey by
  fingerprint, verifies the signature, checks `status==active`. New deps:
  `cryptography`, `PyJWT[crypto]`. (This is the asymmetric plane — deliberately
  separate from the stdlib-only human-auth plane.)

### M3 — Central knowledge service + sync  [`central/` FastAPI app, own DB]
- Separate deployable (same repo, own SQLite/Postgres). Schema:
  `VendorKnowledge` (canonical template, **versioned**, `status:
  unverified|verified|published`, confidence, provenance = contributing
  fingerprints + counts), `LayoutFingerprint` (label set + normalised positions,
  to match invoices to a template even without an explicit vendor id).
- **Sync API** (all signed per M2): sites **pull** published templates by vendor
  identifier / fingerprint match, and **push** learned mappings or fingerprints.
- **Promotion rule**: a contributed mapping is `unverified` until either a human
  reviews it or **N independent sites corroborate** it → `verified` → `published`.
  Sites pull only `published` by default.
- Wire the existing auto-learn (`app/saas.py: build_canonical` /
  `_maybe_learn_vendor`) to *push to central* instead of only writing locally.

### M4 — Annotation back-office (manual mapping)  [extend the Angular studio]
- The existing `/studio` (PDF viewer + drag-box mapping editor) is the seed. Add a
  **task queue** (`AnnotationTask`: vendor identifier, sample fingerprint, status,
  assignee), a **review/approve/publish** workflow, and team accounts (humans with
  MFA from M1). This is where your manual labour produces `verified` templates.

### M5 — Validation layers  [new `app/validation/`]
1. **Structural + arithmetic**: required canonical fields present; lines sum to
   net; net + VAT = gross (tolerance); VAT-id/V-tal format + checksum (Faroese
   V-tal; optional EU VIES lookup); ISO currency; sane dates. Reuse
   `app/extraction/template.py:_normalise_number` and `app/canonical.py`.
2. **Is-it-an-invoice**: an invoice-likelihood score from label density +
   presence of invoice-no/date/total (heuristic now, ML later).
3. **E-invoice**: validate UBL/OIOUBL/PEPPOL against XSD + Schematron, verify
   digital signatures, detect Factur-X/ZUGFeRD embedded XML. (Complements the
   exporters already in `app/exporters.py`.)
4. **Duplicate / fraud**: unique `(tenant, vendor, invoice_no)` duplicate
   detection + amount-anomaly flags — important once this drives a payment flow.
- Surface results as a `validation` block on the export/read responses.

### M6 — Invoice acquisition  [`central/ingest/`]
- **Fingerprint contribution** (default, privacy-safe): sites push layout
  fingerprints, not values.
- **Synthetic bootstrap**: generalise the reportlab generator already in
  `tests/conftest.py` to emit many vendor layouts to seed templates.
- **Structured sources**: PEPPOL / e-invoice partnership (pre-validated UBL),
  public invoice datasets, direct high-volume-vendor onboarding.

## New dependencies
`pyotp`, `qrcode` (MFA); `cryptography`, `PyJWT[crypto]` (site identity / EdDSA);
later `py_webauthn` (passkeys), `lxml` + PEPPOL Schematron (e-invoice validation).
The human-auth plane stays stdlib-only; asymmetric crypto is isolated to the
site/central plane.

## Verification
- **M1**: unit tests for API-key issue/auth/revoke and TOTP enroll/verify;
  `/api/me/export` works with an API key and is rejected after revoke; login
  requires TOTP when enabled; revoked/old-version tokens fail `parse_token`.
- **M2/M3**: a test "site" generates a keypair, enrolls, and a signed pull/push
  round-trips; a tampered signature and a revoked site are both rejected;
  corroboration promotes a mapping to `published`.
- **M5**: arithmetic validator catches a non-reconciling total; UBL validates
  against XSD; duplicate invoice number is flagged.
- **E2E**: self-hosted site auto-learns a vendor → pushes to central → second
  site pulls the published template and exports correctly first try.

## Open questions to confirm before building
1. Topology — confirm self-hosted-sites + central (vs central SaaS only).
2. Auth priority order — is API-keys-for-automation the #1, with MFA close behind?
3. Central DB — SQLite to start, or Postgres now (multi-writer central)?
4. Privacy — is "layout fingerprints only" acceptable as the default contribution?
5. Validity scope for v1 — which of the four layers ship first?
