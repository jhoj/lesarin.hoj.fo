"""The review-loop workflow: auto-read a folder, leave results, reprocess.

The intended rhythm for a site processing invoices in bulk:

1. ``process`` a folder — every ``invoice.pdf`` gets an ``invoice.lesarin.json``
   **result sidecar** beside it (the CLI's full Result envelope: status, the
   mapping applied, fields, validation, rendered output).
2. A human reviews the queue (``status``), opens any *incomplete* document in
   the web **studio**, corrects the mapping there, and saves the vendor
   template.
3. ``process`` again — by default only the not-yet-complete documents are
   re-read, so the corrected template flips them to *complete* without
   touching the ones already done.
4. Every now and then, ``python -m app.sync export`` bundles the learned
   templates for syncing to the central store (see :mod:`app.sync`).

Usage:

    python -m app.workflow process INBOX/ --config job.yaml
    python -m app.workflow status INBOX/
    python -m app.workflow process INBOX/ --all      # force re-read everything

Exit codes match the CLI's convention: 0 when every document is complete,
2 when any need review, 1 when any failed outright.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from . import cli

SIDECAR_SUFFIX = ".lesarin.json"


def sidecar_for(pdf: Path) -> Path:
    return pdf.with_name(pdf.stem + SIDECAR_SUFFIX)


def read_sidecar(pdf: Path) -> Optional[dict]:
    path = sidecar_for(pdf)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def process_folder(
    folder: Path,
    config: dict,
    fmt_override: Optional[str] = None,
    require_override: Optional[List[str]] = None,
    only_pending: bool = True,
) -> dict:
    """Read every PDF in the folder, leaving a result sidecar beside each.

    ``only_pending`` (the default) skips documents whose sidecar already says
    *complete* — that's what makes re-running after a studio correction cheap:
    only the documents that still need work are re-read.
    """
    pdfs = sorted(p for p in folder.glob("*.pdf") if p.is_file())
    summary = {"processed": 0, "skipped": 0, "complete": 0, "incomplete": 0, "failed": 0,
               "documents": []}

    for pdf in pdfs:
        existing = read_sidecar(pdf)
        if only_pending and existing is not None and existing.get("status") == "complete":
            summary["skipped"] += 1
            summary["complete"] += 1
            summary["documents"].append({"file": pdf.name, "status": "complete", "skipped": True})
            continue

        report, _code = cli.run(str(pdf), config, fmt_override=fmt_override,
                                require_override=require_override)
        sidecar_for(pdf).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        summary["processed"] += 1
        summary[report["status"]] += 1
        summary["documents"].append({
            "file": pdf.name,
            "status": report["status"],
            "mapped": report.get("mapped", False),
            "missing": report.get("missing_fields", []),
        })

    return summary


def queue_status(folder: Path) -> dict:
    """Summarise the sidecars in a folder: what's done, what needs review."""
    docs = []
    for pdf in sorted(p for p in folder.glob("*.pdf") if p.is_file()):
        report = read_sidecar(pdf)
        if report is None:
            docs.append({"file": pdf.name, "status": "unprocessed"})
            continue
        docs.append({
            "file": pdf.name,
            "status": report.get("status", "unknown"),
            "mapped": report.get("mapped", False),
            "vendor": (report.get("vendor") or {}).get("name"),
            "missing": report.get("missing_fields", []),
            "valid": (report.get("validation") or {}).get("valid"),
        })
    counts: dict = {}
    for d in docs:
        counts[d["status"]] = counts.get(d["status"], 0) + 1
    return {"counts": counts, "documents": docs}


def _exit_code(counts: dict) -> int:
    if counts.get("failed"):
        return 1
    if counts.get("incomplete") or counts.get("unprocessed"):
        return 2
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.workflow",
        description="Batch review loop: auto-read a folder, leave result sidecars, reprocess.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_proc = sub.add_parser("process", help="read every PDF, writing a .lesarin.json beside each")
    p_proc.add_argument("folder")
    p_proc.add_argument("--config", help="JSON/YAML job config (same shape as app.cli)")
    p_proc.add_argument("--db", help="SQLite mapping store (overrides $LESARIN_DB)")
    p_proc.add_argument("--format", dest="fmt", choices=sorted(cli._VALID_FORMATS))
    p_proc.add_argument("--require", help="comma-separated fields required for 'complete'")
    p_proc.add_argument("--all", action="store_true",
                        help="re-read every document, including already-complete ones")

    p_stat = sub.add_parser("status", help="summarise the folder's sidecars")
    p_stat.add_argument("folder")

    args = parser.parse_args(argv)
    folder = Path(args.folder)
    if not folder.is_dir():
        print(f"not a folder: {folder}", file=sys.stderr)
        return 1

    if args.command == "status":
        result = queue_status(folder)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        pending = [d["file"] for d in result["documents"] if d["status"] != "complete"]
        note = f"{len(pending)} document(s) need attention" if pending else "all complete"
        print(note, file=sys.stderr)
        return _exit_code(result["counts"])

    # process
    from .db import init_db, use_database

    if args.db:
        use_database(args.db)
    init_db()

    try:
        config = cli.load_config(args.config)
    except (OSError, ValueError) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    require = [s.strip() for s in args.require.split(",") if s.strip()] if args.require else None
    summary = process_folder(folder, config, fmt_override=args.fmt,
                             require_override=require, only_pending=not args.all)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(
        f"processed {summary['processed']}, skipped {summary['skipped']} — "
        f"{summary['complete']} complete, {summary['incomplete']} incomplete, "
        f"{summary['failed']} failed",
        file=sys.stderr,
    )
    return _exit_code(summary)


if __name__ == "__main__":
    raise SystemExit(main())
