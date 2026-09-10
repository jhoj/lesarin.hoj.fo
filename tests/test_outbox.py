"""Outbox publication uses existing sidecars, without loading the mapping store."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

from app import outbox


@pytest.fixture
def folders(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    return inbox, tmp_path / "outbox"


def write_result(inbox, name="invoice", status="complete", fmt="json", body='{"id": "123"}'):
    path = inbox / f"{name}.lesarin.json"
    path.write_text(json.dumps({
        "status": status,
        "input": "../../must-not-be-used.pdf",
        "output": {"format": fmt, "body": body},
    }), encoding="utf-8")
    return path


@pytest.mark.parametrize("fmt,extension", [
    ("json", ".json"), ("xml", ".xml"), ("ubl", ".xml"), ("oioubl", ".xml"),
])
def test_exports_exact_body_with_safe_filename(folders, fmt, extension):
    inbox, destination = folders
    body = '{"vendor": "F\u00f8roya Handil"}\n' if fmt == "json" else '<Invoice>F\u00f8roya</Invoice>\n'
    source = write_result(inbox, name="invoice.v2", fmt=fmt, body=body)
    before = source.read_bytes()

    summary = outbox.materialize_outbox(inbox, destination)

    assert summary["exported"] == 1
    assert summary["failed"] == 0
    assert (destination / f"invoice.v2{extension}").read_bytes() == body.encode("utf-8")
    assert source.read_bytes() == before
    assert len(list(destination.iterdir())) == 1


def test_only_complete_results_are_exported(folders):
    inbox, destination = folders
    for status in ("complete", "incomplete", "failed", "needs-attention"):
        write_result(inbox, name=status, status=status)
    (inbox / "unprocessed.pdf").touch()

    summary = outbox.materialize_outbox(inbox, destination)

    assert summary["exported"] == 1
    assert summary["pending"] == 3
    assert summary["failed"] == 0
    assert [p.name for p in destination.iterdir()] == ["complete.json"]


def test_rerun_preserves_existing_output(folders):
    inbox, destination = folders
    write_result(inbox)
    outbox.materialize_outbox(inbox, destination)
    target = destination / "invoice.json"
    before = target.stat()

    summary = outbox.materialize_outbox(inbox, destination)

    assert summary["skipped"] == 1
    assert summary["exported"] == summary["failed"] == 0
    assert target.stat().st_ino == before.st_ino
    assert target.stat().st_mtime_ns == before.st_mtime_ns


def test_conflicting_output_is_not_overwritten_and_other_exports_continue(folders):
    inbox, destination = folders
    write_result(inbox, name="a")
    write_result(inbox, name="b")
    destination.mkdir()
    (destination / "a.json").write_text("keep this", encoding="utf-8")

    summary = outbox.materialize_outbox(inbox, destination)

    assert summary["failed"] == summary["exported"] == 1
    assert "already exists" in summary["documents"][0]["error"]
    assert (destination / "a.json").read_text() == "keep this"
    assert sorted(p.name for p in destination.iterdir()) == ["a.json", "b.json"]


@pytest.mark.parametrize("contents", [
    b"not json", b"\xff", b"[]", b"null", b'{}',
    b'{"status":"complete"}',
    b'{"status":"complete","output":{"format":"csv","body":"a,b"}}',
    b'{"status":"complete","output":{"format":[],"body":"test"}}',
    b'{"status":"complete","output":{"format":"json","body":null}}',
    b'{"status":"complete","output":{"format":"json","body":" "}}',
])
def test_bad_sidecar_does_not_stop_batch(folders, contents):
    inbox, destination = folders
    (inbox / "a.lesarin.json").write_bytes(contents)
    write_result(inbox, name="b")

    summary = outbox.materialize_outbox(inbox, destination)

    assert summary["failed"] == summary["exported"] == 1
    assert [p.name for p in destination.iterdir()] == ["b.json"]


def test_atomic_publication_and_failed_publish_cleanup(folders, monkeypatch):
    inbox, destination = folders
    write_result(inbox)

    def fail_link(source, target):
        assert Path(source).read_bytes() == b'{"id": "123"}'
        assert Path(source).suffix == ".tmp"
        assert not Path(target).exists()
        raise OSError("simulated publication failure")

    monkeypatch.setattr(outbox.os, "link", fail_link)
    summary = outbox.materialize_outbox(inbox, destination)

    assert summary["failed"] == 1
    assert list(destination.iterdir()) == []


def test_concurrent_publisher_cannot_be_overwritten(folders, monkeypatch):
    inbox, destination = folders
    write_result(inbox)
    link = outbox.os.link

    def competing_link(source, target):
        Path(target).write_bytes(b"another publisher")
        link(source, target)

    monkeypatch.setattr(outbox.os, "link", competing_link)
    summary = outbox.materialize_outbox(inbox, destination)

    assert summary["failed"] == 1
    assert (destination / "invoice.json").read_bytes() == b"another publisher"


def test_symlink_output_is_rejected_without_touching_target(folders, tmp_path):
    inbox, destination = folders
    write_result(inbox)
    destination.mkdir()
    other = tmp_path / "other.json"
    other.write_bytes(b'{"id": "123"}')
    (destination / "invoice.json").symlink_to(other)

    summary = outbox.materialize_outbox(inbox, destination)

    assert summary["failed"] == 1
    assert other.read_bytes() == b'{"id": "123"}'
    assert (destination / "invoice.json").is_symlink()


@pytest.mark.parametrize("status,expected", [("complete", 0), ("incomplete", 2), ("unknown", 1)])
def test_cli_summary_and_exit_code(folders, capsys, status, expected):
    inbox, destination = folders
    write_result(inbox, status=status)

    assert outbox.main([str(inbox), "--outbox", str(destination)]) == expected
    captured = capsys.readouterr()
    assert len(json.loads(captured.out)["documents"]) == 1
    assert "exported" in captured.err


def test_empty_folder_is_successful(folders):
    inbox, destination = folders
    assert outbox.main([str(inbox), "--outbox", str(destination)]) == 0
    assert list(destination.iterdir()) == []


def test_missing_source_does_not_create_destination(folders, capsys):
    inbox, destination = folders
    assert outbox.main([str(inbox / "missing"), "--outbox", str(destination)]) == 1
    assert "not a folder" in capsys.readouterr().err
    assert not destination.exists()


def test_same_source_and_destination_are_rejected(folders):
    inbox, _ = folders
    with pytest.raises(ValueError, match="separate"):
        outbox.materialize_outbox(inbox, inbox)


def test_missing_output_can_be_explicitly_materialized_again(folders):
    inbox, destination = folders
    write_result(inbox)
    outbox.materialize_outbox(inbox, destination)
    (destination / "invoice.json").unlink()
    assert outbox.materialize_outbox(inbox, destination)["exported"] == 1


def test_real_workflow_result_exports_without_reprocessing(folders, tmp_path, sample_invoice_pdf):
    inbox, destination = folders
    (inbox / "invoice.pdf").write_bytes(sample_invoice_pdf)
    processed = subprocess.run([
        sys.executable, "-m", "app.workflow", "process", str(inbox),
        "--db", str(tmp_path / "mapping.db"), "--require", "InvoiceNo", "--format", "ubl",
    ], capture_output=True, text=True, check=False)
    assert processed.returncode == 0, processed.stderr
    source = inbox / "invoice.lesarin.json"
    before = source.read_bytes()
    report = json.loads(before)

    exported = subprocess.run([
        sys.executable, "-m", "app.outbox", str(inbox), "--outbox", str(destination),
    ], capture_output=True, text=True, check=False)

    assert exported.returncode == 0, exported.stderr
    assert json.loads(exported.stdout)["exported"] == 1
    assert (destination / "invoice.xml").read_bytes() == report["output"]["body"].encode("utf-8")
    assert source.read_bytes() == before
