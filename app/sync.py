"""Sync — move the learned "brain" between sites as a portable bundle.

The knowledge a site builds up lives in two tables: ``output_fields`` (the
canonical fields + their learned read-label synonyms) and ``vendors`` /
``field_mappings`` (per-vendor templates). This module packages both as one
JSON **bundle** so "sync to central every now and then" is just:

    python -m app.sync export --out site-$(hostname).json     # at each site
    python -m app.sync import site-*.json                     # at the centre
    # ...and the merged central bundle flows back the same way.

Import is a *merge* by default — safe to run repeatedly, never deletes:
* output fields are upserted; alias lists are unioned,
* unknown vendors are created whole,
* known vendors gain mappings only for output keys they don't already map
  (the local teaching wins). ``--replace`` makes incoming vendors overwrite
  local mappings instead — use it when pulling a curated central bundle.

``import-fields`` is the onboarding shortcut: hand it a customer's field list
(YAML/JSON) and it seeds ``output_fields`` — keys, value types, and synonyms —
so their vocabulary auto-locates on never-seen vendors from day one:

    python -m app.sync import-fields customer-fields.yaml

    # customer-fields.yaml
    fields:
      - key: PONumber
        value_type: string
        aliases: ["PO no", "Purchase order", "Innkeypsnr"]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import yaml
from sqlalchemy.orm import Session

from . import repo
from .db_models import OutputField, Vendor

BUNDLE_VERSION = 1


# --- Export ------------------------------------------------------------------

def export_bundle(session: Session) -> dict:
    """Everything another site needs to read the same invoices."""
    fields = [
        {
            "key": f.key,
            "display_name": f.display_name,
            "value_type": f.value_type,
            "sort_order": f.sort_order,
            "aliases": list(f.aliases or []),
        }
        for f in repo.list_output_fields(session)
    ]
    vendors = [
        {
            "identifier": v.identifier,
            "identifier_kind": v.identifier_kind,
            "name": v.name,
            "match_keywords": list(v.match_keywords or []),
            "mappings": [
                {
                    "output": m.output_key,
                    "strategy": m.strategy,
                    "label": m.source_label,
                    "relation": m.relation,
                    "value_type": m.value_type,
                    "page": m.page,
                    "bbox": m.bbox,
                }
                for m in v.mappings
            ],
        }
        for v in repo.list_vendors(session)
    ]
    return {
        "bundle_version": BUNDLE_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "output_fields": fields,
        "vendors": vendors,
    }


# --- Import ------------------------------------------------------------------

def import_bundle(session: Session, bundle: dict, replace: bool = False) -> dict:
    """Merge a bundle into the local store. Never deletes anything.

    Returns counts of what changed, so a cron job's log line is meaningful.
    """
    if bundle.get("bundle_version") != BUNDLE_VERSION:
        raise ValueError(f"unsupported bundle version: {bundle.get('bundle_version')!r}")

    stats = {"fields_added": 0, "fields_updated": 0,
             "vendors_added": 0, "vendors_updated": 0, "mappings_added": 0}

    for spec in bundle.get("output_fields", []):
        existing = next(
            (f for f in repo.list_output_fields(session) if f.key == spec["key"]), None
        )
        merged_aliases = sorted(set(spec.get("aliases", []))
                                | set(existing.aliases or [] if existing else []))
        repo.upsert_output_field(
            session, spec["key"],
            display_name=spec.get("display_name", ""),
            value_type=spec.get("value_type", "string"),
            sort_order=spec.get("sort_order", 0),
            aliases=merged_aliases,
        )
        stats["fields_updated" if existing else "fields_added"] += 1

    for spec in bundle.get("vendors", []):
        vendor = repo.get_vendor_by_identifier(
            session, spec["identifier"], spec.get("identifier_kind", "vtal")
        )
        if vendor is None:
            repo.create_vendor(
                session,
                identifier=spec["identifier"],
                name=spec.get("name", spec["identifier"]),
                identifier_kind=spec.get("identifier_kind", "vtal"),
                match_keywords=spec.get("match_keywords", []),
                mappings=spec.get("mappings", []),
            )
            stats["vendors_added"] += 1
            stats["mappings_added"] += len(spec.get("mappings", []))
            continue

        if replace:
            repo.update_vendor(
                session, vendor.id,
                name=spec.get("name"),
                match_keywords=spec.get("match_keywords"),
                mappings=spec.get("mappings", []),
            )
            stats["vendors_updated"] += 1
            stats["mappings_added"] += len(spec.get("mappings", []))
        else:
            # Merge: only fill output keys this vendor doesn't map yet — a
            # site's own teaching beats an incoming bundle's version.
            have = {m.output_key for m in vendor.mappings}
            new = [m for m in spec.get("mappings", []) if m["output"] not in have]
            if new:
                combined = [
                    {
                        "output": m.output_key, "strategy": m.strategy,
                        "label": m.source_label, "relation": m.relation,
                        "value_type": m.value_type, "page": m.page, "bbox": m.bbox,
                    }
                    for m in vendor.mappings
                ] + new
                repo.update_vendor(session, vendor.id, mappings=combined)
                stats["vendors_updated"] += 1
                stats["mappings_added"] += len(new)

    return stats


# --- Customer field onboarding -------------------------------------------------

def import_fields(session: Session, spec: dict) -> dict:
    """Seed output fields (+ synonyms) from a customer's field list."""
    fields = spec.get("fields", spec if isinstance(spec, list) else [])
    if not isinstance(fields, list):
        raise ValueError("expected a top-level 'fields:' list")
    count = 0
    for order, f in enumerate(fields):
        repo.upsert_output_field(
            session, f["key"],
            display_name=f.get("display_name", f["key"]),
            value_type=f.get("value_type", "string"),
            sort_order=f.get("sort_order", order),
            aliases=f.get("aliases", []),
        )
        count += 1
    return {"fields": count}


