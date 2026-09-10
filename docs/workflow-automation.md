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

## Responsible User

Add a single email address to the job configuration:

```yaml
responsible_email: accounts@example.fo
```

Set `LESARIN_SMTP_HOST`, `LESARIN_SMTP_PORT` (default `587`),
`LESARIN_SMTP_USER`, `LESARIN_SMTP_PASSWORD`, `LESARIN_MAIL_FROM`, and
`LESARIN_BASE_URL` in the process environment. The existing mailer uses STARTTLS
by default; set `LESARIN_SMTP_STARTTLS=false` only for an appropriate local relay.
The base URL must point at this site's web app, not the central brain service.

After processing, one email digest lists unnotified `needs-attention` documents,
their attempt counts, escalation reasons, and located/missing field names. It
links to `/studio`, where a staff user can upload the named PDF and correct its
mapping. These local PDFs are not automatically uploaded to the web app, so the
link opens the studio, not a preloaded document. No extracted invoice values or
PDF attachments are sent, but filenames and inbox paths are included; choose an
authorized responsible user.

On SMTP success, each sidecar records `workflow.notification.to` and `sent_at`.
Further runs do not resend that escalation to the same recipient. If sending
fails, the notification remains pending and is retried on the next run without
another invoice read. A changed recipient or explicit retry/new escalation can
produce a new notification. With no SMTP host, the existing mailer logs the
message instead; this is **not** recorded as successful delivery.

The processing summary includes notification `sent`, `pending`, and `failed`
document counts. Notification failures make `process` exit `1`; successful
notifications do not remove `needs-attention`, so the queue still exits `2`.
Without `responsible_email`, sending is disabled and pending notifications alone
do not cause exit `1`.

SMTP acceptance is not a guarantee that a person read the message. A crash after
SMTP acceptance but before saving the receipt can cause a duplicate notification;
this favors notifying twice over silently losing the alert.

## Remaining Work

Scheduled execution is a separate follow-up step. Until scheduling is installed,
run the commands manually and serialize runs for each inbox. Retry budgets do not themselves provide delivery
acknowledgements or prevent a consumer from importing the same invoice twice;
see the outbox guide's delivery semantics before automating imports.
