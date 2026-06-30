"""The command-line tool: input + config -> rendered output + status result."""

from __future__ import annotations

import json

import pytest

from app import cli, repo
from app.db import Base, SessionLocal, engine as db_engine, init_db


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(db_engine)
    Base.metadata.create_all(db_engine)
    init_db()
    yield
    Base.metadata.drop_all(db_engine)


@pytest.fixture()
def invoice_path(tmp_path, sample_invoice_pdf):
    p = tmp_path / "inv.pdf"
    p.write_bytes(sample_invoice_pdf)
    return str(p)


def _seed_vendor():
    with SessionLocal() as session:
        repo.create_vendor(
            session, identifier="314188", name="Effo", match_keywords=["Føroya Handil"],
            mappings=[
                {"output": "InvoiceNo", "strategy": "label", "label": "Fakturanr"},
                {"output": "DueDate", "strategy": "label", "label": "Forfaldsdato", "value_type": "date"},
            ],
        )


def test_complete_when_mapping_satisfied(invoice_path):
    _seed_vendor()
    report, code = cli.run(invoice_path, config={})
    assert report["status"] == "complete"
    assert code == 0
    assert report["mapped"] is True
    assert report["mapping"]["source"] == "template"
    assert report["vendor"]["identifier"] == "314188"
    assert report["missing_fields"] == []
    assert json.loads(report["output"]["body"])["InvoiceNo"] == "2026-0014"


def test_incomplete_when_unmapped(invoice_path):
    # No vendor seeded → no template matched → best-effort heuristic read.
    report, code = cli.run(invoice_path, config={})
    assert report["status"] == "incomplete"
    assert code == 2
    assert report["mapped"] is False
    assert "no vendor mapping" in report["reason"]
    # ...but it still produced output.
    assert json.loads(report["output"]["body"])["InvoiceNo"] == "2026-0014"


def test_incomplete_when_required_field_missing(invoice_path):
    _seed_vendor()
    report, code = cli.run(invoice_path, config={"require": ["InvoiceNo", "Vat"]})
    assert report["status"] == "incomplete"
    assert code == 2
    assert report["missing_fields"] == ["Vat"]


def test_failed_on_missing_file(tmp_path):
    report, code = cli.run(str(tmp_path / "nope.pdf"), config={})
    assert report["status"] == "failed"
    assert code == 1


def test_config_renames_fields_and_picks_format(invoice_path):
    _seed_vendor()
    config = {
        "format": "json",
        "fields": [{"canonical": "InvoiceNo", "output_name": "invoice_id"}],
    }
    report, code = cli.run(invoice_path, config=config)
    body = json.loads(report["output"]["body"])
    assert body["invoice_id"] == "2026-0014"
    assert "InvoiceNo" not in body


def test_format_override_to_ubl(invoice_path):
    _seed_vendor()
    report, _ = cli.run(invoice_path, config={"format": "json"}, fmt_override="ubl")
    assert report["output"]["format"] == "ubl"
    assert "<cbc:ID>2026-0014</cbc:ID>" in report["output"]["body"]


def test_main_writes_output_file_and_returns_exit_code(invoice_path, tmp_path, capsys):
    _seed_vendor()
    out = tmp_path / "out.json"
    code = cli.main([invoice_path, "--output", str(out), "--bare"])
    assert code == 0
    assert json.loads(out.read_text())["InvoiceNo"] == "2026-0014"
    # --bare prints the rendered document (not the envelope) to stdout.
    captured = capsys.readouterr()
    assert "InvoiceNo" in captured.out
    assert "complete:" in captured.err