# --- CLI ------------------------------------------------------------------------

def _load(path: str) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.sync",
        description="Export/import the learned mapping bundle; onboard customer fields.",
    )
    parser.add_argument("--db", help="SQLite mapping store (overrides $LESARIN_DB)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_exp = sub.add_parser("export", help="write the site's knowledge bundle")
    p_exp.add_argument("--out", help="output file (default: stdout)")

    p_imp = sub.add_parser("import", help="merge a bundle into this store")
    p_imp.add_argument("bundle", nargs="+", help="bundle file(s) to merge")
    p_imp.add_argument("--replace", action="store_true",
                       help="incoming vendors overwrite local mappings (curated central pull)")

    p_fields = sub.add_parser("import-fields", help="seed output fields from a customer list")
    p_fields.add_argument("spec", help="YAML/JSON file with a 'fields:' list")

    args = parser.parse_args(argv)

    from .db import SessionLocal, init_db, use_database

    if args.db:
        use_database(args.db)
    init_db()

    with SessionLocal() as session:
        if args.command == "export":
            bundle = export_bundle(session)
            text = json.dumps(bundle, ensure_ascii=False, indent=2)
            if args.out:
                Path(args.out).write_text(text, encoding="utf-8")
                print(f"wrote {args.out}: {len(bundle['vendors'])} vendor(s), "
                      f"{len(bundle['output_fields'])} field(s)", file=sys.stderr)
            else:
                print(text)
            return 0

        if args.command == "import":
            total: dict = {}
            for path in args.bundle:
                stats = import_bundle(session, _load(path), replace=args.replace)
                for k, v in stats.items():
                    total[k] = total.get(k, 0) + v
            print(json.dumps(total, indent=2))
            return 0

        # import-fields
        stats = import_fields(session, _load(args.spec))
        print(json.dumps(stats, indent=2))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
