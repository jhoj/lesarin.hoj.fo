"""ORM models for the central knowledge service — the concrete form of
``docs/brain-sync.md``.

* ``Site`` / ``EnrollmentToken`` — the trust plane: which deployments are
  allowed to push/pull, keyed by their Ed25519 public-key fingerprint. Only
  an active, enrolled site can push at all — that is the *only* gate.
* ``VendorTemplate`` — a supplier's layout → canonical-field mapping, keyed
  on **identifier + layout fingerprint** (one V-tal, several document
  layouts). Published the moment an active site pushes it — brain-sync.md is
  explicit that publishing needs no approval from the centre, because only
  mappings a human already confirmed at a site are ever pushed
  (``app.brain.export_push_bundle`` only ever ships
  ``FieldMapping.confirmed`` rows). "Last confirmation wins for that
  layout": a push for an identifier+fingerprint that already exists replaces
  it, versioned.
* ``LabelObservation`` — the shared vocabulary ("Fakturanr generally means
  invoice number"), gated by a k-anonymity threshold: a (label, canonical
  field) pairing is only *trusted* once ``k`` independently enrolled sites
  have reported it, so one deployment's mistaken or idiosyncratic pairing
  can't pollute what every other site pulls. (Labels are already allowlisted
  against a shared, known vocabulary before they're ever harvested — see
  ``app/fingerprint.py`` — so, unlike a genuinely free-text token, the label
  text itself isn't confidential; the threshold here is a trust gate on the
  *pairing*, not string secrecy. Documented simplification vs. the stricter
  hash-until-k language in brain-sync.md's "Harvesting" section.)
* ``OutputNamePreset`` — the second k-anonymity vocabulary
  (docs/brain-sync.md "A third kind: what customers do with the data"): which
  key customers rename a canonical field to, e.g. ``InvoiceNo`` → ``Bilagsnr``.
  Unlike a label (already allowlisted against a known vocabulary before it's
  ever harvested), an output name is genuinely free text a customer chose, so
  this table holds only a hash and a count until ``k`` sites independently
  report the identical name — at that point it's a convention, not a private
  detail, and the plaintext is kept.
* ``Admin`` — the back-office's own team accounts (site enrollment, manual
  template withdrawal, monitoring) — not a publishing gatekeeper.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Site(Base):
    """A self-hosted (or SaaS) deployment trusted to push/pull."""

    __tablename__ = "sites"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    public_key: Mapped[str] = mapped_column(Text)  # PEM
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|active|revoked
    created_at: Mapped[datetime] = mapped_column(default=_now)
    activated_at: Mapped[Optional[datetime]] = mapped_column(default=None)


class EnrollmentToken(Base):
    """A one-time token an admin mints out-of-band and hands to a new site's
    operator, so the site can register its public key exactly once."""

    __tablename__ = "enrollment_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # sha256 hex
    note: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(default=_now)
    used_at: Mapped[Optional[datetime]] = mapped_column(default=None)
    used_by_site_id: Mapped[Optional[int]] = mapped_column(default=None)


class VendorTemplate(Base):
    """A confirmed supplier template, published the moment it's pushed.

    ``mappings`` mirrors the confirmed-mapping shape a site pushes (see
    ``app.brain.export_push_bundle``) so it flows back out unchanged.
    """

    __tablename__ = "vendor_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    identifier: Mapped[str] = mapped_column(String(64), index=True)
    identifier_kind: Mapped[str] = mapped_column(String(16), default="vtal")
    layout_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(256))
    match_keywords: Mapped[Optional[list]] = mapped_column(JSON, default=list)
    mappings: Mapped[list] = mapped_column(JSON, default=list)
    version: Mapped[int] = mapped_column(default=1)
    contributed_by_fingerprint: Mapped[str] = mapped_column(String(64))
    # Manual withdrawal (docs/brain-sync.md "Withdrawing") — set by an admin,
    # e.g. after seeing reads against this template stop reconciling across
    # customers. A withdrawn template is excluded from pulls; the next
    # confirmation for this identifier+fingerprint replaces it.
    withdrawn_at: Mapped[Optional[datetime]] = mapped_column(default=None)
    published_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)

    __table_args__ = (
        UniqueConstraint("identifier", "identifier_kind", "layout_fingerprint", name="uq_template_shape"),
    )


class TemplateOutcome(Base):
    """One site's latest snapshot of how a vendor's template-sourced exports
    have been reconciling (docs/brain-sync.md "Withdrawing" / Stage G).

    Upserted (replaced, not accumulated) on every push, so this always holds
    each site's *current* picture — summing across distinct sites is what
    lets central tell "one customer having a bad day" apart from "reads are
    failing across customers", which is the evidence a template gets
    auto-withdrawn on (see ``central/sync.py::_merge_outcomes``).

    Tracked per vendor identifier, not per layout fingerprint — the site-
    local ``export_records`` table this is drawn from
    (``app.brain._recent_outcome``) doesn't record which layout a read
    matched, only which vendor. Documented simplification: a withdrawal
    applies to every published template for that identifier.
    """

    __tablename__ = "template_outcomes"

    id: Mapped[int] = mapped_column(primary_key=True)
    identifier: Mapped[str] = mapped_column(String(64), index=True)
    identifier_kind: Mapped[str] = mapped_column(String(16), default="vtal")
    site_fingerprint: Mapped[str] = mapped_column(String(64))
    valid_count: Mapped[int] = mapped_column(default=0)
    invalid_count: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)

    __table_args__ = (
        UniqueConstraint("identifier", "identifier_kind", "site_fingerprint", name="uq_outcome_site"),
    )


class LabelObservation(Base):
    """One (label, canonical field) pairing, counted across distinct sites.

    ``fingerprints`` is the list of distinct contributing site fingerprints
    (brain-sync.md's "customers, not installations" — a real per-customer
    count needs an organisation identity layer this service doesn't have, so
    counting distinct *sites* is a documented approximation). ``revealed``
    flips true once ``len(fingerprints)`` reaches ``CENTRAL_VOCAB_K``
    (default 5) — only revealed pairings are published back to sites.
    """

    __tablename__ = "label_observations"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(256))
    suggested_key: Mapped[Optional[str]] = mapped_column(String(128), default=None)
    fingerprints: Mapped[list] = mapped_column(JSON, default=list)
    revealed: Mapped[bool] = mapped_column(default=False)
    first_seen_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)

    __table_args__ = (UniqueConstraint("label", "suggested_key", name="uq_label_observation"),)

    @property
    def contributing_fingerprints(self) -> List[str]:
        return list(self.fingerprints or [])


class OutputNamePreset(Base):
    """One (canonical field, output name) pairing, counted across distinct
    sites the same way as ``LabelObservation``. ``name_hash`` is
    ``sha256(canonical + output_name)`` — the plaintext name is never stored
    until ``revealed`` flips true, because unlike a label this string is
    genuinely free text one customer chose and could itself identify them
    (see the module docstring)."""

    __tablename__ = "output_name_presets"

    id: Mapped[int] = mapped_column(primary_key=True)
    canonical: Mapped[str] = mapped_column(String(64), index=True)
    name_hash: Mapped[str] = mapped_column(String(64))
    revealed_name: Mapped[Optional[str]] = mapped_column(String(128), default=None)
    fingerprints: Mapped[list] = mapped_column(JSON, default=list)
    revealed: Mapped[bool] = mapped_column(default=False)
    first_seen_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)

    __table_args__ = (UniqueConstraint("canonical", "name_hash", name="uq_output_name_preset"),)

    @property
    def contributing_fingerprints(self) -> List[str]:
        return list(self.fingerprints or [])


class Admin(Base):
    """A human back-office account: site enrollment/activation, manual
    template withdrawal, monitoring — never a publishing gatekeeper (brain-
    sync.md: "nobody at the centre has to approve anything"). MFA reuses the
    same TOTP scheme as the per-tenant SaaS (``app/auth.py``), kept separate
    here since this is a different product with its own accounts."""

    __tablename__ = "admins"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    token_version: Mapped[int] = mapped_column(default=0)
    mfa_secret: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    mfa_confirmed_at: Mapped[Optional[datetime]] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(default=_now)
