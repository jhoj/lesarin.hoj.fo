"""Linux scheduler runner: locking, exit propagation, and an end-to-end batch."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "deploy" / "run-workflow.sh"
pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or not shutil.which("flock"), reason="Linux runner requires flock",
)


@pytest.fixture
def job(tmp_path):
    inbox = tmp_path / "inbox with spaces"
    inbox.mkdir()
    python = tmp_path / "stub python"
    python.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "print(json.dumps(sys.argv[1:]))\n"
        "name = 'PROCESS_EXIT' if sys.argv[2] == 'app.workflow' else 'OUTBOX_EXIT'\n"
        "raise SystemExit(int(os.environ.get(name, '0')))\n"
    )
    python.chmod(0o700)
    return {
        **os.environ,
        "LESARIN_INBOX": str(inbox),
        "LESARIN_JOB_CONFIG": str(tmp_path / "job config.yaml"),
        "LESARIN_PYTHON": str(python),
        "LESARIN_OUTBOX": "",
    }


def run(env, *args):
    return subprocess.run(["bash", str(RUNNER), *args], cwd=ROOT,
                          env=env, capture_output=True, text=True, check=False)


@pytest.mark.parametrize("process_code,outbox_code,expected", [
    (0, 0, 0), (2, 0, 2), (1, 0, 1), (0, 2, 2), (0, 1, 1),
    (2, 1, 1), (1, 2, 1), (1, 1, 1), (2, 2, 2), (0, 9, 9),
])
def test_outbox_runs_after_partial_batch_and_exit_codes_are_preserved(job, process_code, outbox_code, expected):
    job.update(PROCESS_EXIT=str(process_code), OUTBOX_EXIT=str(outbox_code), LESARIN_OUTBOX="outbox with spaces")
    result = run(job)
    assert result.returncode == expected, result.stderr
    process, export = [json.loads(line) for line in result.stdout.splitlines()]
    assert process == ["-m", "app.workflow", "process", job["LESARIN_INBOX"], "--config", job["LESARIN_JOB_CONFIG"]]
    assert export == ["-m", "app.outbox", job["LESARIN_INBOX"], "--outbox", job["LESARIN_OUTBOX"]]


def test_export_is_disabled_unless_explicitly_configured(job):
    result = run(job, "--retry-attention")
    assert result.returncode == 0
    calls = result.stdout.splitlines()
    assert len(calls) == 1
    assert json.loads(calls[0])[-1] == "--retry-attention"


def test_abnormal_process_exit_does_not_export(job):
    job.update(PROCESS_EXIT="137", LESARIN_OUTBOX="outbox")
    result = run(job)
    assert result.returncode == 137
    assert len(result.stdout.splitlines()) == 1


def test_existing_inbox_lock_skips_work_and_is_not_deleted(job):
    import fcntl

    lock = Path(job["LESARIN_INBOX"]) / ".lesarin-workflow.lock"
    with lock.open("w") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = run(job)
        assert result.returncode == 75
        assert result.stdout == ""
    assert lock.exists()
    assert run(job).returncode == 0


@pytest.mark.parametrize("name", ["LESARIN_INBOX", "LESARIN_JOB_CONFIG"])
def test_required_environment_is_checked_before_processing(job, name):
    job.pop(name)
    result = run(job)
    assert result.returncode != 0
    assert name in result.stderr
    assert result.stdout == ""


def test_real_batch_exports_completed_invoice_despite_failed_pdf(job, tmp_path, sample_invoice_pdf):
    inbox = Path(job["LESARIN_INBOX"])
    (inbox / "good.pdf").write_bytes(sample_invoice_pdf)
    (inbox / "bad.pdf").write_bytes(b"not a PDF")
    config = Path(job["LESARIN_JOB_CONFIG"])
    config.write_text(json.dumps({"db": str(tmp_path / "mapping.db"), "require": ["InvoiceNo"],
                                  "retry": {"max_attempts": 2}}))
    destination = tmp_path / "outbox"
    job.update(LESARIN_PYTHON=sys.executable, LESARIN_OUTBOX=str(destination))

    first = run(job)
    assert first.returncode == 1, first.stderr
    assert (destination / "good.json").is_file()
    assert not (destination / "bad.json").exists()
    before = (destination / "good.json").stat().st_mtime_ns

    second = run(job)
    assert second.returncode == 2, second.stderr
    bad = json.loads((inbox / "bad.lesarin.json").read_text())
    assert bad["status"] == "needs-attention"
    assert bad["workflow"]["attempts"] == 2
    assert (destination / "good.json").stat().st_mtime_ns == before
