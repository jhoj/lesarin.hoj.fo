# Workflow Automation

## Retry Budget

Set either or both limits in the existing job configuration:

```yaml
format: json
validate: strict
retry:
  max_attempts: 3
  max_days: 7
```

```bash
python -m app.workflow process INBOX/ --config job.yaml
python -m app.workflow status INBOX/
python -m app.outbox INBOX/ --outbox OUTBOX/
```

The first limit reached stops automatic retries. A successful read on the last
allowed attempt is still `complete`. Otherwise the result becomes
`needs-attention`; an elapsed day budget is checked before another read, so an
old document does not need one last expensive OCR pass just to be escalated.
Age is measured in UTC from the first tracked attempt, not PDF modification time.

Each sidecar stores `workflow.attempts`, `first_attempt_at`, `last_attempt_at`,
and `last_status`. Escalation adds `attention_reason` while preserving the
reader's original reason, located fields, missing fields, and rendered output.
Sidecars are published atomically. Keep them alongside their original PDFs;
deleting a sidecar also deletes its retry history. Do not replace a PDF with a
different document under the same filename while retaining its old sidecar.

No `retry` configuration preserves the existing unlimited-retry behavior. Old
sidecars without history start their first *tracked* attempt on the next read;
past attempts cannot be inferred. Limits must be positive, and fractional days
are supported. A document already at `needs-attention` remains stopped even if
limits are subsequently raised or removed.

## Human Review

After correcting the vendor mapping in the site's `/studio`, explicitly resume
documents that need attention:

```bash
python -m app.workflow process INBOX/ --config job.yaml --retry-attention
```

This gives those documents a fresh budget while still skipping completed
documents. Ordinary pending documents continue their current budget. The existing
`--all` flag instead forces every PDF to be read and starts fresh budgets for all.

`status` and `process` return exit code `2` when documents need attention, just
as they do for incomplete results. Unescalated read failures still return `1`.
The [outbox command](outbox.md) never exports a `needs-attention` result.

## Remaining Work

Responsible-user notification and scheduled execution are separate follow-up
steps. Until scheduling is installed, run the commands manually and serialize
runs for each inbox. Retry budgets do not themselves provide delivery
acknowledgements or prevent a consumer from importing the same invoice twice;
see the outbox guide's delivery semantics before automating imports.
