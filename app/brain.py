"""This site's half of the central brain sync (``docs/brain-sync.md``).

Distinct from :mod:`app.sync` — that module builds a portable bundle for
merging between a customer's *own* installations, everything included, no
gate. This module builds what's eligible to cross the wire to the **shared**
central knowledge service:

* **confirmed templates** — only field mappings a human actually confirmed
  (``FieldMapping.confirmed``), keyed by vendor identifier + a layout
  fingerprint so one supplier's several document layouts don't clobber each
  other,
* **label observations** — the privacy-safe harvest queued by every read
  (``app.fingerprint`` / ``app.repo.add_label_observation``), never a value.
* **output-name observations** — the (canonical field, customer's renamed
  key) pairs already sitting in this site's own output profiles, e.g.
  ``InvoiceNo`` → ``Bilagsnr``. A customer's own choice, never a document
  value, so it's eligible on the same terms as a label.

And merges back what central publishes: templates become local mappings
(already confirmed — they crossed the wire because *someone* confirmed them),
and revealed vocabulary widens local ``OutputField.aliases``.

Also reports **template outcomes** (docs/brain-sync.md "Withdrawing", Stage
G): each push includes, per vendor with a confirmed template, how many of
this site's *template-sourced* exports recently reconciled vs. didn't —
evidence central can use to withdraw a template whose reads are failing
across customers, not just at one site having a bad day.
"""

from __future__ import annotations

import hashlib
from typing import List

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import repo
from .db_models import ExportRecord, OutputField

BRAIN_BUNDLE_VERSION = 1

# How many of a vendor's most recent template-sourced exports count toward
# the outcome snapshot — recent enough to reflect the current layout, not a
# lifetime average that a since-fixed template can never live down.
_OUTCOME_WINDOW = 20


def _template_fingerprint(mappings: List[dict]) -> str:
    """A shape signature from a template's confirmed labels.

    The push-bundle counterpart to ``app.fingerprint.layout_fingerprint``,
    which also folds in real page positions from a live document read —
    stored ``FieldMapping`` rows don't persist page dimensions, so this
    simpler, label-set-only signature is what keys a template centrally. Two
    confirmed sets of labels for the same vendor hash differently, so a
    genuinely different layout adds a template rather than overwriting one.
    """
    labels = sorted({(m.get("label") or "").strip().lower() for m in mappings if m.get("label")})
    return hashlib.sha256(repr(labels).encode("utf-8")).hexdigest()


def _recent_outcome(session: Session, identifier: str) -> dict:
    """Valid/invalid counts among this vendor's most recent
    template-sourced exports at this site."""
    rows = session.scalars(
        select(ExportRecord.valid)
        .where(ExportRecord.vendor_identifier == identifier, ExportRecord.source == "template")
        .order_by(ExportRecord.created_at.desc())
        .limit(_OUTCOME_WINDOW)
    ).all()
    return {"valid": sum(1 for v in rows if v), "invalid": sum(1 for v in rows if not v)}


def export_push_bundle(session: Session) -> dict:
    """Everything this site is ready to contribute right now."""
    templates = []
    outcomes = []
    for vendor in repo.list_vendors(session):
        confirmed = repo.confirmed_mappings_of(vendor)
        if not confirmed:
            continue
        templates.append({
            "identifier": vendor.identifier,
            "identifier_kind": vendor.identifier_kind,
            "name": vendor.name,
            "match_keywords": list(vendor.match_keywords or []),
            "layout_fingerprint": _template_fingerprint(confirmed),
            "mappings": confirmed,
        })
        outcome = _recent_outcome(session, vendor.identifier)
        if outcome["valid"] or outcome["invalid"]:
            outcomes.append({
                "identifier": vendor.identifier, "identifier_kind": vendor.identifier_kind,
                **outcome,
            })

    observations = [
        {
            "identifier": obs.identifier,
            "layout_fingerprint": obs.layout_fingerprint,
            "label_set": list(obs.label_set or []),
            "positions": list(obs.positions or []),
        }
        for obs in repo.list_label_observations(session)
    ]

    output_name_observations = [
        {"canonical": canonical, "output_name": output_name}
        for canonical, output_name in repo.list_output_name_pairs(session)
    ]

    return {
        "bundle_version": BRAIN_BUNDLE_VERSION,
        "confirmed_templates": templates,
        "label_observations": observations,
        "template_outcomes": outcomes,
        "output_name_observations": output_name_observations,
    }


def merge_pull_bundle(session: Session, bundle: dict) -> dict:
    """Merge central's response. Never overwrites a site's own confirmed
    teaching — only fills fields it doesn't confirm yet — and never
    overwrites a site's own alias list, only unions into it."""
    if bundle.get("bundle_version") != BRAIN_BUNDLE_VERSION:
        raise ValueError(f"unsupported brain bundle version: {bundle.get('bundle_version')!r}")

    stats = {"templates_added": 0, "templates_updated": 0, "vocabulary_updated": 0, "presets_updated": 0}

    for spec in bundle.get("templates", []):
        vendor = repo.get_vendor_by_identifier(
            session, spec["identifier"], spec.get("identifier_kind", "vtal")
        )
        # Published centrally because someone confirmed it — at this site or
        # another; either way it's confirmed knowledge, not a guess.
        incoming = [{**m, "confirmed": True} for m in spec.get("mappings", [])]

        if vendor is None:
            repo.create_vendor(
                session, identifier=spec["identifier"], name=spec.get("name", spec["identifier"]),
                identifier_kind=spec.get("identifier_kind", "vtal"),
                match_keywords=spec.get("match_keywords", []), mappings=incoming,
                change="learned",
            )
            stats["templates_added"] += 1
            continue

        have = {m["output"] for m in repo.mappings_of(vendor) if m["confirmed"]}
        new = [m for m in incoming if m["output"] not in have]
        if new:
            repo.update_vendor(
                session, vendor.id, mappings=repo.mappings_of(vendor) + new, change="learned"
            )
            stats["templates_updated"] += 1

    for spec in bundle.get("vocabulary", []):
        key, aliases = spec.get("key"), spec.get("aliases", [])
        if not key or not aliases:
            continue
        existing = session.scalar(select(OutputField).where(OutputField.key == key))
        if existing is None:
            continue  # a canonical field this site hasn't seeded — nothing to widen
        merged = sorted(set(existing.aliases or []) | set(aliases))
        if merged != sorted(existing.aliases or []):
            repo.upsert_output_field(
                session, key, display_name=existing.display_name,
                value_type=existing.value_type, sort_order=existing.sort_order, aliases=merged,
            )
            stats["vocabulary_updated"] += 1

    for spec in bundle.get("output_name_presets", []):
        key, names = spec.get("key"), spec.get("names", [])
        if not key or not names:
            continue
        existing = session.scalar(select(OutputField).where(OutputField.key == key))
        if existing is None:
            continue  # a canonical field this site hasn't seeded — nothing to suggest names for
        merged = sorted(set(existing.preset_names or []) | set(names))
        if merged != sorted(existing.preset_names or []):
            existing.preset_names = merged
            session.commit()
            stats["presets_updated"] += 1

    return stats
