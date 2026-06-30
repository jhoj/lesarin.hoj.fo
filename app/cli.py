"""Lesarin command-line tool: invoice in, structured data out.

A single, dependency-light execution — *input, config, output* — meant to slot
into an automation pipeline:

    python -m app.cli INVOICE.pdf --config job.yaml --output out.json

It detects the vendor, and **if a saved mapping exists it uses that** to gather
the values; otherwise it falls back to layout heuristics so you still get a
best-effort read. The result carries a **status** so a pipeline can branch:

    complete    a mapping (or your required fields) was satisfied — trust it
    incomplete  output produced, but some expected fields are missing, or no
                vendor mapping matched at all (best-effort heuristic read)
    failed      the document could not be read at all

Exit codes mirror the status (complete=0, incomplete=2, failed=1), and the full
*Result* — status, the mapping that was applied, located fields, what's missing,
and the rendered output — is emitted as JSON on stdout (use ``--bare`` to print
only the rendered document instead).

Config (JSON or YAML, all keys optional):

    db: data/lesarin.db        # mapping store to read (else $LESARIN_DB / default)
    format: json               # json | xml | ubl | oioubl
    fields:                    # optional: select + rename canonical fields
      - canonical: InvoiceNo
        output_name: invoice_id
    require: [InvoiceNo, DueDate]   # fields that must be present for 'complete'
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional, Tuple

import yaml

# Status values and their process exit codes.
STATUS_EXIT = {"complete": 0, "failed": 1, "incomplete": 2}
_VALID_FORMATS = {"json", "xml", "ubl", "oioubl"}


def load_config(path: Optional[str]) -> dict:
    if not path:
        return {}
    text = Path(path).read_text(encoding="utf-8")
    # YAML is a superset of JSON, so one loader handles both .yaml and .json.
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ValueError("Config must be a mapping (object) at the top level.")
    return data


def _expected_fields(config: dict, extraction) -> List[str]:
    """Which fields decide completeness: an explicit ``require`` list wins;
    otherwise the keys the matched vendor template declares; otherwise none
    (an unmapped document can't be judged 'complete')."""
    require = config.get("require")
    if require:
        return list(require)
    if extraction.matched:
        return [m["output"] for m in extraction.applied_template]
    return []


def _render(extraction, config: dict, fmt: str) -> str:
    from .exporters import CanonicalInvoice, render

    profile_fields = None
    if config.get("fields"):
        from . import canonical

        profile_fields = [
            {"canonical": f["canonical"], "output_name": f.get("output_name", f["canonical"])}
            for f in config["fields"]
            if canonical.is_canonical(f["canonical"])
        ]
    invoice = CanonicalInvoice(values=extraction.values(), lines=extraction.lines)
    return render(invoice, fmt, profile_fields).body


def run(input_path: str, config: dict, fmt_override: Optional[str] = None,
        require_override: Optional[List[str]] = None) -> Tuple[dict, int]:
    """Read one invoice and return (result_envelope, exit_code).

    Pure of any I/O beyond reading the input file, so it's easy to test.
    """
    from . import engine
    from .db import SessionLocal
    from .extraction import loader

    fmt = (fmt_override or config.get("format") or "json").lower()
    if fmt not in _VALID_FORMATS:
        return ({"status": "failed", "reason": f"unknown format {fmt!r}",
                 "input": input_path}, STATUS_EXIT["failed"])

    if require_override is not None:
        config = {**config, "require": require_override}

    # Read + parse the document (the one place that can hard-fail).
    try:
        data = Path(input_path).read_bytes()
        document = loader.load(data)
    except FileNotFoundError:
        return ({"status": "failed", "reason": f"no such file: {input_path}",
                 "input": input_path}, STATUS_EXIT["failed"])
    except Exception as exc:  # noqa: BLE001 — any parse failure is a 'failed' read
        return ({"status": "failed", "reason": f"could not read PDF: {exc}",
                 "input": input_path}, STATUS_EXIT["failed"])

    with SessionLocal() as session:
        extraction = engine.extract(session, document)

    expected = _expected_fields(config, extraction)
    missing = [k for k in expected if not extraction.fields.get(k, None) or not extraction.fields[k].found]

    if not expected:
        status = "incomplete"
        reason = "no vendor mapping matched; produced a best-effort heuristic read"
    elif missing:
        status = "incomplete"
        reason = "missing expected fields: " + ", ".join(missing)
    else:
        status = "complete"
        reason = "all expected fields located"

    body = _render(extraction, config, fmt)
    vendor = extraction.vendor
    report = {
        "status": status,
        "reason": reason,
        "input": input_path,
        "mapped": extraction.matched,
        "vendor": (
            {"identifier": vendor.identifier, "name": vendor.name, "matched": extraction.matched}
            if vendor is not None else None
        ),
        "mapping": {"source": extraction.source, "fields": extraction.applied_template},
        "fields": {
            k: {"value": r.value, "found": r.found, "source": r.source,
                "confidence": round(r.confidence, 3)}
            for k, r in extraction.fields.items()
        },
        "expected": expected,
        "missing_fields": missing,
        "lines": [ln.as_dict() for ln in extraction.lines],
        "output": {"format": fmt, "body": body},
    }
    return report, STATUS_EXIT[status]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="Read an invoice PDF using saved vendor mappings and emit structured data.",
    )
    parser.add_argument("input", help="path to the invoice PDF")
    parser.add_argument("--config", help="path to a JSON/YAML job config")
    parser.add_argument("--db", help="SQLite mapping store to read (overrides $LESARIN_DB)")
    parser.add_argument("--format", dest="fmt", choices=sorted(_VALID_FORMATS),
                        help="output format (overrides the config)")
    parser.add_argument("--require", help="comma-separated fields required for 'complete'")
    parser.add_argument("--output", help="write the rendered document to this file")
    parser.add_argument("--bare", action="store_true",
                        help="print only the rendered document to stdout (no result envelope)")
    args = parser.parse_args(argv)

    # Point at the requested store and make sure its schema/vocabulary exist.
    from .db import init_db, use_database

    if args.db:
        use_database(args.db)
    init_db()

    try:
        config = load_config(args.config)
    except (OSError, ValueError) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return STATUS_EXIT["failed"]

    require = [s.strip() for s in args.require.split(",") if s.strip()] if args.require else None
    report, code = run(args.input, config, fmt_override=args.fmt, require_override=require)

    if args.output and report["status"] != "failed":
        Path(args.output).write_text(report["output"]["body"], encoding="utf-8")

    if args.bare and report["status"] != "failed":
        print(report["output"]["body"])
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))

    # A concise status line on stderr, so stdout stays a clean machine payload.
    print(f"{report['status']}: {report['reason']}", file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
