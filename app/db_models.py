"""ORM models — the persisted shape of a vendor template.

Three tables:

* ``output_fields`` — the customer's expected output format (the "setup" table).
  These are the keys the reader must ultimately produce, e.g. ``VendorNumber``.
* ``vendors`` — a known supplier, identified by a V-tal / ID and given a name.
* ``field_mappings`` — for one vendor, how to locate the value for one output
  field on that vendor's invoice (by label text and/or a box on the page).

A vendor's mappings *are* its template: teach it once, reuse it every time that
vendor's invoices come in.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import ForeignKey, JSON, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class OutputField(Base):
    """A field the customer expects in the output (the setup table)."""

    __tablename__ = "output_fields"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(128), unique=True)  # e.g. "VendorNumber"
    display_name: Mapped[str] = mapped_column(String(256), default="")
    value_type: Mapped[str] = mapped_column(String(16), default="string")  # string|date|number
    sort_order: Mapped[int] = mapped_column(default=0)
    # "Read labels" — synonyms the value is announced by on invoices, e.g.
    # VendorNumber ← ["Vtal", "V-Tal"]. Used to auto-locate the field on a
    # never-seen vendor. Applies across all vendors (a vendor's own mapping wins).
    aliases: Mapped[Optional[list]] = mapped_column(JSON, default=list)


class User(Base):
    """A SaaS account. Owns output profiles; never owns vendor templates —
    those are shared centrally so one customer's mapping helps the next."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(default=_now)

    profiles: Mapped[List["OutputProfile"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", order_by="OutputProfile.id"
    )


class OutputProfile(Base):
    """A customer's desired output shape: a named set of renamed canonical
    fields plus the export format (json | xml | ubl | oioubl)."""

    __tablename__ = "output_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128), default="Default")
    fmt: Mapped[str] = mapped_column(String(16), default="json")  # json|xml|ubl|oioubl
    is_default: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)

    user: Mapped["User"] = relationship(back_populates="profiles")
    fields: Mapped[List["ProfileField"]] = relationship(
        back_populates="profile",
        cascade="all, delete-orphan",
        order_by="ProfileField.sort_order, ProfileField.id",
    )


class ProfileField(Base):
    """One row of a profile: a canonical field, renamed to the customer's key."""

    __tablename__ = "profile_fields"

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("output_profiles.id", ondelete="CASCADE"), index=True
    )
    canonical: Mapped[str] = mapped_column(String(64))  # e.g. "InvoiceNo"
    output_name: Mapped[str] = mapped_column(String(128))  # customer's label, e.g. "invoice_id"
    sort_order: Mapped[int] = mapped_column(default=0)

    profile: Mapped["OutputProfile"] = relationship(back_populates="fields")


class Vendor(Base):
    """A known supplier and how to recognise it in an uploaded PDF."""

    __tablename__ = "vendors"

    id: Mapped[int] = mapped_column(primary_key=True)
    identifier: Mapped[str] = mapped_column(String(64), index=True)  # e.g. "314188"
    identifier_kind: Mapped[str] = mapped_column(String(16), default="vtal")
    name: Mapped[str] = mapped_column(String(256))  # e.g. "Effo"
    match_keywords: Mapped[Optional[list]] = mapped_column(JSON, default=list)
    # Provenance: which account first taught this template. Nullable — the store
    # is shared, so this is for audit only, never for access control.
    created_by_user_id: Mapped[Optional[int]] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)

    mappings: Mapped[List["FieldMapping"]] = relationship(
        back_populates="vendor",
        cascade="all, delete-orphan",
        order_by="FieldMapping.id",
    )

    __table_args__ = (UniqueConstraint("identifier", "identifier_kind", name="uq_vendor_identifier"),)


class FieldMapping(Base):
    """How to read one output field's value from one vendor's invoice."""

    __tablename__ = "field_mappings"

    id: Mapped[int] = mapped_column(primary_key=True)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id", ondelete="CASCADE"))
    output_key: Mapped[str] = mapped_column(String(128))  # references OutputField.key
    strategy: Mapped[str] = mapped_column(String(16), default="label")  # label|region
    source_label: Mapped[Optional[str]] = mapped_column(String(256), default=None)
    relation: Mapped[str] = mapped_column(String(16), default="right")  # right|below
    value_type: Mapped[str] = mapped_column(String(16), default="string")
    page: Mapped[Optional[int]] = mapped_column(default=None)  # 1-indexed
    # Box (PDF points, top-left origin) — the value region / refined anchor.
    x0: Mapped[Optional[float]] = mapped_column(default=None)
    top: Mapped[Optional[float]] = mapped_column(default=None)
    x1: Mapped[Optional[float]] = mapped_column(default=None)
    bottom: Mapped[Optional[float]] = mapped_column(default=None)

    vendor: Mapped["Vendor"] = relationship(back_populates="mappings")

    @property
    def bbox(self) -> Optional[List[float]]:
        if None in (self.x0, self.top, self.x1, self.bottom):
            return None
        return [self.x0, self.top, self.x1, self.bottom]
