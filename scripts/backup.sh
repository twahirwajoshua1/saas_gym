#!/usr/bin/env bash
# Makes a backup of the database, for the Linux server the platform runs on.
#
#   scripts/backup.sh                     uses DATABASE_URL from the .env file
#   BACKUP_DIR=/var/backups/gymos scripts/backup.sh
#
# Writes one file per run, named with the date and time, and deletes backups
# older than KEEP_DAYS (30 by default). Run it every night (see
# docs/operations.md) and copy the folder to a second place.
set -euo pipefail

cd "$(dirname "$0")/.."
if [[ -z "${DATABASE_URL:-}" && -f .env ]]; then
  DATABASE_URL="$(grep -E '^DATABASE_URL=' .env | cut -d= -f2-)"
fi
: "${DATABASE_URL:?DATABASE_URL is not set}"

BACKUP_DIR="${BACKUP_DIR:-backups}"
KEEP_DAYS="${KEEP_DAYS:-30}"
mkdir -p "$BACKUP_DIR"
file="$BACKUP_DIR/gymos-$(date +%Y-%m-%d-%H%M%S).dump"

# Custom format: compressed, and restores tables, rules and functions together.
pg_dump --format=custom --no-password --file="$file.partial" "$DATABASE_URL"
mv "$file.partial" "$file"

# A backup that cannot be read is worse than none: check it lists its contents.
pg_restore --list "$file" > /dev/null

find "$BACKUP_DIR" -name 'gymos-*.dump' -mtime +"$KEEP_DAYS" -delete
echo "Backup written: $file ($(du -h "$file" | cut -f1))"
