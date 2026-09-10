"""Apply a pushed bundle to central's knowledge store, and build the bundle a
site pulls back — ``docs/brain-sync.md`` made concrete.

Speaks the bundle shape built by ``app.brain.export_push_bundle`` /
consumed by ``app.brain.merge_pull_bundle``: only human-confirmed templates
and anonymous label observations, never a full local database dump.

No approval gate for templates: a template a site pushes is published the
moment it lands, because the confirmation already happened at the site (only
``FieldMapping.confirmed`` rows are ever included in a push). What *is*
gated is the shared **vocabulary** — a (label, canonical field) pairing only
gets revealed back to every site once enough independently enrolled sites
have reported it, so one deployment's mistake can't pollute everyone else's
guesses.
"""

from __future__ import annotations

import os
from typing import List

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import LabelObservation, Site, VendorTemplate

BUNDLE_VERSION = 1  # matches app.brain.BRAIN_BUNDLE_VERSION


def _vocab_k() -> int:
    """"k" in brain-sync.md's "Harvesting everything without leaking
    anything" — how many distinct sites must independently report a (label,
    field) pairing before it's trusted enough to publish back to everyone.

    Read at call time, not import time — a module-level constant would lock
    in whichever value happened to be set when this module was first
    imported in the process, which is exactly the kind of import-order
    fragility that bites in tests.
    """
    return int(os.environ.get("CENTRAL_VOCAB_K", 5))


def _merge_template(session: Session, site: Site, spec: dict) -> str:
    """Upsert one template, keyed on identifier + layout fingerprint. Returns
    what happened: "created" or "replaced" ("last confirmation wins for that
    layout" — brain-sync.md)."""
    identifier = spec["identifier"]
    kind = spec.get("identifier_kind", "vtal")
    fingerprint = spec["layout_fingerprint"]

    existing = session.scalar(
        select(VendorTemplate).where(
            VendorTemplate.identifier == identifier,
            VendorTemplate.identifier_kind == kind,
            VendorTemplate.layout_fingerprint == fingerprint,
        )
    )
    if existing is None:
        session.add(VendorTemplate(
            identifier=identifier, identifier_kind=kind, layout_fingerprint=fingerprint,
            name=spec.get("name", identifier), match_keywords=spec.get("match_keywords", []),
            mappings=spec.get("mappings", []), version=1,
            contributed_by_fingerprint=site.fingerprint,
        ))
        return "created"

    existing.name = spec.get("name", existing.name)
    existing.match_keywords = spec.get("match_keywords", existing.match_keywords)
    existing.mappings = spec.get("mappings", existing.mappings)
    existing.version += 1
    existing.contributed_by_fingerprint = site.fingerprint
    existing.withdrawn_at = None  # a fresh confirmation un-withdraws it
    return "replaced"


def _merge_label_observations(session: Session, site: Site, observations: List[dict]) -> int:
    """Count each distinct (label, suggested_key) pairing this site reports.
    Returns how many pairings just crossed the k threshold."""
    newly_revealed = 0
    k = _vocab_k()
    seen_this_push = set()
    for obs in observations:
        for pos in obs.get("positions", []):
            label = (pos.get("label") or "").strip().lower()
            suggested_key = pos.get("suggested_key")
            if not label or (label, suggested_key) in seen_this_push:
                continue
            seen_this_push.add((label, suggested_key))

            row = session.scalar(
                select(LabelObservation).where(
                    LabelObservation.label == label, LabelObservation.suggested_key == suggested_key
                )
            )
            if row is None:
                row = LabelObservation(label=label, suggested_key=suggested_key, fingerprints=[])
                session.add(row)
                session.flush()

            fingerprints = set(row.contributing_fingerprints)
            if site.fingerprint not in fingerprints:
                fingerprints.add(site.fingerprint)
                row.fingerprints = sorted(fingerprints)
                if not row.revealed and len(fingerprints) >= k:
                    row.revealed = True
                    newly_revealed += 1
    return newly_revealed


def apply_push(session: Session, site: Site, bundle: dict) -> dict:
    if bundle.get("bundle_version") != BUNDLE_VERSION:
        raise ValueError(f"unsupported bundle version: {bundle.get('bundle_version')!r}")

    results = [_merge_template(session, site, spec) for spec in bundle.get("confirmed_templates", [])]
    newly_revealed = _merge_label_observations(session, site, bundle.get("label_observations", []))
    session.commit()

    return {
        "templates_created": results.count("created"),
        "templates_replaced": results.count("replaced"),
        "vocabulary_revealed": newly_revealed,
    }


def build_pull_bundle(session: Session) -> dict:
    """Published (non-withdrawn) templates, plus vocabulary that's crossed
    the k-anonymity threshold — grouped by canonical field for
    ``app.brain.merge_pull_bundle`` to union into local aliases."""
    templates = [
        {
            "identifier": t.identifier, "identifier_kind": t.identifier_kind, "name": t.name,
            "match_keywords": list(t.match_keywords or []), "layout_fingerprint": t.layout_fingerprint,
            "mappings": list(t.mappings or []),
        }
        for t in session.scalars(select(VendorTemplate).where(VendorTemplate.withdrawn_at.is_(None)))
    ]

    by_key: dict = {}
    for row in session.scalars(select(LabelObservation).where(LabelObservation.revealed.is_(True))):
        if not row.suggested_key:
            continue
        by_key.setdefault(row.suggested_key, set()).add(row.label)
    vocabulary = [{"key": key, "aliases": sorted(aliases)} for key, aliases in by_key.items()]

    return {"bundle_version": BUNDLE_VERSION, "templates": templates, "vocabulary": vocabulary}
