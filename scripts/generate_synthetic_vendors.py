"""M6 "invoice acquisition" — synthetic bootstrap.

Generalises the in-memory reportlab invoice generator used by the test suite
(``tests/conftest.py``) into a standalone generator of *many* plausible
vendor layouts, each rendered through the real extraction pipeline and saved
as a vendor template — so a fresh central instance (or a new site) starts
with a working set of templates instead of an empty store.

Fully synthetic: no real invoices or customer data involved, so it's free to
run repeatedly and free to share.

    python scripts/generate_synthetic_vendors.py --count 20 --db data/lesarin.db
    python -m site_agent.central_client push --central https://central.example
"""

from __future__ import annotations

import argparse
import io
import random
import sys
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

_VENDOR_NAMES = [
    "Føroya Handil", "Effo", "Framtak", "Sp/f Land og Sjógvur", "Tórshavn Trading",
    "Suðuroy Import", "Norðoyggja Handilsfelag", "Vestmanna Vørur", "Kósin P/F", "Bakkafrost Supply",
]

# A few plausible label variants per field, so synthetic layouts aren't identical.
_LABEL_VARIANTS = {
    "InvoiceNo": ["Fakturanr", "Faktura nr.", "Invoice No"],
    "InvoiceDate": ["Fakturadato", "Dato"],
    "DueDate": ["Forfaldsdato", "Gjaldfrist"],
    "VendorNo": ["Vtal", "V-tal"],
}


def _random_vtal(rng: random.Random) -> str:
    return str(rng.randint(100000, 999999))


def _build_invoice_pdf(rng: random.Random, vendor_name: str, vtal: str, labels: dict) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=20 * mm)
    styles = getSampleStyleSheet()
    story = [
        Paragraph(vendor_name, styles["Title"]),
        Paragraph(f"{labels['VendorNo']}: {vtal}", styles["Normal"]),
        Spacer(1, 6 * mm),
        Paragraph(f"{labels['InvoiceNo']}: {rng.randint(1000, 9999)}-{rng.randint(0, 99):02d}",
                   styles["Normal"]),
        Paragraph(f"{labels['InvoiceDate']}: {rng.randint(1, 28):02d}-{rng.randint(1, 12):02d}-2026",
                   styles["Normal"]),
        Paragraph(f"{labels['DueDate']}: {rng.randint(1, 28):02d}-{rng.randint(1, 12):02d}-2026",
                   styles["Normal"]),
    ]
    doc.build(story)
    return buf.getvalue()


def generate(count: int, seed: int = 0) -> List[dict]:
    """Return ``count`` synthetic (vendor_name, vtal, pdf_bytes) layouts."""
    rng = random.Random(seed)
    out = []
    for i in range(count):
        name = f"{rng.choice(_VENDOR_NAMES)} {i}"  # unique across a run
        vtal = _random_vtal(rng)
        labels = {k: rng.choice(v) for k, v in _LABEL_VARIANTS.items()}
        pdf = _build_invoice_pdf(rng, name, vtal, labels)
        out.append({"name": name, "vtal": vtal, "pdf": pdf})
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--db", help="SQLite mapping store (overrides $LESARIN_DB)")
    args = parser.parse_args(argv)

    from app.db import SessionLocal, init_db, use_database
    from app.extraction import loader
    from app import engine as extraction_engine
    from app.saas import build_canonical

    if args.db:
        use_database(args.db)
    init_db()

    created = 0
    with SessionLocal() as session:
        for layout in generate(args.count, args.seed):
            document = loader.load(layout["pdf"])
            build_canonical(session, document)  # auto-learns a vendor template
            created += 1

    print(f"generated {created} synthetic vendor template(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
