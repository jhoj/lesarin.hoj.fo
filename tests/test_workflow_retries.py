"""Retry budgets survive separate scheduler runs in result sidecars."""

from datetime import datetime, timedelta, timezone
import json

import pytest

from app import workflow


@pytest.fixture
def queue(tmp_path, monkeypatch):
    pdf = tmp_path / "invoice.pdf"
    pdf.write_bytes(b"stub PDF")
    calls = []

    def read(*args, **kwargs):
        calls.append(args)
        return {"status": "incomplete", "reason": "missing DueDate",
                "missing_fields": ["DueDate"], "fields": {"InvoiceNo": {"found": True}},
                "output": {"format": "json", "body": "{}"}}, 2

    monkeypatch.setattr(workflow.cli, "run", read)
    return pdf, calls


def test_attempt_limit_persists_and_stops_automatic_reads(queue):
    pdf, calls = queue
    config = {"retry": {"max_attempts": 2}}
    assert workflow.process_folder(pdf.parent, config)["incomplete"] == 1
    summary = workflow.process_folder(pdf.parent, config)
    assert summary["needs-attention"] == 1
    report = workflow.read_sidecar(pdf)
    assert report["workflow"]["attempts"] == 2
    assert report["workflow"]["last_status"] == "incomplete"
    assert report["missing_fields"] == ["DueDate"]
    assert report["reason"] == "missing DueDate"
    before = workflow.sidecar_for(pdf).read_bytes()

    assert workflow.process_folder(pdf.parent, config)["skipped"] == 1
    assert workflow.sidecar_for(pdf).read_bytes() == before
    assert len(calls) == 2
    status = workflow.queue_status(pdf.parent)
    assert status["counts"] == {"needs-attention": 1}
    assert workflow._exit_code(status["counts"]) == 2


def test_age_limit_stops_before_another_attempt(queue):
    pdf, calls = queue
    workflow.process_folder(pdf.parent, {})
    report = workflow.read_sidecar(pdf)
    report["workflow"]["first_attempt_at"] = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    workflow.write_sidecar(pdf, report)

    summary = workflow.process_folder(pdf.parent, {"retry": {"max_days": 7}})

    assert summary["needs-attention"] == summary["skipped"] == 1
    assert summary["processed"] == 0
    assert len(calls) == 1
    assert "7 days" in workflow.read_sidecar(pdf)["workflow"]["attention_reason"]


def test_reducing_budget_stops_before_another_attempt(queue):
    pdf, calls = queue
    workflow.process_folder(pdf.parent, {})
    summary = workflow.process_folder(pdf.parent, {"retry": {"max_attempts": 1}})
    assert summary["needs-attention"] == 1
    assert len(calls) == 1


@pytest.mark.parametrize("resume", [{"retry_attention": True}, {"only_pending": False}])
def test_explicit_retry_starts_new_budget(queue, monkeypatch, resume):
    pdf, calls = queue
    config = {"retry": {"max_attempts": 1}}
    workflow.process_folder(pdf.parent, config)
    monkeypatch.setattr(workflow.cli, "run", lambda *a, **kw: ({"status": "complete"}, 0))

    summary = workflow.process_folder(pdf.parent, config, **resume)

    assert summary["complete"] == 1
    assert workflow.read_sidecar(pdf)["workflow"]["attempts"] == 1
    assert "attention_reason" not in workflow.read_sidecar(pdf)["workflow"]


def test_retry_attention_does_not_reread_completed_documents(queue, monkeypatch):
    pdf, calls = queue
    workflow.write_sidecar(pdf, {"status": "complete"})
    assert workflow.process_folder(pdf.parent, {}, retry_attention=True)["skipped"] == 1
    assert calls == []


def test_failed_read_escalates_but_success_at_limit_does_not(queue, monkeypatch):
    pdf, _ = queue
    monkeypatch.setattr(workflow.cli, "run", lambda *a, **kw: ({"status": "failed", "reason": "bad PDF"}, 1))
    config = {"retry": {"max_attempts": 1}}
    assert workflow.process_folder(pdf.parent, config)["needs-attention"] == 1
    assert workflow.read_sidecar(pdf)["workflow"]["last_status"] == "failed"
    monkeypatch.setattr(workflow.cli, "run", lambda *a, **kw: ({"status": "complete"}, 0))
    assert workflow.process_folder(pdf.parent, config, retry_attention=True)["complete"] == 1


