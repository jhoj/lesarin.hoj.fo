# Importable Outbox Files

`app.outbox` implements the outbox-file portion of
[brain-sync Stage A](brain-sync.md#recommended-order-of-implementation). It reads
the result sidecars already written by `app.workflow`, without accessing the
database, changing templates, re-reading PDFs, or contacting the central service.

## Usage

First run the existing workflow, then materialize its completed results:

```bash
python -m app.workflow process INBOX/ --config job.yaml
python -m app.outbox INBOX/ --outbox OUTBOX/
```

Run these as separate steps even when processing reports incomplete documents
(exit code 2): the completed documents in that batch can still be exported. A
failed sidecar also does not prevent other completed sidecars from exporting.

An `invoice.lesarin.json` with `status: complete` produces `invoice.json` for
JSON, or `invoice.xml` for XML, UBL, and OIOUBL. The file contains the exact UTF-8
`output.body`, not the result envelope. The recorded output format determines
the extension. Filenames come only from sidecar names, never invoice values or
the `input` path inside the report.

Only the immediate folder is scanned, not subdirectories. Use a separate outbox
for each inbox/customer to avoid filename collisions and mixing customer data.
The destination is created if necessary and must not be the sidecar directory.

## Publication Safety

- Only `complete` results are exported. `incomplete`, `failed`, and
  `needs-attention` results remain pending for the existing review loop.
- Completion follows the existing reader's policy. Configure `validate: strict`
  in `job.yaml` before processing if failed invoice checks should block export.
  This command does not revalidate old sidecars or certify UBL/OIOUBL compliance.
- Each payload is written and flushed to a hidden `.tmp` file before being
  atomically published with a hard link. Consumers must watch only `*.json` and
  `*.xml`, not temporary files. The destination filesystem must support hard links.
- An existing byte-identical file is skipped without changing its modification
  time. Different content, directories, and symlinks are reported as conflicts;
  existing outputs are never overwritten. After a mapping correction, inspect
  and archive the previous export explicitly before publishing a replacement.
- Malformed sidecars and filesystem errors are reported per document, while
  other documents continue. Source sidecars are never changed.

## Delivery Semantics

This is **file materialization, not exactly-once delivery**. Repeated runs are
idempotent while the exported files remain in place. If a consumer moves or
deletes an output but leaves its source sidecar in the inbox, a later run will
create that output again. Consumers must deduplicate imports, or the operator
must archive the source PDF and sidecar after confirmed import. Do not schedule
an unattended consuming loop without handling this acknowledgement step.

A PDF with no sidecar is not counted by this command. Use
`python -m app.workflow status INBOX/` for the full processing queue, including
unprocessed PDFs.

The command prints a JSON summary to stdout and a short count to stderr:

| Exit Code | Meaning |
| --- | --- |
| `0` | Every discovered sidecar was exported or already present, or no sidecars exist |
| `2` | Some sidecars are pending, with no export errors |
| `1` | A malformed sidecar, output conflict, or I/O error occurred |

## Implementation Handoff

This continuation is intentionally limited to three new files: `app/outbox.py`,
`tests/test_outbox.py`, and this guide. It does not modify the in-progress central,
site-agent, validation, database, CLI, or frontend implementations.

The [workflow automation guide](workflow-automation.md) describes the implemented
retry/time budget and explicit retry after human review. Stage A still needs
durable delivery acknowledgements for consuming integrations, scheduled processing,
responsible-user configuration, and notification. This command can be called
after processing as a separate step.

Verification:

```bash
pytest -q tests/test_outbox.py tests/test_workflow.py
```
