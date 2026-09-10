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
from datetime import datetime, timezone
import json
import math
import os
import sys
import tempfile
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
        report = json.loads(path.read_text(encoding="utf-8"))
        return report if isinstance(report, dict) else None
    except (OSError, ValueError):
        return None


def write_sidecar(pdf: Path, report: dict) -> None:
    """Publish a whole result so a scheduler/exporter never sees half a write."""
    path = sidecar_for(pdf)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".lesarin-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _retry_policy(config: dict) -> tuple:
    retry = config.get("retry", {})
    if not isinstance(retry, dict) or retry.keys() - {"max_attempts", "max_days"}:
        raise ValueError("retry must be an object with max_attempts and/or max_days")
    attempts, days = retry.get("max_attempts"), retry.get("max_days")
    if attempts is not None and (type(attempts) is not int or attempts < 1):
        raise ValueError("retry.max_attempts must be a positive integer")
    if days is not None and (type(days) not in (int, float) or not math.isfinite(days) or days <= 0):
        raise ValueError("retry.max_days must be a positive finite number")
    return attempts, days


def process_folder(
    folder: Path,
    config: dict,
    fmt_override: Optional[str] = None,
    require_override: Optional[List[str]] = None,
    only_pending: bool = True,
    retry_attention: bool = False,
) -> dict:
    """Read every PDF in the folder, leaving a result sidecar beside each.

    ``only_pending`` (the default) skips documents whose sidecar already says
    *complete* — that's what makes re-running after a studio correction cheap:
    only the documents that still need work are re-read. Exhausted documents
    stay at *needs-attention* until ``retry_attention`` or a forced re-read
    explicitly starts a fresh retry budget.
    """
    max_attempts, max_days = _retry_policy(config)
    pdfs = sorted(p for p in folder.glob("*.pdf") if p.is_file())
    summary = {"processed": 0, "skipped": 0, "complete": 0, "incomplete": 0, "failed": 0,
               "needs-attention": 0, "documents": []}

    for pdf in pdfs:
        existing = read_sidecar(pdf)
        status = existing.get("status") if existing else None
        if only_pending and (status == "complete" or (status == "needs-attention" and not retry_attention)):
            summary["skipped"] += 1
            summary[status] += 1
            summary["documents"].append({"file": pdf.name, "status": status, "skipped": True})
            continue

        now = datetime.now(timezone.utc)
        state = existing.get("workflow", {}) if existing and only_pending else {}
        if status == "needs-attention" and retry_attention:
            state = {}
        if not isinstance(state, dict):
            raise ValueError(f"invalid retry history: {pdf.name}")
        state = dict(state)
        attempts = state.get("attempts", 0)
        try:
            first = datetime.fromisoformat(state["first_attempt_at"]) if state else now
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid retry history: {pdf.name}") from exc
        if type(attempts) is not int or attempts < 0 or first.tzinfo is None:
            raise ValueError(f"invalid retry history: {pdf.name}")
        age_days = (now - first).total_seconds() / 86400
        exhausted = (max_attempts is not None and attempts >= max_attempts) or (
            max_days is not None and age_days >= max_days
        )
        if existing and exhausted:
            report = dict(existing)
            summary["skipped"] += 1
        else:
            report, _code = cli.run(str(pdf), config, fmt_override=fmt_override,
                                    require_override=require_override)
            attempts += 1
            state.update(attempts=attempts, first_attempt_at=first.isoformat(),
                         last_attempt_at=now.isoformat(), last_status=report["status"])
            summary["processed"] += 1
        if report["status"] != "complete":
            if max_attempts is not None and attempts >= max_attempts:
                state["attention_reason"] = f"retry limit reached ({max_attempts} attempts)"
            elif max_days is not None and age_days >= max_days:
                state["attention_reason"] = f"retry age reached ({max_days} days)"
            if state.get("attention_reason"):
                report["status"] = "needs-attention"
        report["workflow"] = state
        write_sidecar(pdf, report)
        summary[report["status"]] += 1
        summary["documents"].append({
            "file": pdf.name,
            "status": report["status"],
            "mapped": report.get("mapped", False),
            "missing": report.get("missing_fields", []),
            "workflow": state,
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
            "workflow": report.get("workflow", {}),
        })
    counts: dict = {}
    for d in docs:
        counts[d["status"]] = counts.get(d["status"], 0) + 1
    return {"counts": counts, "documents": docs}


def _exit_code(counts: dict) -> int:
    if counts.get("failed"):
        return 1
    if counts.get("incomplete") or counts.get("unprocessed") or counts.get("needs-attention"):
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
    p_proc.add_argument("--retry-attention", action="store_true",
                        help="give needs-attention documents a fresh retry budget")

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

    try:
        config = cli.load_config(args.config)
        _retry_policy(config)
    except (OSError, ValueError) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    from .db import init_db, use_database

    database = args.db or config.get("db")
    if database:
        use_database(database)
    init_db()

    require = [s.strip() for s in args.require.split(",") if s.strip()] if args.require else None
    try:
        summary = process_folder(folder, config, fmt_override=args.fmt,
                                 require_override=require, only_pending=not args.all,
                                 retry_attention=args.retry_attention)
    except (OSError, ValueError) as exc:
        print(f"workflow error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(
        f"processed {summary['processed']}, skipped {summary['skipped']} — "
        f"{summary['complete']} complete, {summary['incomplete']} incomplete, "
        f"{summary['failed']} failed, {summary['needs-attention']} need attention",
        file=sys.stderr,
    )
    return _exit_code(summary)


if __name__ == "__main__":
    raise SystemExit(main())
