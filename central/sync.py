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

import hashlib
import os
from datetime import datetime, timezone
from typing import List

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import LabelObservation, OutputNamePreset, Site, TemplateOutcome, VendorTemplate

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


def _withdraw_thresholds() -> tuple[int, int, float]:
    """(min distinct sites, min total samples, failure rate) before evidence
    is strong enough to auto-withdraw a template — read at call time for the
    same reason as ``_vocab_k``."""
    return (
        int(os.environ.get("CENTRAL_WITHDRAW_MIN_SITES", 2)),
        int(os.environ.get("CENTRAL_WITHDRAW_MIN_SAMPLES", 3)),
        float(os.environ.get("CENTRAL_WITHDRAW_FAILURE_RATE", 0.5)),
    )


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


def _output_name_hash(canonical: str, output_name: str) -> str:
    return hashlib.sha256(f"{canonical}\x00{output_name}".encode("utf-8")).hexdigest()


def _merge_output_name_presets(session: Session, site: Site, observations: List[dict]) -> int:
    """Count each distinct (canonical, output_name) pairing this site
    reports, hashed so the name itself is never held before ``k`` sites
    independently report the same one. Returns how many pairings just
    crossed the threshold."""
    newly_revealed = 0
    k = _vocab_k()
    seen_this_push = set()
    for obs in observations:
        canonical = (obs.get("canonical") or "").strip()
        name = (obs.get("output_name") or "").strip()
        if not canonical or not name or (canonical, name) in seen_this_push:
            continue
        seen_this_push.add((canonical, name))

        name_hash = _output_name_hash(canonical, name)
        row = session.scalar(
            select(OutputNamePreset).where(
                OutputNamePreset.canonical == canonical, OutputNamePreset.name_hash == name_hash
            )
        )
        if row is None:
            row = OutputNamePreset(canonical=canonical, name_hash=name_hash, fingerprints=[])
            session.add(row)
            session.flush()

        fingerprints = set(row.contributing_fingerprints)
        if site.fingerprint not in fingerprints:
            fingerprints.add(site.fingerprint)
            row.fingerprints = sorted(fingerprints)
            if not row.revealed and len(fingerprints) >= k:
                row.revealed = True
                row.revealed_name = name
                newly_revealed += 1
    return newly_revealed


def _merge_outcomes(session: Session, site: Site, outcomes: List[dict]) -> List[str]:
    """Upsert this site's latest valid/invalid snapshot for each vendor it
    reports on, then check whether the evidence *across all reporting sites*
    now justifies withdrawing that vendor's published templates
    (docs/brain-sync.md "Withdrawing"). Returns the identifiers just
    auto-withdrawn.
    """
    min_sites, min_samples, failure_rate = _withdraw_thresholds()
    touched = set()

    for o in outcomes:
        identifier, kind = o["identifier"], o.get("identifier_kind", "vtal")
        row = session.scalar(
            select(TemplateOutcome).where(
                TemplateOutcome.identifier == identifier, TemplateOutcome.identifier_kind == kind,
                TemplateOutcome.site_fingerprint == site.fingerprint,
            )
        )
        if row is None:
            row = TemplateOutcome(identifier=identifier, identifier_kind=kind, site_fingerprint=site.fingerprint)
            session.add(row)
        row.valid_count = int(o.get("valid", 0))
        row.invalid_count = int(o.get("invalid", 0))
        touched.add((identifier, kind))
    session.flush()

    withdrawn = []
    for identifier, kind in touched:
        rows = list(session.scalars(
            select(TemplateOutcome).where(
                TemplateOutcome.identifier == identifier, TemplateOutcome.identifier_kind == kind
            )
        ))
        total_valid = sum(r.valid_count for r in rows)
        total_invalid = sum(r.invalid_count for r in rows)
        total = total_valid + total_invalid
        if len(rows) < min_sites or total < min_samples or total == 0:
            continue
        if total_invalid / total <= failure_rate:
            continue

        live = list(session.scalars(
            select(VendorTemplate).where(
                VendorTemplate.identifier == identifier, VendorTemplate.identifier_kind == kind,
                VendorTemplate.withdrawn_at.is_(None),
            )
        ))
        if not live:
            continue
        now = datetime.now(timezone.utc)
        for t in live:
            t.withdrawn_at = now
        withdrawn.append(identifier)
    return withdrawn


def apply_push(session: Session, site: Site, bundle: dict) -> dict:
    if bundle.get("bundle_version") != BUNDLE_VERSION:
        raise ValueError(f"unsupported bundle version: {bundle.get('bundle_version')!r}")

    results = [_merge_template(session, site, spec) for spec in bundle.get("confirmed_templates", [])]
    newly_revealed = _merge_label_observations(session, site, bundle.get("label_observations", []))
    presets_revealed = _merge_output_name_presets(session, site, bundle.get("output_name_observations", []))
    auto_withdrawn = _merge_outcomes(session, site, bundle.get("template_outcomes", []))
    session.commit()

    return {
        "templates_created": results.count("created"),
        "templates_replaced": results.count("replaced"),
        "vocabulary_revealed": newly_revealed,
        "presets_revealed": presets_revealed,
        "auto_withdrawn": auto_withdrawn,
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

    presets_by_key: dict = {}
    for row in session.scalars(select(OutputNamePreset).where(OutputNamePreset.revealed.is_(True))):
        presets_by_key.setdefault(row.canonical, set()).add(row.revealed_name)
    output_name_presets = [
        {"key": key, "names": sorted(names)} for key, names in presets_by_key.items()
    ]

    return {
        "bundle_version": BUNDLE_VERSION, "templates": templates, "vocabulary": vocabulary,
        "output_name_presets": output_name_presets,
    }
