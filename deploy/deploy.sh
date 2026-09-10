#!/usr/bin/env bash
# Server-side deploy step, run by the self-hosted GitHub Actions runner after
# the new code + built frontend have been synced into place. Idempotent: it
# refreshes the virtualenv, installs deps, and restarts the service. The schema
# is created/migrated automatically by init_db() at app startup, so there is no
# separate migration step.
set -euo pipefail

APP_DIR="${DEPLOY_PATH:-/opt/lesarin}"
cd "$APP_DIR"

# Snapshot the database before anything else touches the machine. A release is
# exactly when a schema change runs, so this is the copy most likely to matter.
# Never block a deploy on it, but say so loudly if it didn't work.
echo "==> Backing up the database first"
sudo /bin/bash "$APP_DIR/deploy/backup.sh" || echo "WARNING: pre-deploy backup failed — continuing" >&2

# Virtualenv (created once, reused after).
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
./.venv/bin/pip install --upgrade pip >/dev/null
./.venv/bin/pip install -r requirements.txt

# Restart via systemd. The deploy user is granted exactly this one sudo command
# (see deploy/setup-server.sh), so no password is needed here.
sudo systemctl restart lesarin

# Give it a moment, then fail loudly if it didn't come up — this makes the
# GitHub Actions run go red instead of silently leaving a dead service.
sleep 2
sudo systemctl is-active --quiet lesarin && echo "lesarin is running." || {
  echo "lesarin failed to start:" >&2
  sudo systemctl status lesarin --no-pager -l | tail -30 >&2
  exit 1
}
