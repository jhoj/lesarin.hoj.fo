"""Escalations notify the responsible user without repeating successful mail."""

import pytest

from app import mailer, workflow


@pytest.fixture
def queue(tmp_path, monkeypatch):
    for name in ("a", "b"):
        (tmp_path / f"{name}.pdf").write_bytes(b"stub PDF")
    reads, messages = [], []

    def read(*args, **kwargs):
        reads.append(args)
        return {"status": "incomplete", "missing_fields": ["DueDate"],
                "fields": {"InvoiceNo": {"found": True, "value": "PRIVATE-INVOICE-VALUE"}}}, 2

    def send(*args):
        messages.append(args)
        return True

    monkeypatch.setattr(workflow.cli, "run", read)
    monkeypatch.setattr(mailer, "send", send)
    monkeypatch.setenv("LESARIN_BASE_URL", "https://reader.example.test/")
    return tmp_path, reads, messages


CONFIG = {"retry": {"max_attempts": 1}, "responsible_email": "accounts@example.test"}


def test_one_digest_covers_new_escalations_and_records_success(queue):
    folder, reads, messages = queue
    summary = workflow.process_folder(folder, CONFIG)

    assert summary["notifications"] == {"sent": 2, "pending": 0, "failed": 0}
    assert workflow._exit_code(summary) == 2
    assert len(messages) == 1
    to, subject, body = messages[0]
    assert to == CONFIG["responsible_email"]
    assert "a.pdf" in body and "b.pdf" in body
    assert "Located fields: InvoiceNo" in body
    assert "Missing fields: DueDate" in body
    assert "https://reader.example.test/studio" in body
    assert "PRIVATE-INVOICE-VALUE" not in body
    for name in ("a", "b"):
        receipt = workflow.read_sidecar(folder / f"{name}.pdf")["workflow"]["notification"]
        assert receipt["to"] == to
        assert receipt["sent_at"]

    again = workflow.process_folder(folder, CONFIG)
    assert again["notifications"] == {"sent": 0, "pending": 0, "failed": 0}
    assert len(messages) == 1
    assert len(reads) == 2


def test_failed_smtp_is_retried_without_repeating_extraction(queue, monkeypatch):
    folder, reads, messages = queue
    send = mailer.send
    monkeypatch.setattr(mailer, "send", lambda *a: False)
    failed = workflow.process_folder(folder, CONFIG)
    assert failed["notifications"] == {"sent": 0, "pending": 2, "failed": 2}
    assert workflow._exit_code(failed) == 1
    assert "notification" not in workflow.read_sidecar(folder / "a.pdf")["workflow"]

    monkeypatch.setattr(mailer, "send", send)
    assert workflow.process_folder(folder, CONFIG)["notifications"]["sent"] == 2
    assert len(reads) == 2


def test_bad_smtp_settings_do_not_lose_pending_notifications(queue, monkeypatch):
    folder, _, _ = queue

    def fail(*args):
        raise ValueError("invalid SMTP port")

    monkeypatch.setattr(mailer, "send", fail)
    assert workflow.process_folder(folder, CONFIG)["notifications"]["failed"] == 2
    assert "notification" not in workflow.read_sidecar(folder / "a.pdf")["workflow"]


def test_no_responsible_user_remains_opt_in_and_can_be_configured_later(queue):
    folder, reads, messages = queue
    summary = workflow.process_folder(folder, {"retry": {"max_attempts": 1}})
    assert summary["notifications"] == {"sent": 0, "pending": 2, "failed": 0}
    assert messages == []
    assert workflow.process_folder(folder, CONFIG)["notifications"]["sent"] == 2
    assert len(reads) == 2


def test_change_of_responsible_user_notifies_new_recipient(queue):
    folder, _, messages = queue
    workflow.process_folder(folder, CONFIG)
    summary = workflow.process_folder(folder, {**CONFIG, "responsible_email": "replacement@example.test"})
    assert summary["notifications"]["sent"] == 2
    assert messages[-1][0] == "replacement@example.test"


def test_explicit_retry_allows_notification_for_new_escalation(queue):
    folder, reads, messages = queue
    workflow.process_folder(folder, CONFIG)
    summary = workflow.process_folder(folder, CONFIG, retry_attention=True)
    assert summary["notifications"]["sent"] == 2
    assert len(reads) == 4 and len(messages) == 2


def test_successful_retry_needs_no_further_notification(queue, monkeypatch):
    folder, _, messages = queue
    workflow.process_folder(folder, CONFIG)
    monkeypatch.setattr(workflow.cli, "run", lambda *a, **kw: ({"status": "complete"}, 0))
    summary = workflow.process_folder(folder, CONFIG, retry_attention=True)
    assert summary["complete"] == 2
    assert summary["notifications"]["sent"] == 0
    assert len(messages) == 1


@pytest.mark.parametrize("recipient", ["", "not-email", [], "a@b\nBcc: c@d", "a@b,c@d", "User <a@b>"])
def test_invalid_recipient_is_rejected_before_reading(queue, recipient):
    folder, reads, messages = queue
    with pytest.raises(ValueError, match="responsible_email"):
        workflow.process_folder(folder, {**CONFIG, "responsible_email": recipient})
    assert reads == messages == []
