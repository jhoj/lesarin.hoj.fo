#!/usr/bin/env bash
# Run from the application directory. See docs/workflow-automation.md.
set -euo pipefail

: "${LESARIN_INBOX:?Set LESARIN_INBOX to the customer PDF folder}"
: "${LESARIN_JOB_CONFIG:?Set LESARIN_JOB_CONFIG to the job YAML/JSON file}"
python="${LESARIN_PYTHON:-/opt/lesarin/.venv/bin/python}"

# Keep the lock file in place: deleting it would let another process lock a
# different inode while this run still owns the original lock.
exec 9>"$LESARIN_INBOX/.lesarin-workflow.lock"
flock --nonblock --conflict-exit-code 75 9

process_code=0
"$python" -m app.workflow process "$LESARIN_INBOX" --config "$LESARIN_JOB_CONFIG" "$@" || process_code=$?
case "$process_code" in
    0|1|2) ;;
    *) exit "$process_code" ;;
esac

# Export completed documents even when other PDFs failed or need review.
# Opt-in only: consuming importers need deduplication/acknowledgement first.
outbox_code=0
if [[ -n "${LESARIN_OUTBOX:-}" ]]; then
    "$python" -m app.outbox "$LESARIN_INBOX" --outbox "$LESARIN_OUTBOX" || outbox_code=$?
fi
case "$outbox_code" in
    0|1|2) ;;
    *) exit "$outbox_code" ;;
esac

if (( process_code == 1 || outbox_code == 1 )); then
    exit 1
elif (( process_code == 2 || outbox_code == 2 )); then
    exit 2
fi
exit 0
