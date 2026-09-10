#!/usr/bin/env bash
# Back up the Lesarin database.
#
# The database is the only irreplaceable thing on this server. The accounts
# could be recreated, but the vendor templates are accumulated knowledge — the
# product's whole value — and nothing outside this file can rebuild them.
#
# Run by the systemd timer nightly, and by deploy.sh before every release, so
# there is always a copy taken immediately before the risky moment.
#
#   bash backup.sh                 # write a dated dump into BACKUP_DIR
#   BACKUP_DIR=/mnt/x bash backup.sh
#
# Reads the same environment the app does (/etc/lesarin/lesarin.env), so it
# backs up whichever database is actually in use.
set -euo pipefail

ENV_FILE="${ENV_FILE:-/etc/lesarin/lesarin.env}"
if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  set -a && . "$ENV_FILE" && set +a
fi

BACKUP_DIR="${BACKUP_DIR:-/var/backups/lesarin}"
KEEP_DAYS="${KEEP_DAYS:-30}"
LESARIN_DB="${LESARIN_DB:-/var/lib/lesarin/lesarin.db}"
stamp="$(date +%Y-%m-%dT%H-%M-%S)"

mkdir -p "$BACKUP_DIR"

if [ -n "${LESARIN_DATABASE_URL:-}" ]; then
  # Postgres. pg_dump reads the URL directly; keep the custom format so
  # pg_restore can be selective.
  target="$BACKUP_DIR/lesarin-$stamp.dump"
  echo "==> pg_dump -> $target"
  pg_dump --format=custom --no-owner --dbname="$LESARIN_DATABASE_URL" --file="$target"
else
  # A missing source is the classic silent failure: sqlite3 would happily
  # create an empty database and "back up" that, every night, until the day
  # someone needs it. Refuse instead.
  if [ ! -f "$LESARIN_DB" ]; then
    echo "FATAL: no database at $LESARIN_DB (check LESARIN_DB in $ENV_FILE)" >&2
    exit 1
  fi

  # SQLite. Use the .backup command, not cp: it takes a consistent snapshot
  # even while the service is mid-write, which a plain copy does not.
  target="$BACKUP_DIR/lesarin-$stamp.db"
  echo "==> sqlite3 .backup -> $target"
  sqlite3 "$LESARIN_DB" ".backup '$target'"
  gzip --force "$target"
  target="$target.gz"
fi

# A dump that can't be read is not a backup — fail loudly now rather than on
# the day it's needed.
if [ ! -s "$target" ]; then
  echo "FATAL: backup file $target is empty" >&2
  exit 1
fi
case "$target" in
  *.gz) gzip --test "$target" ;;
  *.dump) pg_restore --list "$target" >/dev/null ;;
esac

echo "==> ok: $(du -h "$target" | cut -f1) $target"

# Prune old copies. -mtime +N is "older than N days".
deleted=$(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'lesarin-*' -mtime "+$KEEP_DAYS" -print -delete | wc -l)
[ "$deleted" -gt 0 ] && echo "==> pruned $deleted backup(s) older than $KEEP_DAYS days"

exit 0
