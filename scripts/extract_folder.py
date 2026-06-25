"""Batch-test the running Lesarin service against a folder of PDFs.

Starts nothing itself — run the service first (`uvicorn app.main:app`), then:

    python scripts/extract_folder.py C:\\path\\to\\invoices

For each *.pdf it POSTs to /extract and writes <name>.lesarin.json next to it.
A summary line per file shows which target fields were located.

Options:
    --url   service base URL (default http://127.0.0.1:8000)
    --out   write JSON files to this directory instead of alongside the PDFs
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib import error, request

TARGET_FIELDS = ["invoiceno", "sentdate", "paydate"]


def _located(result: dict, key: str) -> bool:
    node = result.get(key) or {}
    return bool(node.get("value"))


def _vendor_located(result: dict) -> bool:
    vendor = result.get("vendor") or {}
    name = vendor.get("name") or {}
    return bool(name.get("value"))


def post_pdf(url: str, pdf: Path) -> dict:
    """POST a single PDF as multipart/form-data using only the stdlib."""
    boundary = "----lesarinbatch"
    body = bytearray()
    body += f"--{boundary}\r\n".encode()
    body += (
        f'Content-Disposition: form-data; name="file"; filename="{pdf.name}"\r\n'
    ).encode()
    body += b"Content-Type: application/pdf\r\n\r\n"
    body += pdf.read_bytes()
    body += f"\r\n--{boundary}--\r\n".encode()

    req = request.Request(
        f"{url.rstrip('/')}/extract",
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description="Batch-extract a folder of invoice PDFs.")
    ap.add_argument("folder", help="Folder containing .pdf files")
    ap.add_argument("--url", default="http://127.0.0.1:8000", help="Service base URL")
    ap.add_argument("--out", default=None, help="Output dir (default: alongside each PDF)")
    args = ap.parse_args()

    folder = Path(args.folder)
    if not folder.is_dir():
        print(f"Not a folder: {folder}", file=sys.stderr)
        return 2

    out_dir = Path(args.out) if args.out else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    pdfs = sorted(folder.glob("*.pdf"))
    if not pdfs:
        print(f"No .pdf files in {folder}", file=sys.stderr)
        return 1

    ok = 0
    for pdf in pdfs:
        try:
            result = post_pdf(args.url, pdf)
        except error.URLError as exc:
            print(f"  ! {pdf.name}: request failed ({exc.reason}) — is the service running?")
            continue
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {pdf.name}: {exc}")
            continue

        dest = (out_dir / pdf.name if out_dir else pdf).with_suffix(".lesarin.json")
        dest.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

        found = [f for f in TARGET_FIELDS if _located(result, f)]
        if _vendor_located(result):
            found.append("vendor.name")
        n_lines = len(result.get("lines") or [])
        ocr = " [OCR]" if (result.get("meta") or {}).get("ocr_used") else ""
        print(f"  ok {pdf.name}: {', '.join(found) or 'nothing located'}; {n_lines} line(s){ocr}")
        ok += 1

    print(f"\nDone: {ok}/{len(pdfs)} processed. JSON written next to each PDF (or --out).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
