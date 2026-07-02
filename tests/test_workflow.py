"""The review loop: process a folder → correct in the studio → reprocess.
Plus the sync bundle that carries the learned templates between sites."""

from __future__ import annotations

import json

import pytest

from app import repo, sync, workflow
from app.db import Base, SessionLocal, engine as db_engine, init_db


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(db_engine)
    Base.metadata.create_all(db_engine)
    init_db()
    yield
    Base.metadata.drop_all(db_engine)


@pytest.fixture()
def inbox(tmp_path, sample_invoice_pdf):
    folder = tmp_path / "inbox"
    folder.mkdir()
    (folder / "a.pdf").write_bytes(sample_invoice_pdf)
    (folder / "b.pdf").write_bytes(sample_invoice_pdf)
    return folder


def _teach_vendor():
    with SessionLocal() as session:
        repo.create_vendor(
            session, identifier="314188", name="Effo", match_keywords=["Føroya Handil"],
            mappings=[
                {"output": "InvoiceNo", "strategy": "label", "label": "Fakturanr"},
                {"output": "DueDate", "strategy": "label", "label": "Forfaldsdato", "value_type": "date"},
            ],
        )


def test_process_leaves_result_sidecars(inbox):
    summary = workflow.process_folder(inbox, config={})
    assert summary["processed"] == 2
    # No vendor taught yet → best-effort reads, marked incomplete for review.
    assert summary["incomplete"] == 2
    sidecar = json.loads((inbox / "a.lesarin.json").read_text())
    assert sidecar["status"] == "incomplete"
    assert sidecar["fields"]["InvoiceNo"]["value"] == "2026-0014"


def test_correct_then_reprocess_flips_to_complete(inbox):
    workflow.process_folder(inbox, config={})            # 1. auto-read
    _teach_vendor()                                      # 2. "correct" in the studio
    summary = workflow.process_folder(inbox, config={})  # 3. reprocess pending
    assert summary["processed"] == 2 and summary["complete"] == 2
    sidecar = json.loads((inbox / "a.lesarin.json").read_text())
    assert sidecar["status"] == "complete"
    assert sidecar["mapping"]["source"] == "template"


def test_reprocess_skips_already_complete(inbox):
    _teach_vendor()
    workflow.process_folder(inbox, config={})
    again = workflow.process_folder(inbox, config={})
    assert again["processed"] == 0 and again["skipped"] == 2
    forced = workflow.process_folder(inbox, config={}, only_pending=False)
    assert forced["processed"] == 2


def test_queue_status_reports_the_review_queue(inbox, sample_invoice_pdf):
    (inbox / "c.pdf").write_bytes(sample_invoice_pdf)
    workflow.process_folder(inbox, config={})
    (inbox / "c.lesarin.json").unlink()  # simulate a never-processed file
    status = workflow.queue_status(inbox)
    assert status["counts"] == {"incomplete": 2, "unprocessed": 1}
    assert workflow._exit_code(status["counts"]) == 2


# --- Sync bundle -------------------------------------------------------------

def test_bundle_round_trip():
    _teach_vendor()
    with SessionLocal() as session:
        repo.upsert_output_field(session, "PONumber", value_type="string",
                                 aliases=["PO no", "Innkeypsnr"])
        bundle = sync.export_bundle(session)

    assert any(v["identifier"] == "314188" for v in bundle["vendors"])

    # A "second site": wipe the store, import the bundle, knowledge is back.
    Base.metadata.drop_all(db_engine)
    Base.metadata.create_all(db_engine)
    with SessionLocal() as session:
        stats = sync.import_bundle(session, bundle)
        assert stats["vendors_added"] == 1
        vendor = repo.get_vendor_by_identifier(session, "314188")
        assert vendor is not None and len(vendor.mappings) == 2
        fields = {f.key: f for f in repo.list_output_fields(session)}
        assert "PO no" in fields["PONumber"].aliases


def test_import_merges_without_clobbering_local_teaching():
    _teach_vendor()
    with SessionLocal() as session:
        bundle = sync.export_bundle(session)
        # Local site refines its own DueDate mapping after the export.
        vendor = repo.get_vendor_by_identifier(session, "314188")
        repo.update_vendor(session, vendor.id, mappings=[
            {"output": "DueDate", "strategy": "label", "label": "Gjaldsdagur", "value_type": "date"},
        ])
        # Re-importing the older bundle must keep the local DueDate mapping and
        # only add the mapping the vendor now lacks (InvoiceNo).
        stats = sync.import_bundle(session, bundle)
        assert stats["mappings_added"] == 1
        vendor = repo.get_vendor_by_identifier(session, "314188")
        by_key = {m.output_key: m for m in vendor.mappings}
        assert by_key["DueDate"].source_label == "Gjaldsdagur"
        assert "InvoiceNo" in by_key


def test_import_fields_onboards_a_customer_list():
    spec = {"fields": [
        {"key": "PONumber", "value_type": "string", "aliases": ["PO no"]},
        {"key": "DeliveryDate", "value_type": "date"},
    ]}
    with SessionLocal() as session:
        stats = sync.import_fields(session, spec)
        assert stats == {"fields": 2}
        keys = {f.key for f in repo.list_output_fields(session)}
        assert {"PONumber", "DeliveryDate"} <= keys


def test_import_rejects_unknown_bundle_version():
    with SessionLocal() as session:
        with pytest.raises(ValueError):
            sync.import_bundle(session, {"bundle_version": 99})