def test_existing_sidecar_without_history_starts_first_tracked_attempt(queue):
    pdf, calls = queue
    workflow.write_sidecar(pdf, {"status": "incomplete"})
    workflow.process_folder(pdf.parent, {"retry": {"max_attempts": 2}})
    assert workflow.read_sidecar(pdf)["workflow"]["attempts"] == 1


def test_no_policy_preserves_unbounded_retry_behavior(queue):
    pdf, calls = queue
    for _ in range(4):
        assert workflow.process_folder(pdf.parent, {})["incomplete"] == 1
    assert len(calls) == 4


@pytest.mark.parametrize("retry", [None, [], {"max_attempts": 0}, {"max_attempts": True},
    {"max_attempts": 1.5}, {"max_days": -1}, {"max_days": float("inf")},
    {"max_days": float("nan")}, {"max_days": "7"}, {"max_atempts": 3}])
def test_bad_policy_fails_before_processing(queue, retry):
    pdf, calls = queue
    with pytest.raises(ValueError, match="retry"):
        workflow.process_folder(pdf.parent, {"retry": retry})
    assert calls == []
    assert not workflow.sidecar_for(pdf).exists()


@pytest.mark.parametrize("contents", [b"null", b"[]", b"\xff", b"{unfinished"])
def test_unreadable_sidecar_can_be_reprocessed(queue, contents):
    pdf, calls = queue
    workflow.sidecar_for(pdf).write_bytes(contents)
    assert workflow.process_folder(pdf.parent, {})["incomplete"] == 1
    assert len(calls) == 1


def test_atomic_sidecar_failure_preserves_previous_result(queue, monkeypatch):
    pdf, _ = queue
    workflow.write_sidecar(pdf, {"status": "incomplete"})
    before = workflow.sidecar_for(pdf).read_bytes()

    def fail_replace(source, target):
        assert json.loads(source.read_bytes())["status"] == "complete"
        raise OSError("simulated failed replace")

    monkeypatch.setattr(workflow.os, "replace", fail_replace)
    with pytest.raises(OSError):
        workflow.write_sidecar(pdf, {"status": "complete"})
    assert workflow.sidecar_for(pdf).read_bytes() == before
    assert not list(pdf.parent.glob("*.tmp"))


def test_cli_rejects_invalid_retry_config_before_database_init(queue, tmp_path, capsys):
    pdf, calls = queue
    config = tmp_path / "job.json"
    config.write_text('{"retry": {"max_attempts": 0}}')
    assert workflow.main(["process", str(pdf.parent), "--config", str(config)]) == 1
    assert "retry.max_attempts" in capsys.readouterr().err
    assert calls == []


@pytest.mark.parametrize("state", [None, [], {"attempts": 2},
    {"attempts": 2, "first_attempt_at": None},
    {"attempts": 2, "first_attempt_at": "2026-01-01T00:00:00"},
    {"attempts": "2", "first_attempt_at": "2026-01-01T00:00:00+00:00"}])
def test_invalid_history_fails_without_silently_resetting_budget(queue, state):
    pdf, calls = queue
    workflow.write_sidecar(pdf, {"status": "incomplete", "workflow": state})
    before = workflow.sidecar_for(pdf).read_bytes()
    with pytest.raises(ValueError, match="invalid retry history"):
        workflow.process_folder(pdf.parent, {})
    assert workflow.sidecar_for(pdf).read_bytes() == before
    assert calls == []


def test_cli_honors_config_database_and_explicit_override(queue, tmp_path, monkeypatch):
    from app import db

    pdf, _ = queue
    selected = []
    monkeypatch.setattr(db, "use_database", selected.append)
    monkeypatch.setattr(db, "init_db", lambda: None)
    config = tmp_path / "job.json"
    config.write_text('{"db": "customer.db"}')
    assert workflow.main(["process", str(pdf.parent), "--config", str(config)]) == 2
    assert selected == ["customer.db"]
    workflow.main(["process", str(pdf.parent), "--config", str(config), "--db", "override.db"])
    assert selected[-1] == "override.db"
