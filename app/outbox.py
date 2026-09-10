"""Materialize completed workflow sidecars without re-reading invoices.

    python -m app.outbox INBOX/ --outbox OUTBOX/

This deliberately has no database or extraction dependencies. Existing outputs
are never overwritten; consumers should watch only *.json and *.xml files.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile


def materialize_outbox(folder: Path, outbox: Path) -> dict:
    """Export complete sidecars, skipping identical files and reporting conflicts.

    Re-running is idempotent while output files remain in place. Removing an
    output makes it eligible for export again; this is not a delivery ledger.
    """
    if not folder.is_dir():
        raise ValueError(f"not a folder: {folder}")
    if folder.resolve() == outbox.resolve():
        raise ValueError("outbox must be separate from the sidecar folder")
    outbox.mkdir(parents=True, exist_ok=True)
    summary = {"exported": 0, "skipped": 0, "pending": 0, "failed": 0, "documents": []}
    suffix = ".lesarin.json"
    extensions = {"json": ".json", "xml": ".xml", "ubl": ".xml", "oioubl": ".xml"}

    for sidecar in sorted(folder.glob(f"*{suffix}")):
        if not sidecar.is_file():
            continue
        entry = {"file": sidecar.name}
        temporary = None
        try:
            report = json.loads(sidecar.read_text(encoding="utf-8"))
            if not isinstance(report, dict):
                raise ValueError("sidecar must contain a result object")
            status = report.get("status")
            if status in ("incomplete", "failed", "needs-attention"):
                entry.update(status="pending", source_status=status)
            elif status != "complete":
                raise ValueError(f"unknown result status: {status!r}")
            else:
                output = report.get("output")
                if not isinstance(output, dict):
                    raise ValueError("complete result has no output object")
                fmt = output.get("format")
                if not isinstance(fmt, str) or fmt not in extensions:
                    raise ValueError(f"unsupported output format: {fmt!r}")
                body = output.get("body")
                if not isinstance(body, str) or not body.strip():
                    raise ValueError("complete result has no non-empty output body")
                name = sidecar.name[:-len(suffix)]
                if not name:
                    raise ValueError("sidecar has no document name")
                # Never use the report's input path or invoice values as a filename.
                target = outbox / (name + extensions[fmt])
                entry["output"] = target.name
                payload = body.encode("utf-8")
                with tempfile.NamedTemporaryFile(dir=outbox, prefix=".lesarin-",
                                                 suffix=".tmp", delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    # Publish a fully written file atomically, without replacing
                    # anything another run (or an operator) has already created.
                    os.link(temporary, target)
                    entry["status"] = "exported"
                except FileExistsError:
                    if target.is_symlink() or not target.is_file() or target.read_bytes() != payload:
                        raise ValueError(f"output already exists with different content or type: {target.name}")
                    entry["status"] = "skipped"
        except (OSError, ValueError) as exc:
            entry.update(status="failed", error=str(exc))
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        summary[entry["status"]] += 1
        summary["documents"].append(entry)

    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.outbox",
        description="Write importable files from completed workflow result sidecars.",
    )
    parser.add_argument("folder", type=Path, help="folder containing .lesarin.json sidecars")
    parser.add_argument("--outbox", type=Path, required=True, help="separate destination folder")
    args = parser.parse_args(argv)
    try:
        summary = materialize_outbox(args.folder, args.outbox)
    except (OSError, ValueError) as exc:
        print(f"outbox error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(
        f"exported {summary['exported']}, skipped {summary['skipped']}, "
        f"pending {summary['pending']}, failed {summary['failed']}",
        file=sys.stderr,
    )
    return 1 if summary["failed"] else 2 if summary["pending"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
