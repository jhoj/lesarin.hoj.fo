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

## Scheduled Execution

Linux deployments can opt into `deploy/lesarin-workflow@.service` and
`deploy/lesarin-workflow@.timer`. These are application deployment templates;
adding them to the repository does not install or enable anything. They assume
the existing `/opt/lesarin` installation and `lesarin` service account, plus Bash
and `flock` (from util-linux).

Create `/etc/lesarin/workflows/customer.env` with absolute paths:

```ini
LESARIN_INBOX=/var/lib/lesarin/inbox/customer
LESARIN_JOB_CONFIG=/etc/lesarin/workflows/customer.yaml
# Enable only after the consumer's duplicate-handling policy is ready:
# LESARIN_OUTBOX=/var/lib/lesarin/outbox/customer
```

Create `customer.yaml` with the retry limits, responsible email, validation policy,
and any output profile settings shown above. The workflow honors `db` in that
configuration; `--db` overrides it. Otherwise the database and SMTP environment
come from the existing `/etc/lesarin/lesarin.env`. Keep secrets out of version
control and ensure the configuration is readable only by authorized operators
and the service account. Systemd reads the `.env` files as root; Python reads
the YAML as `lesarin`.

Create the inbox and optional outbox under `/var/lib/lesarin`, owned by
`lesarin:lesarin`. The service has a read-only filesystem elsewhere. For example,
after configuring those files and directories, install and enable the timer:

```bash
sudo install -m 644 deploy/lesarin-workflow@.service /etc/systemd/system/
sudo install -m 644 deploy/lesarin-workflow@.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now lesarin-workflow@customer.timer
sudo systemctl start lesarin-workflow@customer.service
sudo journalctl -u lesarin-workflow@customer.service
```

It runs shortly after boot and approximately five minutes after the previous run
finishes, with up to 30 seconds of jitter. Change the interval with a timer
override, not by running a second scheduler on the same inbox. The service allows
one hour per run; adjust `TimeoutStartSec` for large OCR queues. A killed process
may not have persisted its current attempt and requires operator investigation.

The runner `deploy/run-workflow.sh` holds an exclusive, nonblocking inbox lock
through processing, notification, and optional export. The lock file stays in
place between runs; never delete it while a runner might be active. Exit `75`
means another runner owns that inbox. Systemd treats `2` (review pending) and
`75` as expected results, but `1` (read, export, or notification error) remains a
service failure visible in the journal. The timer still schedules subsequent
runs after a failure. Monitor failed units; review notifications do not replace
operational monitoring.

The runner exports completed sidecars even when processing returns `1` or `2`
for other documents. Unexpected exit codes, such as termination by a signal,
stop the run before export. Both commands print their own JSON summary in the
journal. Automatic outbox export is disabled unless `LESARIN_OUTBOX` is set.

For a manual retry that shares the scheduler lock, run from the application
directory, as the service account with the same database/SMTP environment:

```bash
LESARIN_INBOX=/var/lib/lesarin/inbox/customer \
LESARIN_JOB_CONFIG=/etc/lesarin/workflows/customer.yaml \
bash deploy/run-workflow.sh --retry-attention
```

`LESARIN_PYTHON` can override the default `/opt/lesarin/.venv/bin/python` for local
testing. Direct `python -m app.workflow` calls do not acquire the runner's lock;
do not run them concurrently with a scheduled job on the same inbox. Deliver new
PDFs by writing elsewhere and atomically renaming them into the inbox, so a
scheduled read never catches a partly copied file.

## Delivery Boundary

Retry budgets, notifications, and scheduling do not provide import
acknowledgements or prevent a consumer from importing the same invoice twice.
Keep automatic outbox export disabled until the consumer deduplicates imports
or archives the original PDF and sidecar after confirmed import. See the
[outbox delivery semantics](outbox.md#delivery-semantics). Scheduled central sync
is not included; the runner only uses the site's existing local knowledge.
